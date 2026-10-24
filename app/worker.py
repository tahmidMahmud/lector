"""Background generation worker: EPUB -> chapters -> TTS -> encoded audio.

One worker thread runs at a time (TTS saturates the CPU). Jobs are Rendition rows
with status ``queued``; chapters already ``done`` are skipped, so a restart
resumes where it left off. Status changes made through the API (paused /
cancelled) are picked up between chunks.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from sqlalchemy import select

from .config import settings
from .db import Book, Chapter, Rendition, session_scope, utcnow
from .epub_text import BookText, chunk_for_tts, clean_for_tts, extract_book
from .tts import get_engine
from .tts.base import describe_voice

log = logging.getLogger("lector.worker")

PARAGRAPH_GAP = 0.55   # seconds of silence between paragraphs
CHUNK_GAP = 0.22       # between chunks inside a paragraph
HEADING_GAP = 1.0      # after a chapter heading


@dataclass
class LiveStatus:
    rendition_id: int | None = None
    book_title: str = ""
    chapter_index: int | None = None
    chapter_title: str = ""
    chunk: int = 0
    chunks: int = 0
    chapter_started: float = 0.0
    audio_seconds: float = 0.0
    wall_seconds: float = 0.0
    engine_ready: bool | None = None
    engine_message: str = ""
    history: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "rendition_id": self.rendition_id, "book_title": self.book_title,
            "chapter_index": self.chapter_index, "chapter_title": self.chapter_title,
            "chunk": self.chunk, "chunks": self.chunks,
            "realtime_factor": round(self.audio_seconds / self.wall_seconds, 2) if self.wall_seconds > 1 else None,
            "engine_ready": self.engine_ready, "engine_message": self.engine_message,
        }


class CancelledError(Exception):
    pass


def rendition_dir(book_id: int, rendition_id: int) -> Path:
    return settings.audio_dir / str(book_id) / str(rendition_id)


def encode_audio(samples: np.ndarray, sr: int, dest: Path, title: str, album: str, artist: str,
                 track: int) -> float:
    """Encode float32 mono samples to dest (mp3/m4a) with ffmpeg. Returns duration in seconds."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = settings.audio_format
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = Path(tmp.name)
    try:
        sf.write(str(wav), samples, sr, subtype="PCM_16")
        meta = ["-metadata", f"title={title}", "-metadata", f"album={album}", "-metadata", f"artist={artist}",
                "-metadata", f"track={track}"]
        if fmt == "m4a":
            codec = ["-c:a", "aac", "-b:a", settings.audio_bitrate, "-movflags", "+faststart"]
        else:
            codec = ["-c:a", "libmp3lame", "-b:a", settings.audio_bitrate]
        tmp_out = dest.with_suffix(dest.suffix + ".part" + dest.suffix)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", *codec, *meta, str(tmp_out)]
        subprocess.run(cmd, check=True, capture_output=True)
        tmp_out.replace(dest)
    finally:
        wav.unlink(missing_ok=True)
    return len(samples) / sr


