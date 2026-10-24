"""Background generation worker: EPUB -> chapters -> TTS -> encoded audio.

One worker thread runs at a time (TTS saturates the CPU). Jobs are Rendition rows
with status ``queued``; chapters already ``done`` are skipped, so a restart
resumes where it left off. Status changes made through the API (paused /
cancelled) are picked up between chunks.

DB sessions are kept short: nothing holds a session while audio is being
synthesised or encoded. Chapter audio is streamed to a temporary WAV file as it
is generated, so memory use does not grow with chapter length.
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
TARGET_PEAK = 0.89     # chapters louder than this are attenuated to it
CONTROL_EVERY = 4      # check for pause/cancel every N chunks
MAX_CONSECUTIVE_FAILURES = 3  # this many unsynthesisable chunks in a row fails the chapter
AUDIO_FORMATS = ("mp3", "m4a")


@dataclass
class LiveStatus:
    rendition_id: int | None = None
    book_title: str = ""
    chapter_index: int | None = None
    chapter_title: str = ""
    chunk: int = 0
    chunks: int = 0
    chapter_started: float = 0.0
    job_started: float = 0.0
    audio_seconds: float = 0.0   # audio generated in this job
    wall_seconds: float = 0.0    # synthesis wall time in this job
    engine_ready: bool | None = None
    engine_message: str = ""
    history: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        rtf = round(self.audio_seconds / self.wall_seconds, 2) if self.wall_seconds > 1 else None
        return {
            "rendition_id": self.rendition_id, "book_title": self.book_title,
            "chapter_index": self.chapter_index, "chapter_title": self.chapter_title,
            "chunk": self.chunk, "chunks": self.chunks,
            "audio_seconds": round(self.audio_seconds, 1),
            "realtime_factor": rtf,
            "engine_ready": self.engine_ready, "engine_message": self.engine_message,
        }


class CancelledError(Exception):
    """The rendition was paused/cancelled (or the worker is stopping)."""


class NothingToNarrate(Exception):
    """A section has no speakable text after cleaning."""


def rendition_dir(book_id: int, rendition_id: int) -> Path:
    return settings.audio_dir / str(book_id) / str(rendition_id)


def audio_ext() -> str:
    fmt = (settings.audio_format or "mp3").lower().lstrip(".")
    if fmt not in AUDIO_FORMATS:
        log.warning("AUDIO_FORMAT=%r is not supported (use mp3 or m4a); using mp3", fmt)
        return "mp3"
    return fmt


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH; it is required to encode audio")
    return exe


def _run_ffmpeg(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed (exit {e.returncode}): {err[-1500:] or 'no output'}") from None


def probe_duration(path: Path) -> float | None:
    """Duration in seconds according to ffprobe, or None if unavailable."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", str(path)],
                             check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return None


def encode_wav(wav: Path, dest: Path, title: str, album: str, artist: str, track: int,
               gain: float = 1.0) -> None:
    """Encode a WAV file to dest (mp3/m4a, chosen by dest's suffix) with ffmpeg, atomically."""
    ffmpeg = _ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = dest.suffix.lstrip(".").lower()
    meta = ["-metadata", f"title={title}", "-metadata", f"album={album}", "-metadata", f"artist={artist}",
            "-metadata", f"track={track}", "-metadata", "genre=Audiobook"]
    if fmt == "m4a":
        codec = ["-c:a", "aac", "-b:a", settings.audio_bitrate, "-movflags", "+faststart"]
    else:
        codec = ["-c:a", "libmp3lame", "-b:a", settings.audio_bitrate, "-id3v2_version", "3"]
    af = ["-af", f"volume={gain:.6f}"] if abs(gain - 1.0) > 1e-4 else []
    tmp_out = dest.with_name(dest.stem + ".part" + dest.suffix)
    cmd = [ffmpeg, "-y", "-nostdin", "-loglevel", "error", "-i", str(wav), "-ac", "1", *af, *codec, *meta,
           str(tmp_out)]
    try:
        _run_ffmpeg(cmd)
        tmp_out.replace(dest)
    finally:
        tmp_out.unlink(missing_ok=True)


