"""Library browsing, voices, and rendition requests / admin job control."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import admin_user, current_user
from ..config import settings
from ..db import Book, Progress, Rendition, User, get_db
from ..schemas import RenditionCreate
from ..sync import cover_path
from ..tts import get_engine
from ..tts.base import describe_voice
from ..worker import delete_rendition_files, worker
from ._helpers import (
    BOOK_LOAD_OPTIONS, book_detail, book_summary, bookmark_dict, chapter_dict, effective_settings,
    get_book_or_404, get_rendition_or_404, normalize_speed, progress_dict, rendition_dict, require_requester,
    user_bookmarks, user_progress_map, voice_dict,
)

log = logging.getLogger("lector.api")
router = APIRouter(prefix="/api", tags=["library"])

CONTINUE_LIMIT = 12
PREVIEW_TEXT = ("Chapter one. It was the best of times, it was the worst of times, it was the age of wisdom, "
                "it was the age of foolishness, it was the epoch of belief.")  # ~8 s at 1x
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_preview_lock = threading.Lock()


# -- library --------------------------------------------------------------------------
@router.get("/library")
def library(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    books = list(db.scalars(select(Book).options(*BOOK_LOAD_OPTIONS)
                            .order_by(func.lower(func.coalesce(Book.sort_title, Book.title)), Book.id)))
    progress_by_rid = user_progress_map(db, user)
    summaries = {b.id: book_summary(b, progress_by_rid) for b in books}
    renditions = {r.id: r for b in books for r in b.renditions}
    recent = sorted((p for p in progress_by_rid.values() if not p.finished and p.rendition_id in renditions),
                    key=lambda p: p.updated_at, reverse=True)[:CONTINUE_LIMIT]
    cont = [{"book": summaries[renditions[p.rendition_id].book_id],
             "rendition": rendition_dict(renditions[p.rendition_id]),
             "progress": progress_dict(renditions[p.rendition_id], p)} for p in recent]
    return {"books": list(summaries.values()), "continue": cont}


@router.get("/books/{book_id}")
def book(book_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return book_detail(db, get_book_or_404(db, book_id), user)


def _image_media_type(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(8)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


@router.get("/books/{book_id}/cover")
def cover(book_id: int, _: User = Depends(current_user)) -> FileResponse:
    p = cover_path(book_id)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    return FileResponse(p, media_type=_image_media_type(p), headers={"Cache-Control": "private, max-age=86400"})


# -- voices ---------------------------------------------------------------------------
def _engine_voices() -> list[dict]:
    try:
        return [voice_dict(v) for v in get_engine().voices()]
    except Exception:  # noqa: BLE001
        log.exception("listing voices failed")
        return []


async def voice_ids() -> set[str]:
    return {v["id"] for v in await run_in_threadpool(_engine_voices)}


async def validate_voice(voice: str) -> None:
    if not VOICE_ID_RE.match(voice):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid voice id")
    ids = await voice_ids()
    if ids and voice not in ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown voice: {voice}")


@router.get("/voices")
async def voices(db: Session = Depends(get_db), _: User = Depends(current_user)) -> dict:
    eff = effective_settings(db)
    return {"voices": await run_in_threadpool(_engine_voices), "default_voice": eff["default_voice"],
            "default_speed": eff["default_speed"]}


def _encode_mp3(samples: np.ndarray, sr: int, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.mp3")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
           "-c:a", "libmp3lame", "-b:a", settings.audio_bitrate, str(tmp)]
    subprocess.run(cmd, input=samples.astype("<f4").tobytes(), check=True, capture_output=True)
    tmp.replace(dest)


def _build_preview(voice: str, dest: Path) -> None:
    with _preview_lock:
        if dest.exists():
            return
        engine = get_engine()
        ok, msg = engine.ready()
        if not ok:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"TTS engine not ready: {msg}")
        samples = engine.synthesize(PREVIEW_TEXT, voice, 1.0, describe_voice(voice)["lang"])
        _encode_mp3(np.asarray(samples, dtype=np.float32), engine.sample_rate, dest)


@router.get("/voices/{voice}/preview")
async def voice_preview(voice: str, _: User = Depends(current_user)) -> FileResponse:
    await validate_voice(voice)
    dest = settings.cache_dir / "previews" / f"{voice}.mp3"
    if not dest.exists():
        try:
            await run_in_threadpool(_build_preview, voice, dest)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("voice preview failed for %s", voice)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Preview failed: {e}") from e
    return FileResponse(dest, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=604800"})


# -- renditions -----------------------------------------------------------------------
@router.post("/books/{book_id}/renditions")
async def request_rendition(book_id: int, body: RenditionCreate, user: User = Depends(current_user),
                            db: Session = Depends(get_db)) -> dict:
    require_requester(db, user)
    b = get_book_or_404(db, book_id)
    await validate_voice(body.voice)
    speed = normalize_speed(body.speed if body.speed is not None else effective_settings(db)["default_speed"])
    existing = db.scalar(select(Rendition).where(Rendition.book_id == b.id, Rendition.voice == body.voice,
                                                 Rendition.speed == speed))
    if existing is not None:
        if existing.status == "cancelled":  # re-requesting a cancelled narration re-queues it
            existing.status = "queued"
            existing.error = None
            db.commit()
            worker.wake()
        return rendition_dict(existing)
    r = Rendition(book_id=b.id, voice=body.voice, speed=speed, engine=get_engine().name, status="queued",
                  requested_by=user.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    worker.wake()
    return rendition_dict(r)


@router.get("/renditions/{rid}")
def rendition(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    r = get_rendition_or_404(db, rid)
    b = db.scalars(select(Book).options(*BOOK_LOAD_OPTIONS).where(Book.id == r.book_id)).one()
    progress_by_rid = user_progress_map(db, user, [x.id for x in b.renditions])
    p: Progress | None = progress_by_rid.get(rid)
    d = rendition_dict(r)
    d["generation_progress"] = d["progress"]
    d.update({
        "book": book_summary(b, progress_by_rid),
        "chapters": [chapter_dict(c) for c in r.chapters],
        "progress": progress_dict(r, p) if p is not None else None,
        "bookmarks": [bookmark_dict(x) for x in user_bookmarks(db, user, rid)],
    })
    return d


# -- admin job control ---------------------------------------------------------------
_TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "pause": ({"queued", "running"}, "paused"),
    "resume": ({"paused"}, "queued"),
    "cancel": ({"queued", "running", "paused"}, "cancelled"),
    "retry": ({"error", "cancelled"}, "queued"),
}


def _apply_transition(db: Session, r: Rendition, action: str) -> None:
    allowed, target = _TRANSITIONS[action]
    if r.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot {action} a rendition that is {r.status}")
    r.status = target
    if action == "retry":
        r.error = None
        r.finished_at = None
        for c in r.chapters:
            if c.status in ("error", "running"):
                c.status = "pending"
                c.error = None
    db.commit()
    if target == "queued":
        worker.wake()


def _control(action: str):
    def endpoint(rid: int, _: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
        r = get_rendition_or_404(db, rid)
        _apply_transition(db, r, action)
        db.refresh(r)
        return rendition_dict(r)
    endpoint.__name__ = f"rendition_{action}"
    return endpoint


for _action in _TRANSITIONS:
    router.add_api_route(f"/renditions/{{rid}}/{_action}", _control(_action), methods=["POST"],
                         name=f"rendition_{_action}")


@router.delete("/renditions/{rid}")
def delete_rendition(rid: int, _: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    r = get_rendition_or_404(db, rid)
    if r.status == "running":
        # Make the worker drop the job at its next control check before we pull the files.
        r.status = "cancelled"
        db.commit()
    delete_rendition_files(r.book_id, r.id)
    db.delete(r)
    db.commit()
    return {"ok": True}
