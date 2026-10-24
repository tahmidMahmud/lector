"""Pull the Kavita library into the local Book table and cache covers.

One Book row per Kavita chapter (= one EPUB file). Re-running a sync is idempotent:
rows are matched on ``kavita_chapter_id``. Books that disappear from Kavita are flagged
``missing`` (never deleted, so generated audio and listening progress survive), but only
when their library/series was listed successfully - a Kavita hiccup never hides books.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import Book, SyncLog, session_scope, utcnow
from .epub_text import read_epub_metadata
from .kavita import FORMAT_NAMES, KavitaClient, KavitaError, file_entries

log = logging.getLogger("lector.sync")

_lock = threading.Lock()
_client: KavitaClient | None = None


def kavita() -> KavitaClient:
    global _client
    if _client is None:
        _client = KavitaClient(settings.kavita_url, settings.kavita_api_key, settings.kavita_username,
                               settings.kavita_password)
    return _client


def reset_client() -> None:
    """Drop the cached client (call after the Kavita settings change)."""
    global _client
    old, _client = _client, None
    if old is not None:
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass


def cover_path(book_id: int) -> Path:
    return settings.cache_dir / "covers" / f"{book_id}.jpg"


def download_path(chapter_id: int, file_path: str) -> Path:
    return settings.cache_dir / "books" / f"{chapter_id}{Path(file_path).suffix.lower() or '.epub'}"


def _readable(p: str | Path) -> bool:
    try:
        return os.path.isfile(p) and os.access(p, os.R_OK)
    except OSError:
        return False


def _local_path_for(file_path: str | None, kv: KavitaClient, chapter_id: int,
                    expected_bytes: int | None = None) -> tuple[str | None, bool]:
    """Map Kavita's path to ours; if it isn't readable here, use/download a private copy.

    Returns ``(path, missing)``.
    """
    if not file_path:
        return None, True
    mapped = settings.map_kavita_path(file_path)
    if _readable(mapped):
        return mapped, False
    # Fallback: ask Kavita for the file (needs the Download role on the Kavita user).
    dest = download_path(chapter_id, file_path)
    if _readable(dest) and (not expected_bytes or dest.stat().st_size == expected_bytes):
        return str(dest), False
    try:
        kv.download_chapter(chapter_id, dest)
        log.info("downloaded %s from Kavita (path %s not mounted)", dest.name, mapped)
        return str(dest), False
    except KavitaError as e:
        log.warning("cannot access %s (mapped %s) and download failed: %s", file_path, mapped, e)
        if _readable(dest):  # keep using an older copy rather than losing the book
            return str(dest), False
        return mapped, True


def _write_cover(book_id: int, data: bytes) -> None:
    cp = cover_path(book_id)
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(cp)


def _authors_from(meta: dict) -> str:
    names = [w.get("name", "").strip() for w in (meta.get("writers") or []) if isinstance(w, dict)]
    return ", ".join(dict.fromkeys(n for n in names if n))


def _sync_series(kv: KavitaClient, lib: dict, series: dict, force_covers: bool, stats: dict) -> list[int]:
    """Upsert the Book rows of one series. Returns the chapter ids seen."""
    sid = series["id"]
    name = (series.get("name") or series.get("originalName") or f"Series {sid}").strip()
    # Kavita derives multi-volume series names from the EPUB title, which can leave a dangling
    # separator ("The Works of Edgar Allan Poe —"); drop it so titles don't read "... — — Volume 1".
    name = name.rstrip(" \u2014\u2013-:|,").strip() or name
    volumes = kv.volumes(sid)
    entries = [e for e in file_entries(volumes, name) if e.get("file_path") and e.get("chapter_id") is not None]
    if not entries:
        return []
    try:
        meta = kv.series_metadata(sid)
    except KavitaError as e:
        log.warning("metadata for series %s failed: %s", sid, e)
        meta = {}
    authors = _authors_from(meta)
    multi = len(entries) > 1
    seen: list[int] = []
    for n, e in enumerate(entries, 1):
        seen.append(e["chapter_id"])
        vol_label = (e.get("label") or f"Volume {n}") if multi else None
        title = f"{name} — {vol_label}" if vol_label else name
        fmt = FORMAT_NAMES.get(e.get("format"), str(e.get("format")))
        with session_scope() as s:
            book = s.scalar(select(Book).where(Book.kavita_chapter_id == e["chapter_id"]))
            if book is None:
                book = Book(kavita_chapter_id=e["chapter_id"])
                s.add(book)
                stats["new"] += 1
            book.kavita_series_id = sid
            book.kavita_library_id = lib["id"]
            book.kavita_volume_id = e.get("volume_id")
            book.series_title = name
            book.volume_label = vol_label
            vnum = e.get("volume_number")
            book.volume_number = float(vnum) if vnum is not None else float(n)
            book.title = title
            book.sort_title = series.get("sortName") or name
            book.file_path = e["file_path"]
            book.file_format = fmt
            book.word_count = e.get("word_count") or (series.get("wordCount") if not multi else None) \
                or book.word_count
            book.kavita_updated = series.get("lastChapterAdded")
            book.last_synced = utcnow()
            book.local_path, book.missing = _local_path_for(e["file_path"], kv, e["chapter_id"], e.get("bytes"))
            summary = (meta.get("summary") or "").strip()
            book_authors = authors
            # Kavita leaves writers/summary empty when library metadata is off: read the OPF instead.
            if (not book_authors or not summary) and fmt == "epub" and book.local_path and not book.missing \
                    and (not book.authors or not book.summary):
                try:
                    opf = read_epub_metadata(book.local_path)
                    book_authors = book_authors or ", ".join(opf.get("authors") or [])
                    summary = summary or (opf.get("description") or "")
                except Exception as ex:  # noqa: BLE001 - a broken file must not stop the sync
                    log.warning("cannot read EPUB metadata of %s: %s", book.local_path, ex)
            book.authors = book_authors or book.authors
            book.summary = summary or book.summary
            # Project Gutenberg detection (OPF identifier/source); only read the OPF once per book.
            if fmt == "epub" and book.local_path and not book.missing and not book.is_gutenberg \
                    and book.gutenberg_id is None:
                try:
                    opf = read_epub_metadata(book.local_path)
                    book.is_gutenberg = bool(opf.get("is_gutenberg"))
                    book.gutenberg_id = opf.get("gutenberg_id") or ("" if not book.is_gutenberg else None)
                except Exception as ex:  # noqa: BLE001
                    log.warning("cannot read EPUB metadata of %s: %s", book.local_path, ex)
            s.flush()
            book_id = book.id
            need_cover = force_covers or not book.cover_cached or not cover_path(book_id).exists()
        if need_cover:
            # Each volume of a multi-file series gets its own cover; else the series cover.
            data = (kv.chapter_cover_bytes(e["chapter_id"]) if multi else None) or kv.cover_bytes(sid)
            if data:
                _write_cover(book_id, data)
                with session_scope() as s:
                    b = s.get(Book, book_id)
                    if b is not None:
                        b.cover_cached = True
                stats["covers"] += 1
    return seen


def run_sync(force_covers: bool = False) -> dict:
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "message": "a sync is already running"}
    started = time.time()
    log_id: int | None = None
    stats = {"new": 0, "covers": 0}
    errors: list[str] = []
    try:
        kv = kavita()
        with session_scope() as s:
            logrow = SyncLog()
            s.add(logrow)
            s.flush()
            log_id = logrow.id
        if not kv.configured:
            raise KavitaError("Kavita is not configured (set KAVITA_API_KEY or KAVITA_USERNAME/KAVITA_PASSWORD)")
        libraries = [l for l in kv.libraries() if l.get("type") in settings.kavita_library_types]
        seen_chapter_ids: set[int] = set()
        failed_libraries: set[int] = set()
        failed_series: set[int] = set()
        n_series = 0
        for lib in libraries:
            try:
                series_list = kv.series_in_library(lib["id"])
            except KavitaError as e:
                failed_libraries.add(lib["id"])
                errors.append(f"library {lib.get('name', lib['id'])}: {e}")
                log.warning("listing library %s failed: %s", lib["id"], e)
                continue
            for series in series_list:
                n_series += 1
                try:
                    seen_chapter_ids.update(_sync_series(kv, lib, series, force_covers, stats))
                except Exception as e:  # noqa: BLE001 - one bad series must not stop the others
                    failed_series.add(series.get("id"))
                    errors.append(f"series {series.get('name', series.get('id'))}: {e}")
                    log.warning("sync of series %s failed: %s", series.get("id"), e, exc_info=True)
        # Books that vanished from Kavita are flagged, not deleted (keeps generated audio + progress).
        newly_missing = 0
        missing_total = 0
        with session_scope() as s:
            for b in s.scalars(select(Book)):
                if b.kavita_chapter_id not in seen_chapter_ids:
                    if b.kavita_library_id in failed_libraries or b.kavita_series_id in failed_series:
                        continue  # unknown state: leave it alone
                    if not b.missing:
                        newly_missing += 1
                    b.missing = True
                if b.missing:
                    missing_total += 1
            lr = s.get(SyncLog, log_id)
            lr.finished_at = utcnow()
            lr.status = "ok"
            lr.books_seen = len(seen_chapter_ids)
            msg = (f"{len(seen_chapter_ids)} book file(s) in {n_series} series across "
                   f"{len(libraries)} library(ies); {stats['new']} new, {missing_total} missing")
            if errors:
                msg += f"; {len(errors)} error(s): " + " | ".join(errors[:5])
            lr.message = msg[:2000]
        return {"status": "ok", "books": len(seen_chapter_ids), "series": n_series, "libraries": len(libraries),
                "new": stats["new"], "covers": stats["covers"], "missing": missing_total,
                "newly_missing": newly_missing, "errors": errors, "seconds": round(time.time() - started, 1)}
    except Exception as e:  # noqa: BLE001
        log.exception("sync failed")
        try:
            with session_scope() as s:
                lr = s.get(SyncLog, log_id) if log_id is not None else None
                if lr is not None:
                    lr.status = "error"
                    lr.finished_at = utcnow()
                    lr.message = str(e)[:1000]
        except Exception:  # noqa: BLE001
            log.exception("could not record sync failure")
        return {"status": "error", "message": str(e)}
    finally:
        _lock.release()


class SyncScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if settings.sync_interval_minutes <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lector-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # First sync shortly after boot, then periodically.
        if self._stop.wait(5):
            return
        while not self._stop.is_set():
            try:
                res = run_sync()
                log.info("sync: %s", res)
            except Exception:  # noqa: BLE001
                log.exception("scheduled sync crashed")
            if self._stop.wait(settings.sync_interval_minutes * 60):
                return


scheduler = SyncScheduler()
