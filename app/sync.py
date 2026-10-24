"""Pull the Kavita library into the local Book table and cache covers."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import Book, SyncLog, session_scope, utcnow
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
    global _client
    _client = None


def cover_path(book_id: int) -> Path:
    return settings.cache_dir / "covers" / f"{book_id}.jpg"


def _local_path_for(file_path: str | None, kv: KavitaClient, chapter_id: int) -> tuple[str | None, bool]:
    """Map Kavita's path to ours; if it doesn't exist, download a private copy. Returns (path, missing)."""
    if not file_path:
        return None, True
    mapped = settings.map_kavita_path(file_path)
    if Path(mapped).exists():
        return mapped, False
    # Fallback: ask Kavita for the file (needs the Download role on the Kavita user).
    dest = settings.cache_dir / "books" / f"{chapter_id}{Path(file_path).suffix.lower()}"
    if dest.exists():
        return str(dest), False
    try:
        kv.download_chapter(chapter_id, dest)
        log.info("downloaded %s from Kavita (path %s not mounted)", dest.name, mapped)
        return str(dest), False
    except KavitaError as e:
        log.warning("cannot access %s (mapped %s) and download failed: %s", file_path, mapped, e)
        return mapped, True


def run_sync(force_covers: bool = False) -> dict:
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "message": "a sync is already running"}
    started = time.time()
    seen = 0
    try:
        kv = kavita()
        if not kv.configured:
            raise KavitaError("Kavita is not configured")
        with session_scope() as s:
            logrow = SyncLog()
            s.add(logrow)
            s.flush()
            log_id = logrow.id
        libraries = [l for l in kv.libraries() if l.get("type") in settings.kavita_library_types]
        seen_chapter_ids: set[int] = set()
        for lib in libraries:
            for series in kv.series_in_library(lib["id"]):
                sid = series["id"]
                try:
                    volumes = kv.volumes(sid)
                except KavitaError as e:
                    log.warning("volumes for series %s failed: %s", sid, e)
                    continue
                entries = [e for e in file_entries(volumes) if e.get("file_path")]
                if not entries:
                    continue
                try:
                    meta = kv.series_metadata(sid)
                except KavitaError:
                    meta = {}
                authors = ", ".join(w.get("name", "") for w in (meta.get("writers") or []) if w.get("name"))
                multi = len(entries) > 1
                for n, e in enumerate(entries, 1):
                    seen += 1
                    seen_chapter_ids.add(e["chapter_id"])
                    vol_label = None
                    if multi:
                        t = (e.get("title") or "").strip()
                        vol_label = t if t and t.lower() != series["name"].lower() else f"Volume {n}"
                    title = f"{series['name']} — {vol_label}" if vol_label else series["name"]
                    with session_scope() as s:
                        book = s.scalar(select(Book).where(Book.kavita_chapter_id == e["chapter_id"]))
                        if book is None:
                            book = Book(kavita_chapter_id=e["chapter_id"])
                            s.add(book)
                        book.kavita_series_id = sid
                        book.kavita_library_id = lib["id"]
                        book.kavita_volume_id = e.get("volume_id")
                        book.series_title = series["name"]
                        book.volume_label = vol_label
                        book.volume_number = float(n)
                        book.title = title
                        book.sort_title = series.get("sortName") or series["name"]
                        book.authors = authors or book.authors
                        book.summary = meta.get("summary") or book.summary
                        book.file_path = e["file_path"]
                        book.file_format = FORMAT_NAMES.get(e.get("format"), str(e.get("format")))
                        book.word_count = series.get("wordCount") if not multi else book.word_count
                        book.kavita_updated = series.get("lastChapterAdded")
                        book.last_synced = utcnow()
                        book.local_path, book.missing = _local_path_for(e["file_path"], kv, e["chapter_id"])
                        s.flush()
                        book_id, need_cover = book.id, (force_covers or not book.cover_cached)
                    if need_cover:
                        data = kv.cover_bytes(sid)
                        if data:
                            cp = cover_path(book_id)
                            cp.parent.mkdir(parents=True, exist_ok=True)
                            cp.write_bytes(data)
                            with session_scope() as s:
                                b = s.get(Book, book_id)
                                b.cover_cached = True
        # Books that vanished from Kavita are flagged, not deleted (keeps generated audio + progress).
        with session_scope() as s:
            for b in s.scalars(select(Book)):
                if b.kavita_chapter_id not in seen_chapter_ids:
                    b.missing = True
            lr = s.get(SyncLog, log_id)
            lr.finished_at = utcnow()
            lr.status = "ok"
            lr.books_seen = seen
            lr.message = f"{seen} book file(s) across {len(libraries)} library(ies)"
        return {"status": "ok", "books": seen, "seconds": round(time.time() - started, 1)}
    except Exception as e:  # noqa: BLE001
        log.exception("sync failed")
        with session_scope() as s:
            lr = s.scalars(select(SyncLog).order_by(SyncLog.id.desc())).first()
            if lr is not None and lr.status == "running":
                lr.status = "error"
                lr.finished_at = utcnow()
                lr.message = str(e)[:1000]
        return {"status": "error", "message": str(e)}
    finally:
        _lock.release()


class SyncScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if settings.sync_interval_minutes <= 0:
            return
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
