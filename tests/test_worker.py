"""Control-flow tests for the generation worker, using a fake (instant) TTS engine."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # no pytest config in the repo yet
os.environ.setdefault("LECTOR_DATA_DIR", tempfile.mkdtemp(prefix="lector-test-"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.worker as W  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Book, Chapter, Rendition, session_scope  # noqa: E402
from app.epub_text import Section  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")

SR = 24000
CHAPTERS = [
    ("Chapter One", ["The morning was grey and the river ran high under the old stone bridge by the mill.",
                     "Martha walked along the towpath, counting the barges that drifted past the willows.",
                     "By noon she had counted eleven, and the twelfth was the one she had been waiting for."]),
    ("Chapter Two", ["The barge was painted green and gold, and a small dog stood at its bow barking at gulls.",
                     "Its captain was a tall man with a grey beard who waved his hat at her as he passed.",
                     "She waved back and then ran to the lock gates, where the keeper was already waiting."]),
    ("Chapter Three", ["That evening the three of them ate supper on the deck while the sun went down.",
                       "Nobody spoke of the letter, though all of them were thinking of nothing else.",
                       "When the stars came out the captain finally opened it and read it aloud to them."]),
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def make_epub(path: Path, chapters=CHAPTERS, title="The Barge", author="A. Writer") -> Path:
    opf_items, spine, nav_li = [], [], []
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        for i, (ctitle, paras) in enumerate(chapters, 1):
            body = f"<h1>{ctitle}</h1>" + "".join(f"<p>{p}</p>" for p in paras)
            z.writestr(f"OEBPS/ch{i}.xhtml",
                       '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                       f"<head><title>{ctitle}</title></head><body>{body}</body></html>")
            opf_items.append(f'<item id="ch{i}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="ch{i}"/>')
            nav_li.append(f'<li><a href="ch{i}.xhtml">{ctitle}</a></li>')
        z.writestr("OEBPS/nav.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
                   f'<nav epub:type="toc"><ol>{"".join(nav_li)}</ol></nav></body></html>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="id">urn:test:1</dc:identifier><dc:title>{title}</dc:title>'
                   f'<dc:creator>{author}</dc:creator><dc:language>en</dc:language></metadata><manifest>'
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                   f'{"".join(opf_items)}</manifest><spine>{"".join(spine)}</spine></package>')
    return path


class FakeEngine:
    name = "fake"
    sample_rate = SR

    def __init__(self, hook=None, ready=(True, "ok")):
        self.hook = hook
        self._ready = ready
        self.calls = 0
        self.texts: list[str] = []

    def ready(self):
        return self._ready

    def warmup(self):
        pass

    def has_voice(self, voice):
        return None

    def voices(self):
        return []

    def synthesize(self, text, voice, speed=1.0, lang="en-us"):
        self.calls += 1
        if self.hook is not None:
            self.hook(self, text)
        self.texts.append(text)
        n = int(0.05 * SR)
        return (0.5 * np.sin(2 * np.pi * 440 * np.arange(n) / SR)).astype(np.float32)


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "audio_dir", data / "audio")
    monkeypatch.setattr(settings, "cache_dir", data / "cache")
    monkeypatch.setattr(settings, "db_path", data / "lector.db")
    monkeypatch.setattr(settings, "audio_format", "mp3")
    monkeypatch.setattr(settings, "audio_bitrate", "64k")
    # Restore whatever database other tests were using afterwards.
    monkeypatch.setattr(db, "_engine", db._engine)
    monkeypatch.setattr(db, "SessionLocal", db.SessionLocal)
    db.init_db()
    epub = make_epub(tmp_path / "barge.epub")
    return {"tmp": tmp_path, "epub": epub}


def use_engine(monkeypatch, engine):
    monkeypatch.setattr(W, "get_engine", lambda: engine)
    return engine


def add_book(local_path, status="queued", voice="af_heart") -> int:
    with session_scope() as s:
        b = Book(kavita_series_id=1, kavita_library_id=1, kavita_chapter_id=1, series_title="The Barge",
                 title="The Barge", authors="A. Writer", local_path=str(local_path), file_format="epub")
        s.add(b)
        s.flush()
        r = Rendition(book_id=b.id, voice=voice, speed=1.0, engine="fake", status=status)
        s.add(r)
        s.flush()
        return r.id


def claim(w: W.Worker, rid: int) -> None:
    assert w._next_job() == rid


def set_status(rid: int, status: str) -> None:
    with session_scope() as s:
        s.get(Rendition, rid).status = status


def snapshot(rid: int):
    with session_scope() as s:
        r = s.get(Rendition, rid)
        return ({"status": r.status, "error": r.error, "progress": r.progress, "done": r.done_chapters,
                 "total": r.total_chapters, "duration": r.total_duration, "finished_at": r.finished_at,
                 "book_id": r.book_id, "m4b": r.m4b_path},
                [{"index": c.index, "title": c.title, "status": c.status, "duration": c.duration,
                  "file": c.file_path, "error": c.error} for c in r.chapters])


def ffprobe_duration(p) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "default=nw=1:nk=1", str(p)], capture_output=True, text=True,
                                check=True).stdout)


def chunks_per_chapter() -> int:
    return 1 + len(CHAPTERS[0][1])  # heading + one chunk per (short) paragraph


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_process_narrates_all_chapters(env, monkeypatch):
    eng = use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done" and r["error"] is None
    assert r["progress"] == 1.0 and r["done"] == r["total"] == 3
    assert r["finished_at"] is not None
    assert [c["title"] for c in chs] == ["Chapter One", "Chapter Two", "Chapter Three"]
    assert all(c["status"] == "done" for c in chs)
    assert eng.calls == 3 * chunks_per_chapter()
    assert r["duration"] == pytest.approx(sum(c["duration"] for c in chs))
    out = W.rendition_dir(r["book_id"], rid)
    for c in chs:
        f = Path(c["file"])
        assert f.exists() and f.parent == out and f.suffix == ".mp3"
        assert ffprobe_duration(f) == pytest.approx(c["duration"], abs=0.08)
        tl = json.loads((out / f"{c['index']:03d}.json").read_text())
        assert tl[0]["heading"] is True and tl[0]["text"] == c["title"] + "."
        assert all(not e["heading"] for e in tl[1:])
        assert all(e["start"] < e["end"] for e in tl)
        assert all(a["end"] <= b["start"] for a, b in zip(tl, tl[1:]))
        assert tl[-1]["end"] <= c["duration"]
    # no temp files left behind
    assert not [p for p in out.iterdir() if "part" in p.name]


@needs_ffmpeg
def test_run_job_via_queue_and_live_status_reset(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["epub"])
    w = W.Worker()
    assert w._next_job() == rid
    w.run_job(rid)
    assert snapshot(rid)[0]["status"] == "done"
    assert w.status.rendition_id is None
    assert w._next_job() is None


# ---------------------------------------------------------------------------
# Pause / resume / stop
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_pause_mid_chapter_leaves_chapter_pending(env, monkeypatch):
    monkeypatch.setattr(W, "CONTROL_EVERY", 1)
    rid = add_book(env["epub"])

    def hook(eng, text):
        if eng.calls == 2:
            set_status(rid, "paused")

    use_engine(monkeypatch, FakeEngine(hook))
    w = W.Worker()
    claim(w, rid)
    with pytest.raises(W.CancelledError):
        w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "paused"
    assert [c["status"] for c in chs] == ["pending", "pending", "pending"]
    out = W.rendition_dir(r["book_id"], rid)
    assert not list(out.glob("*.mp3")) and not list(out.glob("*.wav"))  # partial audio cleaned up


@needs_ffmpeg
def test_pause_then_resume_skips_done_chapters(env, monkeypatch):
    monkeypatch.setattr(W, "CONTROL_EVERY", 1)
    rid = add_book(env["epub"])
    per = chunks_per_chapter()

    def hook(eng, text):
        if eng.calls == per + 1:  # first chunk of chapter two
            set_status(rid, "paused")

    use_engine(monkeypatch, FakeEngine(hook))
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)  # CancelledError is swallowed by run_job
    r, chs = snapshot(rid)
    assert r["status"] == "paused" and r["error"] is None
    assert [c["status"] for c in chs] == ["done", "pending", "pending"]
    assert r["done"] == 1 and r["progress"] == pytest.approx(1 / 3)
    first_file = Path(chs[0]["file"])
    mtime = first_file.stat().st_mtime_ns

    eng2 = use_engine(monkeypatch, FakeEngine())
    set_status(rid, "queued")
    claim(w, rid)
    w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done" and r["progress"] == 1.0
    assert [c["status"] for c in chs] == ["done"] * 3
    assert eng2.calls == 2 * per
    assert "Chapter One." not in eng2.texts and "Chapter Two." in eng2.texts
    assert first_file.stat().st_mtime_ns == mtime  # chapter one was not regenerated


@needs_ffmpeg
def test_cancel_between_chapters(env, monkeypatch):
    rid = add_book(env["epub"])
    per = chunks_per_chapter()

    def hook(eng, text):
        if eng.calls == per:  # last chunk of chapter one
            set_status(rid, "cancelled")

    use_engine(monkeypatch, FakeEngine(hook))
    w = W.Worker()
    claim(w, rid)
    with pytest.raises(W.CancelledError):
        w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "cancelled"
    assert [c["status"] for c in chs] == ["done", "pending", "pending"]


@needs_ffmpeg
def test_stop_abandons_chapter_and_restart_requeues(env, monkeypatch):
    monkeypatch.setattr(W, "CONTROL_EVERY", 1)
    rid = add_book(env["epub"])
    w = W.Worker()

    def hook(eng, text):
        if eng.calls == 2:
            w._stop.set()

    use_engine(monkeypatch, FakeEngine(hook))
    claim(w, rid)
    w.run_job(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "running"  # process went away mid-job
    assert chs[0]["status"] == "pending"
    assert W.Worker.requeue_interrupted() == 1
    assert snapshot(rid)[0]["status"] == "queued"


def test_requeue_interrupted(env):
    rid = add_book(env["epub"], status="running")
    other = None
    with session_scope() as s:
        s.add(Chapter(rendition_id=rid, index=0, title="One", status="running"))
        s.add(Chapter(rendition_id=rid, index=1, title="Two", status="done", duration=3.0))
        b = Book(kavita_series_id=2, kavita_library_id=1, kavita_chapter_id=2, series_title="X", title="X")
        s.add(b)
        s.flush()
        r2 = Rendition(book_id=b.id, voice="af_heart", engine="fake", status="paused")
        s.add(r2)
        s.flush()
        other = r2.id
    assert W.Worker.requeue_interrupted() == 1
    r, chs = snapshot(rid)
    assert r["status"] == "queued"
    assert [c["status"] for c in chs] == ["pending", "done"]
    assert snapshot(other)[0]["status"] == "paused"


@needs_ffmpeg
def test_worker_thread_end_to_end(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["epub"], status="running")  # left over from a "previous process"
    w = W.Worker()
    done = threading.Event()
    orig = w.run_job

    def run_job(r):
        orig(r)
        done.set()

    w.run_job = run_job
    w.start()
    try:
        assert done.wait(30)
    finally:
        w.stop(timeout=5)
    assert snapshot(rid)[0]["status"] == "done"
    assert w.status.engine_ready is True


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_non_ascii_chunk_falls_back_to_ascii(env, monkeypatch):
    chapters = [CHAPTERS[0], ("Chapter Two", ["He said 日本 was far away, over the sea and past the islands, "
                                              "and that nobody from the village had ever been there."]),
                CHAPTERS[2]]
    epub = make_epub(env["tmp"] / "unicode.epub", chapters=chapters)

    def hook(eng, text):
        if any(ord(ch) > 127 for ch in text):
            raise ValueError("phonemizer exploded")

    eng = use_engine(monkeypatch, FakeEngine(hook))
    rid = add_book(epub)
    w = W.Worker()
    claim(w, rid)
    w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done"
    assert all(c["status"] == "done" for c in chs)
    assert any("was far away" in t and "日本" not in t for t in eng.texts)


@needs_ffmpeg
def test_single_unsynthesisable_chunk_is_silenced_not_fatal(env, monkeypatch):
    def hook(eng, text):
        if "towpath" in text:
            raise RuntimeError("model hiccup")

    use_engine(monkeypatch, FakeEngine(hook))
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done"
    assert all(c["status"] == "done" for c in chs)


@needs_ffmpeg
def test_hard_engine_failure_marks_chapters_and_rendition_error(env, monkeypatch):
    def hook(eng, text):
        raise RuntimeError("onnx session is dead")

    use_engine(monkeypatch, FakeEngine(hook))
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)
    r, chs = snapshot(rid)
    assert [c["status"] for c in chs] == ["error"] * 3
    assert all("TTS failed" in c["error"] for c in chs)
    assert r["status"] == "error"
    assert "3 chapter(s) failed" in r["error"] and "Chapter One" in r["error"]
    assert r["finished_at"] is not None


@needs_ffmpeg
def test_one_bad_chapter_others_still_done(env, monkeypatch):
    def hook(eng, text):
        if "Chapter Two" in text or "barge was painted" in text or "Its captain" in text:
            raise RuntimeError("boom")

    use_engine(monkeypatch, FakeEngine(hook))
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)
    r, chs = snapshot(rid)
    assert [c["status"] for c in chs] == ["done", "error", "done"]
    assert r["status"] == "error" and "Chapter Two" in r["error"]
    # Re-queueing retries only the failed chapter.
    eng = use_engine(monkeypatch, FakeEngine())
    set_status(rid, "queued")
    claim(w, rid)
    w.run_job(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done" and r["error"] is None
    assert eng.calls == chunks_per_chapter()


def test_engine_not_ready_fails_job(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine(ready=(False, "model files missing")))
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)
    r, _ = snapshot(rid)
    assert r["status"] == "error" and "model files missing" in r["error"]


def test_missing_book_file(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["tmp"] / "nope.epub")
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)
    r, _ = snapshot(rid)
    assert r["status"] == "error" and "Book file not found" in r["error"]


def test_non_epub_rejected(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine())
    pdf = env["tmp"] / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    rid = add_book(pdf)
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)
    assert "Only EPUB" in snapshot(rid)[0]["error"]


# ---------------------------------------------------------------------------
# Section edge cases
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_heading_only_section_is_narrated(env, monkeypatch):
    eng = FakeEngine()
    out = env["tmp"] / "out"
    sec = Section(title="Part One", paragraphs=[], headings=["Part One"])
    w = W.Worker()
    w._check_control = lambda rid: None  # no rendition row needed for this unit test
    dur, tl = w._narrate_section(0, 0, 1, sec, "en", "en-us", eng, "af_heart", 1.0,
                                          out / "000.mp3", "Book", "Author")
    assert eng.calls == 1
    assert tl == [{"start": 0.0, "end": 0.05, "text": "Part One.", "heading": True}]
    assert dur == pytest.approx(0.05 + W.HEADING_GAP)
    assert (out / "000.mp3").exists()


def test_empty_section_raises_nothing_to_narrate(env):
    sec = Section(title="Blank", paragraphs=["   ", "[1]"], headings=[])
    with pytest.raises(W.NothingToNarrate):
        W.Worker()._narrate_section(0, 0, 1, sec, "en", "en-us", FakeEngine(), "af_heart", 1.0,
                                    env["tmp"] / "000.mp3", "Book", "Author")


@needs_ffmpeg
def test_changed_text_is_renarrated_on_resume(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w._process(rid)
    with session_scope() as s:
        c = s.query(Chapter).filter_by(rendition_id=rid, index=1).one()
        c.text_hash = "stale"
    eng = use_engine(monkeypatch, FakeEngine())
    set_status(rid, "queued")
    claim(w, rid)
    w._process(rid)
    assert snapshot(rid)[0]["status"] == "done"
    assert eng.calls == chunks_per_chapter()
    assert "Chapter Two." in eng.texts


# ---------------------------------------------------------------------------
# Encoding / export
# ---------------------------------------------------------------------------
def _tone(seconds: float) -> np.ndarray:
    return (0.3 * np.sin(2 * np.pi * 220 * np.arange(int(seconds * SR)) / SR)).astype(np.float32)


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp3", "m4a"])
def test_encode_audio_duration_and_tags(env, ext):
    dest = env["tmp"] / f"tone.{ext}"
    dur = W.encode_audio(_tone(1.5), SR, dest, title="T; x=1", album="Alb", artist="Art", track=2)
    assert dur == pytest.approx(1.5)
    assert ffprobe_duration(dest) == pytest.approx(1.5, abs=0.06)
    tags = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(dest)],
                                     capture_output=True, text=True).stdout)["format"]["tags"]
    tags = {k.lower(): v for k, v in tags.items()}
    assert tags["title"] == "T; x=1" and tags["album"] == "Alb" and tags["artist"] == "Art"
    assert not list(env["tmp"].glob("*.wav")) and not list(env["tmp"].glob("*.part*"))


def test_encode_audio_without_ffmpeg_is_clear(env, monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(W.shutil, "which", lambda name: None if name == "ffmpeg" else real_which(name))
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        W.encode_audio(_tone(0.1), SR, env["tmp"] / "x.mp3", title="t", album="a", artist="b", track=1)


@needs_ffmpeg
def test_ffmpeg_error_message_includes_stderr(env, monkeypatch):
    monkeypatch.setattr(settings, "audio_bitrate", "notabitrate")
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        W.encode_audio(_tone(0.1), SR, env["tmp"] / "x.mp3", title="t", album="a", artist="b", track=1)


def test_unknown_audio_format_falls_back_to_mp3(monkeypatch):
    monkeypatch.setattr(settings, "audio_format", "ogg")
    assert W.audio_ext() == "mp3"
    monkeypatch.setattr(settings, "audio_format", "M4A")
    assert W.audio_ext() == "m4a"


def _chapters_of(path) -> list[dict]:
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
                                     capture_output=True, text=True, check=True).stdout)["chapters"]


@needs_ffmpeg
@pytest.mark.parametrize("fmt", ["mp3", "m4a"])
def test_full_job_and_m4b(env, monkeypatch, fmt):
    monkeypatch.setattr(settings, "audio_format", fmt)
    use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done"
    assert all(c["file"].endswith("." + fmt) for c in chs)
    m4b = w.build_m4b(rid)
    assert m4b.exists() and m4b.name == "audiobook.m4b"
    assert snapshot(rid)[0]["m4b"] == str(m4b)
    marks = _chapters_of(m4b)
    assert [c["tags"]["title"] for c in marks] == ["Chapter One", "Chapter Two", "Chapter Three"]
    assert float(marks[-1]["end_time"]) == pytest.approx(r["duration"], abs=0.3)
    assert ffprobe_duration(m4b) == pytest.approx(r["duration"], abs=0.3)
    assert not list(m4b.parent.glob("*.part*"))


def test_build_m4b_without_chapters(env):
    rid = add_book(env["epub"])
    with pytest.raises(RuntimeError, match="no finished chapters"):
        W.Worker().build_m4b(rid)


def test_ffmeta_escape():
    assert W._ffmeta_escape("a=b;c#d\\e\nf") == "a\\=b\\;c\\#d\\\\e f"


def test_live_status_realtime_factor():
    st = W.LiveStatus(audio_seconds=30.0, wall_seconds=10.0)
    assert st.as_dict()["realtime_factor"] == 3.0
    assert W.LiveStatus(audio_seconds=1.0, wall_seconds=0.5).as_dict()["realtime_factor"] is None


def test_unknown_voice_fails_fast(env, monkeypatch):
    eng = FakeEngine()
    eng.has_voice = lambda v: v == "af_heart"
    use_engine(monkeypatch, eng)
    rid = add_book(env["epub"], voice="zz_nobody")
    w = W.Worker()
    claim(w, rid)
    w.run_job(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "error" and "Unknown voice 'zz_nobody'" in r["error"]
    assert eng.calls == 0 and chs == []


@needs_ffmpeg
def test_book_restructured_since_last_run_starts_over(env, monkeypatch):
    use_engine(monkeypatch, FakeEngine())
    rid = add_book(env["epub"])
    w = W.Worker()
    claim(w, rid)
    w._process(rid)
    out = W.rendition_dir(snapshot(rid)[0]["book_id"], rid)
    assert (out / "002.mp3").exists()
    make_epub(env["epub"], chapters=CHAPTERS[:2])  # the file on disk changed
    eng = use_engine(monkeypatch, FakeEngine())
    set_status(rid, "queued")
    claim(w, rid)
    w._process(rid)
    r, chs = snapshot(rid)
    assert r["status"] == "done" and r["total"] == 2 and r["done"] == 2
    assert [c["title"] for c in chs] == ["Chapter One", "Chapter Two"]
    assert eng.calls == 2 * chunks_per_chapter()
    assert not (out / "002.mp3").exists() and not (out / "002.json").exists()
    assert r["duration"] == pytest.approx(sum(c["duration"] for c in chs))
