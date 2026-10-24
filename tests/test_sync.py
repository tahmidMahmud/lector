"""Kavita -> Book table sync, with a fake Kavita client returning real 0.9.1 response shapes."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LECTOR_DATA_DIR", tempfile.mkdtemp(prefix="lector-test-"))

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.db as db  # noqa: E402
import app.sync as sync  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Book, SyncLog, session_scope  # noqa: E402
from app.kavita import KavitaError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n fake cover"


def write_epub(path: Path, title: str, author: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>'
                   '<dc:language>en</dc:language><dc:description>A &lt;b&gt;fine&lt;/b&gt; book.</dc:description>'
                   '</metadata><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("OEBPS/c1.xhtml", "<html><body><p>Hello.</p></body></html>")
    return path


def vol(vid, name, number, chapters):
    return {"id": vid, "name": name, "number": number, "minNumber": number, "maxNumber": number,
            "chapters": chapters}


def chap(cid, file_path, title="-100000", title_name="", is_special=False, size=None):
    return {"id": cid, "title": title, "titleName": title_name, "range": title, "minNumber": -100000,
            "number": "-100000", "isSpecial": is_special, "sortOrder": -100000, "wordCount": 1000 + cid,
            "files": [{"filePath": file_path, "format": 3, "pages": 12, "bytes": size}]}


class FakeKavita:
    """Stands in for KavitaClient; data shapes copied from a real Kavita 0.9.1 server."""

    configured = True

    def __init__(self, book_root: str = "/books"):
        r = book_root
        self.libs = [{"id": 1, "name": "Books", "type": 2}, {"id": 9, "name": "Manga", "type": 0}]
        self.series = {1: [
            {"id": 1, "name": "The Works of Edgar Allan Poe", "sortName": "Works of Edgar Allan Poe",
             "wordCount": 175000, "lastChapterAdded": "2026-10-24T15:39:52"},
            {"id": 5, "name": "A Tale of Two Cities", "sortName": "Tale of Two Cities", "wordCount": 120269,
             "lastChapterAdded": "2026-10-24T15:50:27"},
            {"id": 2, "name": "The Gift of the Magi", "sortName": "Gift of the Magi", "wordCount": 4798},
        ], 9: [{"id": 99, "name": "Some Manga"}]}
        self.vols = {
            1: [vol(2, "2", 2, [chap(2, f"{r}/Edgar Allan Poe/The Works of Edgar Allan Poe - Volume 2.epub")]),
                vol(1, "1", 1, [chap(1, f"{r}/Edgar Allan Poe/The Works of Edgar Allan Poe - Volume 1.epub")])],
            5: [vol(6, "-100000", -100000, [chap(6, f"{r}/Charles Dickens/A Tale of Two Cities.epub",
                                                  title="A Tale of Two Cities", title_name="A Tale of Two Cities",
                                                  is_special=True)])],
            2: [vol(3, "-100000", -100000, [chap(3, f"{r}/O. Henry/The Gift of the Magi.epub")])],
        }
        self.meta = {
            1: {"summary": "", "writers": [{"id": 2, "name": "Edgar Allan Poe"}], "language": "en"},
            5: {"summary": "It was the best of times.", "writers": [{"id": 5, "name": "Charles Dickens"}]},
            2: {"summary": "", "writers": [], "language": "", "releaseYear": 0},  # metadata off in Kavita
        }
        self.fail_volumes: set[int] = set()
        self.downloads: list[int] = []
        self.download_ok = True
        self.cover_calls: list[tuple[str, int]] = []

    def libraries(self):
        return self.libs

    def series_in_library(self, library_id, page_size=100):
        return list(self.series.get(library_id, []))

    def volumes(self, series_id):
        if series_id in self.fail_volumes:
            raise KavitaError(f"Kavita GET /api/Series/volumes -> HTTP 500 for {series_id}")
        return self.vols.get(series_id, [])

    def series_metadata(self, series_id):
        return self.meta.get(series_id, {})

    def cover_bytes(self, series_id):
        self.cover_calls.append(("series", series_id))
        return PNG

    def chapter_cover_bytes(self, chapter_id):
        self.cover_calls.append(("chapter", chapter_id))
        return PNG + str(chapter_id).encode()

    def download_chapter(self, chapter_id, dest):
        if not self.download_ok:
            raise KavitaError(f"download chapter {chapter_id}: HTTP 403 (the Kavita user needs the Download role)")
        self.downloads.append(chapter_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_epub(dest, f"Downloaded {chapter_id}", "Downloaded Author")
        return dest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "audio_dir", data / "audio")
    monkeypatch.setattr(settings, "cache_dir", data / "cache")
    monkeypatch.setattr(settings, "db_path", data / "lector.db")
    monkeypatch.setattr(settings, "kavita_library_types", [2])
    lib = tmp_path / "library"
    for rel, title, author in [
        ("Edgar Allan Poe/The Works of Edgar Allan Poe - Volume 1.epub", "Poe 1", "Edgar Allan Poe"),
        ("Edgar Allan Poe/The Works of Edgar Allan Poe - Volume 2.epub", "Poe 2", "Edgar Allan Poe"),
        ("Charles Dickens/A Tale of Two Cities.epub", "A Tale of Two Cities", "Charles Dickens"),
        ("O. Henry/The Gift of the Magi.epub", "The Gift of the Magi", "O. Henry"),
    ]:
        write_epub(lib / rel, title, author)
    monkeypatch.setattr(settings, "kavita_path_map", settings._parse_path_map(f"/books={lib}"))
    monkeypatch.setattr(db, "_engine", db._engine)
    monkeypatch.setattr(db, "SessionLocal", db.SessionLocal)
    db.init_db()
    fake = FakeKavita()
    monkeypatch.setattr(sync, "kavita", lambda: fake)
    return {"fake": fake, "lib": lib, "data": data}


def books() -> list[Book]:
    with session_scope() as s:
        return list(s.scalars(select(Book).order_by(Book.kavita_chapter_id)))


def by_chapter() -> dict[int, Book]:
    return {b.kavita_chapter_id: b for b in books()}


def test_sync_creates_one_book_per_file(env):
    res = sync.run_sync()
    assert res["status"] == "ok", res
    assert res["books"] == 4 and res["series"] == 3 and res["libraries"] == 1 and res["new"] == 4
    assert res["errors"] == []
    b = by_chapter()
    assert sorted(b) == [1, 2, 3, 6]  # the manga library (type 0) is ignored

    poe1, poe2 = b[1], b[2]
    assert (poe1.volume_label, poe2.volume_label) == ("Volume 1", "Volume 2")
    assert poe1.title == "The Works of Edgar Allan Poe — Volume 1"
    assert poe2.title == "The Works of Edgar Allan Poe — Volume 2"
    assert (poe1.volume_number, poe2.volume_number) == (1.0, 2.0)
    assert poe1.series_title == "The Works of Edgar Allan Poe" and poe1.kavita_series_id == 1
    assert poe1.kavita_volume_id == 1 and poe2.kavita_volume_id == 2
    assert poe1.authors == "Edgar Allan Poe"
    assert poe1.word_count == 1001  # per-chapter count, not the series total

    tale = b[6]
    assert tale.title == "A Tale of Two Cities" and tale.volume_label is None
    assert tale.summary == "It was the best of times."
    assert tale.sort_title == "Tale of Two Cities"
    assert tale.kavita_updated == "2026-10-24T15:50:27"

    for book in b.values():
        assert book.missing is False
        assert book.file_format == "epub"
        assert book.local_path and Path(book.local_path).is_file()
        assert book.local_path.startswith(str(env["lib"]))
        assert book.cover_cached is True
        assert sync.cover_path(book.id).read_bytes().startswith(PNG)
    assert "-100000" not in " ".join(x.title + (x.volume_label or "") for x in b.values())

    with session_scope() as s:
        logs = list(s.scalars(select(SyncLog)))
    assert len(logs) == 1 and logs[0].status == "ok" and logs[0].books_seen == 4


def test_empty_kavita_writers_fall_back_to_opf(env):
    sync.run_sync()
    magi = by_chapter()[3]
    assert magi.authors == "O. Henry"          # read from the EPUB's dc:creator
    assert magi.summary == "A fine book."      # dc:description, HTML stripped


def test_multi_volume_series_gets_per_volume_covers(env):
    sync.run_sync()
    b = by_chapter()
    assert sync.cover_path(b[1].id).read_bytes() == PNG + b"1"
    assert sync.cover_path(b[2].id).read_bytes() == PNG + b"2"
    assert ("series", 5) in env["fake"].cover_calls


def test_sync_is_idempotent(env):
    sync.run_sync()
    first = {b.kavita_chapter_id: (b.id, b.title, b.local_path) for b in books()}
    calls = len(env["fake"].cover_calls)
    res = sync.run_sync()
    assert res["status"] == "ok" and res["new"] == 0 and res["covers"] == 0
    second = {b.kavita_chapter_id: (b.id, b.title, b.local_path) for b in books()}
    assert first == second and len(second) == 4
    assert len(env["fake"].cover_calls) == calls  # covers are not refetched


def test_force_covers_refetches(env):
    sync.run_sync()
    res = sync.run_sync(force_covers=True)
    assert res["covers"] == 4


def test_deleted_cover_file_is_refetched(env):
    sync.run_sync()
    b = by_chapter()[6]
    sync.cover_path(b.id).unlink()
    sync.run_sync()
    assert sync.cover_path(b.id).exists()


def test_removed_series_is_flagged_missing_and_restored(env):
    fake = env["fake"]
    sync.run_sync()
    removed = fake.series[1].pop(2)  # The Gift of the Magi disappears from Kavita
    res = sync.run_sync()
    assert res["newly_missing"] == 1 and res["missing"] == 1
    b = by_chapter()
    assert b[3].missing is True
    assert all(not b[c].missing for c in (1, 2, 6))
    assert len(b) == 4  # flagged, never deleted

    fake.series[1].append(removed)
    sync.run_sync()
    assert by_chapter()[3].missing is False


def test_failed_series_is_not_flagged_missing(env):
    fake = env["fake"]
    sync.run_sync()
    fake.fail_volumes.add(1)
    res = sync.run_sync()
    assert res["status"] == "ok"
    assert len(res["errors"]) == 1 and "HTTP 500" in res["errors"][0]
    b = by_chapter()
    assert b[1].missing is False and b[2].missing is False  # unknown state: left alone
    assert b[6].missing is False


def test_failed_library_listing_flags_nothing(env, monkeypatch):
    fake = env["fake"]
    sync.run_sync()

    def broken(library_id, page_size=100):
        raise KavitaError("cannot connect to Kavita")

    monkeypatch.setattr(fake, "series_in_library", broken)
    res = sync.run_sync()
    assert res["status"] == "ok" and res["errors"]
    assert not any(b.missing for b in books())


def test_unreachable_kavita_is_an_error_not_a_crash(env, monkeypatch):
    fake = env["fake"]
    sync.run_sync()

    def down():
        raise KavitaError("cannot connect to Kavita at http://kavita:5000")

    monkeypatch.setattr(fake, "libraries", down)
    res = sync.run_sync()
    assert res["status"] == "error" and "cannot connect" in res["message"]
    assert not any(b.missing for b in books())
    with session_scope() as s:
        last = s.scalars(select(SyncLog).order_by(SyncLog.id.desc())).first()
    assert last.status == "error" and last.finished_at is not None


def test_unmounted_files_are_downloaded(env, monkeypatch):
    monkeypatch.setattr(settings, "kavita_path_map", settings._parse_path_map("/books=/nonexistent"))
    res = sync.run_sync()
    assert res["status"] == "ok"
    assert sorted(env["fake"].downloads) == [1, 2, 3, 6]
    for b in books():
        assert b.missing is False
        assert Path(b.local_path) == settings.cache_dir / "books" / f"{b.kavita_chapter_id}.epub"
        assert Path(b.local_path).is_file()
    # Second sync reuses the downloaded copies.
    sync.run_sync()
    assert sorted(env["fake"].downloads) == [1, 2, 3, 6]


def test_inaccessible_file_without_download_is_missing(env, monkeypatch):
    monkeypatch.setattr(settings, "kavita_path_map", settings._parse_path_map("/books=/nonexistent"))
    env["fake"].download_ok = False
    res = sync.run_sync()
    assert res["status"] == "ok"
    for b in books():
        assert b.missing is True
        assert b.local_path.startswith("/nonexistent/")


def test_concurrent_sync_reports_busy(env):
    with sync._lock:
        assert sync.run_sync()["status"] == "busy"


def test_not_configured(env, monkeypatch):
    monkeypatch.setattr(env["fake"], "configured", False)
    res = sync.run_sync()
    assert res["status"] == "error" and "not configured" in res["message"]


def test_scheduler_start_stop(monkeypatch):
    monkeypatch.setattr(settings, "sync_interval_minutes", 0)
    sch = sync.SyncScheduler()
    sch.start()
    assert sch._thread is None
    monkeypatch.setattr(settings, "sync_interval_minutes", 60)
    ran = threading.Event()
    monkeypatch.setattr(sync, "run_sync", lambda: ran.set() or {"status": "ok"})
    sch = sync.SyncScheduler()
    monkeypatch.setattr(sch._stop, "wait", lambda t: sch._stop.is_set())
    sch.start()
    assert ran.wait(2)
    sch.stop()
    sch._thread.join(2)
    assert not sch._thread.is_alive()