def encode_audio(samples: np.ndarray, sr: int, dest: Path, title: str, album: str, artist: str,
                 track: int) -> float:
    """Encode float32 mono samples to dest (mp3/m4a) with ffmpeg. Returns duration in seconds."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", dir=dest.parent, delete=False) as tmp:
        wav = Path(tmp.name)
    try:
        sf.write(str(wav), np.asarray(samples, dtype=np.float32), sr, subtype="FLOAT")
        encode_wav(wav, dest, title, album, artist, track)
    finally:
        wav.unlink(missing_ok=True)
    return len(samples) / sr


def _ffmeta_escape(v: str) -> str:
    """Escape a value for ffmpeg's FFMETADATA1 format."""
    v = " ".join(str(v).split())
    for ch in ("\\", "=", ";", "#"):
        v = v.replace(ch, "\\" + ch)
    return v


def _write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


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
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lector-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the worker to stop. The current chapter is abandoned at the next chunk
        boundary and resumed (rendition requeued) on the next start."""
        self._stop.set()
        self._wake.set()
        if timeout and self._thread:
            self._thread.join(timeout)

    def wake(self) -> None:
        self._wake.set()

    # -- main loop -----------------------------------------------------------
    def check_engine(self) -> bool:
        engine = get_engine()
        ok, msg = engine.ready()
        if ok:
            try:
                t0 = time.time()
                engine.warmup()
                log.info("TTS engine %s warmed up in %.1fs", engine.name, time.time() - t0)
            except Exception as e:  # noqa: BLE001
                log.warning("TTS warmup failed: %s", e)
        self.status.engine_ready, self.status.engine_message = ok, msg
        if not ok:
            log.error("TTS engine not ready: %s", msg)
        return ok

    @staticmethod
    def requeue_interrupted() -> int:
        """Anything left ``running`` by a previous process goes back to the queue."""
        n = 0
        with session_scope() as s:
            for r in s.scalars(select(Rendition).where(Rendition.status == "running")):
                r.status = "queued"
                n += 1
            for c in s.scalars(select(Chapter).where(Chapter.status == "running")):
                c.status = "pending"
        if n:
            log.info("requeued %d interrupted rendition(s)", n)
        return n

    def _run(self) -> None:
        try:
            self.check_engine()
            self.requeue_interrupted()
        except Exception:  # noqa: BLE001
            log.exception("worker start-up failed")
        while not self._stop.is_set():
            try:
                rid = self._next_job()
            except Exception:  # noqa: BLE001  (e.g. database locked) - keep the thread alive
                log.exception("could not fetch next job")
                self._stop.wait(10)
                continue
            if rid is None:
                self._wake.wait(timeout=15)
                self._wake.clear()
                continue
            self.run_job(rid)

    def run_job(self, rid: int) -> None:
        """Process one rendition, recording failures on the row."""
        try:
            self._process(rid)
        except CancelledError:
            log.info("rendition %s paused/cancelled/stopped", rid)
        except Exception as e:  # noqa: BLE001
            log.exception("rendition %s failed", rid)
            try:
                with session_scope() as s:
                    r = s.get(Rendition, rid)
                    if r is not None and r.status not in ("paused", "cancelled"):
                        r.status = "error"
                        r.error = str(e)[:2000] or type(e).__name__
            except Exception:  # noqa: BLE001
                log.exception("could not record failure of rendition %s", rid)
        finally:
            self.status = LiveStatus(engine_ready=self.status.engine_ready,
                                     engine_message=self.status.engine_message)

    def _next_job(self) -> int | None:
        with session_scope() as s:
            r = s.scalars(select(Rendition).where(Rendition.status == "queued")
                          .order_by(Rendition.created_at, Rendition.id)).first()
            if r is None:
                return None
            r.status = "running"
            r.error = None
            return r.id

    def _check_control(self, rid: int) -> None:
        if self._stop.is_set():
            raise CancelledError()
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
            if book is None:
                raise RuntimeError("book no longer exists")
            voice, speed, book_id = r.voice, float(r.speed or 1.0), r.book_id
            book_title, authors, local_path = book.title, book.authors or "", book.local_path
        self.status.rendition_id, self.status.book_title = rid, book_title
        self.status.job_started = time.time()

        if not local_path or not Path(local_path).exists():
            raise RuntimeError(f"Book file not found: {local_path!r} (check KAVITA_PATH_MAP / volume mounts)")
        if not local_path.lower().endswith(".epub"):
            raise RuntimeError("Only EPUB books can be narrated right now")

        engine = get_engine()
        ok, msg = engine.ready()
        if not ok:
            raise RuntimeError(f"TTS engine not ready: {msg}")
        if engine.has_voice(voice) is False:
            raise RuntimeError(f"Unknown voice {voice!r} for the {engine.name} engine")

        bt: BookText = extract_book(local_path)
        log.info("extracted %r: %d sections, gutenberg=%s trimmed=%s/%s", bt.title, len(bt.sections),
                 bt.is_gutenberg, bt.trimmed_start, bt.trimmed_end)
        if not bt.sections:
            raise RuntimeError("no readable text found in this EPUB")
        out_dir = rendition_dir(book_id, rid)
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = audio_ext()
        chapters = self._sync_chapters(rid, bt, out_dir)

        lang = describe_voice(voice)["lang"]
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
            dest = out_dir / f"{i:03d}.{ext}"
            try:
                duration, timeline = self._narrate_section(rid, i, n_total, sec, bt.language, lang, engine,
                                                           voice, speed, dest, book_title, authors)
            except CancelledError:
                with session_scope() as s:
                    c = s.get(Chapter, cid)
                    c.status = "pending"
                raise
            except NothingToNarrate:
                log.info("chapter %d of rendition %d has no narratable text; skipping", i, rid)
                with session_scope() as s:
                    c = s.get(Chapter, cid)
                    c.status = "skipped"
                    c.error = "no narratable text"
                self._update_totals(rid)
                continue
            except Exception as e:  # noqa: BLE001
                log.exception("chapter %d of rendition %d failed", i, rid)
                with session_scope() as s:
                    c = s.get(Chapter, cid)
                    c.status = "error"
                    c.error = (str(e) or type(e).__name__)[:2000]
                continue
            # Timeline first, so a "done" chapter always has one.
            _write_json_atomic(out_dir / f"{i:03d}.json", timeline)
            with session_scope() as s:
                c = s.get(Chapter, cid)
                c.status = "done"
                c.duration = duration
                c.file_path = str(dest)
            self._update_totals(rid)

        with session_scope() as s:
            r = s.get(Rendition, rid)
            if r is None or r.status != "running":
                return
            errors = [c for c in r.chapters if c.status == "error"]
            pending = [c for c in r.chapters if c.status in ("pending", "running")]
            if pending:
                r.status = "queued"  # shouldn't happen, but be safe
                return
            if errors:
                r.status = "error"
                r.error = f"{len(errors)} chapter(s) failed: " + "; ".join(
                    f"{c.title}: {c.error}" for c in errors[:3])
            elif not any(c.status == "done" for c in r.chapters):
                r.status = "error"
                r.error = "nothing to narrate: every section was skipped"
            else:
                r.status = "done"
                r.error = None
                r.progress = 1.0
            r.finished_at = utcnow()
        log.info("rendition %s finished", rid)

    def _sync_chapters(self, rid: int, bt: BookText, out_dir: Path) -> dict[int, tuple[int, str]]:
        """Create chapter rows on first run; on resume, invalidate chapters whose text changed."""
        with session_scope() as s:
            r = s.get(Rendition, rid)
            existing = list(r.chapters)
            if existing and len(existing) != len(bt.sections):
                log.warning("rendition %d: book now has %d sections (was %d); starting over",
                            rid, len(bt.sections), len(existing))
                for c in existing:
                    s.delete(c)
                s.flush()
                for f in out_dir.glob("[0-9][0-9][0-9].*"):
                    f.unlink(missing_ok=True)
                r.chapters = []
                existing = []
            if not existing:
                for i, sec in enumerate(bt.sections):
                    s.add(Chapter(rendition_id=rid, index=i, title=sec.title,
                                  status="skipped" if sec.skipped else "pending",
                                  char_count=sec.char_count, text_hash=sec.text_hash()))
            else:
                for c in existing:
                    sec = bt.sections[c.index]
                    h = sec.text_hash()
                    if c.text_hash != h:
                        log.info("rendition %d chapter %d text changed; re-narrating", rid, c.index)
                        c.text_hash, c.title, c.char_count = h, sec.title, sec.char_count
                        c.status = "skipped" if sec.skipped else "pending"
                        c.duration, c.file_path, c.error = 0.0, None, None
            r.total_chapters = len(bt.sections)
            s.flush()
            s.expire(r, ["chapters"])
            chapters = {c.index: (c.id, c.status) for c in r.chapters}
        self._update_totals(rid)
        return chapters

    @staticmethod
    def _update_totals(rid: int) -> None:
        with session_scope() as s:
            r = s.get(Rendition, rid)
            if r is None:
                return
            chs = list(r.chapters)
            done = [c for c in chs if c.status == "done"]
            narr = [c for c in chs if c.status != "skipped"]
            r.done_chapters = len(done)
            r.progress = len(done) / max(1, len(narr))
            r.total_duration = float(sum(c.duration or 0.0 for c in done))

    def _narrate_section(self, rid: int, index: int, n_total: int, sec, book_lang: str, voice_lang: str,
                         engine, voice: str, speed: float, dest: Path, book_title: str, authors: str):
        """Synthesise one section into ``dest``. Returns (duration_seconds, timeline)."""
        spoken = sec.spoken_paragraphs()
        n_head = len(sec.headings)
        # (text, is_heading, chunks) - flags are computed before filtering so they stay aligned.
        items = []
        for pi, p in enumerate(spoken):
            text = clean_for_tts(p, book_lang)
            chunks = chunk_for_tts([text]) if text else []
            if chunks:
                items.append((text, pi < n_head, chunks))
        if not items:
            raise NothingToNarrate()
        sr = int(engine.sample_rate)
        total_chunks = sum(len(c) for _, _, c in items)
        self.status.chapter_index, self.status.chapter_title = index, sec.title
        self.status.chunk, self.status.chunks = 0, total_chunks
        self.status.chapter_started = time.time()

        timeline: list[dict] = []
        pos = 0            # samples written
        peak = 0.0
        done_chunks = failed = consecutive = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        wav = dest.with_name(f".{dest.stem}.partial.wav")
        try:
            with sf.SoundFile(str(wav), mode="w", samplerate=sr, channels=1, subtype="FLOAT") as out:
                def write(a: np.ndarray) -> None:
                    nonlocal pos
                    out.write(a)
                    pos += len(a)

                for text, is_heading, chunks in items:
                    start = pos / sr
                    for ci, chunk in enumerate(chunks):
                        if done_chunks % CONTROL_EVERY == 0:
                            self._check_control(rid)
                        t0 = time.time()
                        audio, ok = self._synth(engine, chunk, voice, speed, voice_lang)
                        if ok:
                            consecutive = 0
                        else:
                            failed += 1
                            consecutive += 1
                            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                                raise RuntimeError(
                                    f"TTS failed on {consecutive} consecutive chunks (last: {chunk[:60]!r})")
                        if audio.size:
                            peak = max(peak, float(np.max(np.abs(audio))))
                        write(audio)
                        if ci != len(chunks) - 1:
                            write(np.zeros(int(round(CHUNK_GAP * sr)), dtype=np.float32))
                        done_chunks += 1
                        self.status.chunk = done_chunks
                        self.status.audio_seconds += len(audio) / sr
                        self.status.wall_seconds += time.time() - t0
                    timeline.append({"start": round(start, 3), "end": round(pos / sr, 3), "text": text,
                                     "heading": is_heading})
                    gap = HEADING_GAP if is_heading else PARAGRAPH_GAP
                    write(np.zeros(int(round(gap * sr)), dtype=np.float32))
            if failed and failed >= done_chunks:
                raise RuntimeError("TTS failed on every chunk of this chapter")
            if failed:
                log.warning("chapter %d: %d of %d chunks could not be synthesised (silence inserted)",
                            index, failed, done_chunks)
            # Gentle normalisation so every chapter has similar loudness (attenuate only).
            gain = min(1.0, TARGET_PEAK / peak) if peak > 0 else 1.0
            encode_wav(wav, dest, title=sec.title, album=book_title, artist=authors, track=index + 1,
                       gain=gain)
        finally:
            wav.unlink(missing_ok=True)
        return pos / sr, timeline

    @staticmethod
    def _synth(engine, text: str, voice: str, speed: float, lang: str) -> tuple[np.ndarray, bool]:
        """Synthesise a chunk. Returns (samples, ok); on failure the samples are a short silence."""
        silence = np.zeros(int(0.3 * engine.sample_rate), dtype=np.float32)
        try:
            audio = engine.synthesize(text, voice, speed, lang)
        except Exception as e:  # noqa: BLE001
            log.warning("synthesis failed (%s); retrying with ASCII-only text", e)
            safe = text.encode("ascii", "ignore").decode("ascii")
            if not safe.strip():
                return silence, True   # nothing speakable in this chunk; not an engine failure
            try:
                audio = engine.synthesize(safe, voice, speed, lang)
            except Exception as e2:  # noqa: BLE001
                log.error("synthesis failed again (%s); skipping chunk: %r", e2, text[:80])
                return silence, False
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return np.zeros(int(0.2 * engine.sample_rate), dtype=np.float32), True
        if not np.all(np.isfinite(audio)):
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        return audio, True

    # -- M4B export ---------------------------------------------------------------
    def build_m4b(self, rid: int) -> Path:
        """Concatenate a finished rendition into a single chapterised .m4b."""
        with self._m4b_lock:
            with session_scope() as s:
                r = s.get(Rendition, rid)
                if r is None:
                    raise RuntimeError("rendition not found")
                book = s.get(Book, r.book_id)
                chapters = [(c.title, c.file_path, float(c.duration or 0.0)) for c in r.chapters
                            if c.status == "done" and c.file_path]
                book_id, title, authors = book.id, book.title, book.authors or ""
            chapters = [c for c in chapters if Path(c[1]).exists()]
            if not chapters:
                raise RuntimeError("no finished chapters")
            ffmpeg = _ffmpeg()
            out_dir = rendition_dir(book_id, rid)
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / "audiobook.m4b"
            cover = settings.cache_dir / "covers" / f"{book_id}.jpg"
            all_aac = all(p.lower().endswith(".m4a") for _, p, _ in chapters)
            with tempfile.TemporaryDirectory() as td:
                lst = Path(td) / "list.txt"
                meta = Path(td) / "meta.txt"
                lines = ["ffconcat version 1.0"]
                for _, p, _ in chapters:
                    lines.append("file '" + p.replace("'", "'\\''") + "'")
                lst.write_text("\n".join(lines) + "\n")
                m = [";FFMETADATA1", f"title={_ffmeta_escape(title)}", f"artist={_ffmeta_escape(authors)}",
                     f"album_artist={_ffmeta_escape(authors)}", f"album={_ffmeta_escape(title)}",
                     "genre=Audiobook"]
                t = 0.0
                for ctitle, p, dur in chapters:
                    # Encoded files can differ slightly from the sample count (encoder padding).
                    real = probe_duration(Path(p)) or dur
                    start = int(round(t * 1000))
                    t += real
                    m += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={int(round(t * 1000))}",
                          f"title={_ffmeta_escape(ctitle)}"]
                meta.write_text("\n".join(m) + "\n")
                tmp = out_dir / "audiobook.part.m4b"
                cmd = [ffmpeg, "-y", "-nostdin", "-loglevel", "error", "-f", "concat", "-safe", "0",
                       "-i", str(lst), "-i", str(meta)]
                maps = ["-map_metadata", "1", "-map_chapters", "1", "-map", "0:a"]
                if cover.exists():
                    cmd += ["-i", str(cover)]
                    maps += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
                cmd += maps
                if all_aac:
                    cmd += ["-c:a", "copy"]
                else:
                    cmd += ["-c:a", "aac", "-b:a", settings.audio_bitrate]
                cmd += ["-movflags", "+faststart", "-f", "mp4", str(tmp)]
                try:
                    _run_ffmpeg(cmd)
                    tmp.replace(dest)
                finally:
                    tmp.unlink(missing_ok=True)
            with session_scope() as s:
                r = s.get(Rendition, rid)
                if r is not None:
                    r.m4b_path = str(dest)
            return dest


def delete_rendition_files(book_id: int, rendition_id: int) -> None:
    d = rendition_dir(book_id, rendition_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


worker = Worker()