class Worker:
    def __init__(self) -> None:
        self.status = LiveStatus()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._m4b_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="lector-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    # -- main loop -----------------------------------------------------------
    def _run(self) -> None:
        ok, msg = get_engine().ready()
        self.status.engine_ready, self.status.engine_message = ok, msg
        if not ok:
            log.error("TTS engine not ready: %s", msg)
        # Anything left "running" from a previous process is resumed.
        with session_scope() as s:
            for r in s.scalars(select(Rendition).where(Rendition.status == "running")):
                r.status = "queued"
        while not self._stop.is_set():
            rid = self._next_job()
            if rid is None:
                self._wake.wait(timeout=15)
                self._wake.clear()
                continue
            try:
                self._process(rid)
            except CancelledError:
                log.info("rendition %s paused/cancelled", rid)
            except Exception as e:  # noqa: BLE001
                log.exception("rendition %s failed", rid)
                with session_scope() as s:
                    r = s.get(Rendition, rid)
                    if r is not None:
                        r.status = "error"
                        r.error = str(e)[:2000]
            finally:
                self.status = LiveStatus(engine_ready=self.status.engine_ready,
                                         engine_message=self.status.engine_message)

    def _next_job(self) -> int | None:
        with session_scope() as s:
            r = s.scalars(select(Rendition).where(Rendition.status == "queued")
                          .order_by(Rendition.created_at)).first()
            if r is None:
                return None
            r.status = "running"
            r.error = None
            return r.id

    def _check_control(self, rid: int) -> None:
        with session_scope() as s:
            r = s.get(Rendition, rid)
            if r is None or r.status in ("paused", "cancelled"):
                raise CancelledError()

    # -- job -----------------------------------------------------------------
    def _process(self, rid: int) -> None:
        with session_scope() as s:
            r = s.get(Rendition, rid)
            if r is None:
                return
            book = s.get(Book, r.book_id)
            voice, speed, book_id = r.voice, r.speed, r.book_id
            book_title, authors, local_path = book.title, book.authors or "", book.local_path
            existing = {c.index: c for c in r.chapters}
            have_chapters = bool(existing)
        self.status.rendition_id, self.status.book_title = rid, book_title

        if not local_path or not Path(local_path).exists():
            raise RuntimeError(f"Book file not found: {local_path!r} (check KAVITA_PATH_MAP / volume mounts)")
        if not local_path.lower().endswith(".epub"):
            raise RuntimeError("Only EPUB books can be narrated right now")

        bt: BookText = extract_book(local_path)
        log.info("extracted %r: %d sections, gutenberg=%s trimmed=%s/%s", bt.title, len(bt.sections),
                 bt.is_gutenberg, bt.trimmed_start, bt.trimmed_end)
        with session_scope() as s:
            r = s.get(Rendition, rid)
            if not have_chapters:
                for i, sec in enumerate(bt.sections):
                    s.add(Chapter(rendition_id=rid, index=i, title=sec.title,
                                  status="skipped" if sec.skipped else "pending",
                                  char_count=sec.char_count, text_hash=sec.text_hash()))
                r.total_chapters = len(bt.sections)
            s.flush()
            chapters = {c.index: (c.id, c.status) for c in r.chapters}

        lang = describe_voice(voice)["lang"]
        engine = get_engine()
        out_dir = rendition_dir(book_id, rid)
        out_dir.mkdir(parents=True, exist_ok=True)
        n_total = len(bt.sections)
        for i, sec in enumerate(bt.sections):
            cid, cstatus = chapters.get(i, (None, None))
            if cid is None or cstatus in ("done", "skipped"):
                continue
            self._check_control(rid)
            with session_scope() as s:
                c = s.get(Chapter, cid)
                c.status = "running"
                c.error = None
            try:
                duration, timeline = self._narrate_section(rid, i, n_total, sec, bt.language, lang, engine,
                                                           voice, speed, out_dir, book_title, authors)
            except CancelledError:
                with session_scope() as s:
                    c = s.get(Chapter, cid)
                    c.status = "pending"
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("chapter %d of rendition %d failed", i, rid)
                with session_scope() as s:
                    c = s.get(Chapter, cid)
                    c.status = "error"
                    c.error = str(e)[:2000]
                continue
            with session_scope() as s:
                c = s.get(Chapter, cid)
                c.status = "done"
                c.duration = duration
                c.file_path = str(out_dir / f"{i:03d}.{settings.audio_format}")
                r = s.get(Rendition, rid)
                done = sum(1 for ch in r.chapters if ch.status == "done")
                narr = sum(1 for ch in r.chapters if ch.status != "skipped")
                r.done_chapters = done
                r.progress = done / max(1, narr)
                r.total_duration = sum(ch.duration for ch in r.chapters if ch.status == "done")
            (out_dir / f"{i:03d}.json").write_text(json.dumps(timeline, ensure_ascii=False))

        with session_scope() as s:
            r = s.get(Rendition, rid)
            if r.status != "running":
                return
            errors = [c for c in r.chapters if c.status == "error"]
            pending = [c for c in r.chapters if c.status in ("pending", "running")]
            if pending:
                r.status = "queued"  # shouldn't happen, but be safe
            elif errors:
                r.status = "error"
                r.error = f"{len(errors)} chapter(s) failed: " + "; ".join(
                    f"{c.title}: {c.error}" for c in errors[:3])
            else:
                r.status = "done"
                r.error = None
            r.finished_at = utcnow()
            r.progress = 1.0 if r.status == "done" else r.progress
        log.info("rendition %s finished", rid)

    def _narrate_section(self, rid: int, index: int, n_total: int, sec, book_lang: str, voice_lang: str,
                         engine, voice: str, speed: float, out_dir: Path, book_title: str, authors: str):
        paragraphs = [clean_for_tts(p, book_lang) for p in sec.spoken_paragraphs()]
        paragraphs = [p for p in paragraphs if p]
        n_headings = len([h for h in sec.headings if h.strip()])
        sr = engine.sample_rate
        pieces: list[np.ndarray] = []
        timeline: list[dict] = []
        t = 0.0
        total_chunks = sum(len(chunk_for_tts([p])) for p in paragraphs)
        self.status.chapter_index, self.status.chapter_title = index, sec.title
        self.status.chunk, self.status.chunks = 0, total_chunks
        self.status.chapter_started = time.time()
        done_chunks = 0
        for pi, para in enumerate(paragraphs):
            chunks = chunk_for_tts([para])
            para_start = t
            for ci, chunk in enumerate(chunks):
                if done_chunks % 4 == 0:
                    self._check_control(rid)
                audio = self._synth(engine, chunk, voice, speed, voice_lang)
                pieces.append(audio)
                t += len(audio) / sr
                if ci != len(chunks) - 1:
                    gap = np.zeros(int(CHUNK_GAP * sr), dtype=np.float32)
                    pieces.append(gap)
                    t += CHUNK_GAP
                done_chunks += 1
                self.status.chunk = done_chunks
                self.status.audio_seconds += len(audio) / sr
                self.status.wall_seconds = time.time() - self.status.chapter_started + 1e-6
            timeline.append({"start": round(para_start, 3), "end": round(t, 3), "text": para,
                             "heading": pi < n_headings})
            gap_len = HEADING_GAP if pi < n_headings else PARAGRAPH_GAP
            pieces.append(np.zeros(int(gap_len * sr), dtype=np.float32))
            t += gap_len
        if not pieces:
            raise RuntimeError("no narratable text")
        samples = np.concatenate(pieces)
        # Gentle normalisation so every chapter has similar loudness.
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 0:
            samples = samples * min(1.0, 0.89 / peak)
        dest = out_dir / f"{index:03d}.{settings.audio_format}"
        duration = encode_audio(samples, sr, dest, title=sec.title, album=book_title, artist=authors,
                                track=index + 1)
        return duration, timeline

    @staticmethod
    def _synth(engine, text: str, voice: str, speed: float, lang: str) -> np.ndarray:
        try:
            audio = engine.synthesize(text, voice, speed, lang)
        except Exception as e:  # noqa: BLE001
            log.warning("synthesis failed (%s); retrying with ASCII-only text", e)
            safe = text.encode("ascii", "ignore").decode("ascii")
            if not safe.strip():
                return np.zeros(int(0.3 * engine.sample_rate), dtype=np.float32)
            try:
                audio = engine.synthesize(safe, voice, speed, lang)
            except Exception as e2:  # noqa: BLE001
                log.error("synthesis failed again (%s); skipping chunk: %r", e2, text[:80])
                return np.zeros(int(0.3 * engine.sample_rate), dtype=np.float32)
        if audio.size == 0:
            return np.zeros(int(0.2 * engine.sample_rate), dtype=np.float32)
        return audio

    # -- M4B export ---------------------------------------------------------------
    def build_m4b(self, rid: int) -> Path:
        """Concatenate a finished rendition into a single chapterised .m4b."""
        with self._m4b_lock, session_scope() as s:
            r = s.get(Rendition, rid)
            if r is None:
                raise RuntimeError("rendition not found")
            book = s.get(Book, r.book_id)
            chapters = [c for c in r.chapters if c.status == "done" and c.file_path]
            if not chapters:
                raise RuntimeError("no finished chapters")
            out_dir = rendition_dir(book.id, rid)
            dest = out_dir / "audiobook.m4b"
            cover = settings.cache_dir / "covers" / f"{book.id}.jpg"
            with tempfile.TemporaryDirectory() as td:
                lst = Path(td) / "list.txt"
                meta = Path(td) / "meta.txt"
                lines = ["ffconcat version 1.0"]
                for c in chapters:
                    lines.append("file '" + c.file_path.replace("'", "'\\''") + "'")
                lst.write_text("\n".join(lines) + "\n")
                m = [";FFMETADATA1", f"title={book.title}", f"artist={book.authors or ''}",
                     f"album={book.title}", "genre=Audiobook"]
                t = 0.0
                for c in chapters:
                    start = int(t * 1000)
                    t += c.duration
                    m += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={int(t * 1000)}",
                          f"title={c.title}"]
                meta.write_text("\n".join(m) + "\n")
                tmp = out_dir / "audiobook.part.m4b"
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                       "-i", str(meta)]
                maps = ["-map_metadata", "1", "-map", "0:a"]
                if cover.exists():
                    cmd += ["-i", str(cover)]
                    maps += ["-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
                cmd += maps
                if settings.audio_format == "m4a":
                    cmd += ["-c:a", "copy"]
                else:
                    cmd += ["-c:a", "aac", "-b:a", settings.audio_bitrate]
                cmd += ["-movflags", "+faststart", "-f", "mp4", str(tmp)]
                subprocess.run(cmd, check=True, capture_output=True)
                tmp.replace(dest)
            r.m4b_path = str(dest)
            return dest


def delete_rendition_files(book_id: int, rendition_id: int) -> None:
    d = rendition_dir(book_id, rendition_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


worker = Worker()
