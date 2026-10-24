"""Per-user listening progress and bookmarks."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import Bookmark, Progress, User, get_db, utcnow
from ..schemas import BookmarkCreate, BookmarkUpdate, ProgressUpdate
from ._helpers import bookmark_dict, get_rendition_or_404, progress_dict, user_bookmarks

router = APIRouter(prefix="/api", tags=["progress"])


def _get_progress(db: Session, user: User, rid: int) -> Progress | None:
    return db.scalar(select(Progress).where(Progress.user_id == user.id, Progress.rendition_id == rid))


def save_progress(db: Session, user: User, rid: int, body: ProgressUpdate) -> dict:
    r = get_rendition_or_404(db, rid)
    p = _get_progress(db, user, rid)
    if p is None:
        p = Progress(user_id=user.id, rendition_id=rid)
        db.add(p)
    p.chapter_index = body.chapter_index
    p.position = body.position
    if body.finished is not None:
        p.finished = body.finished
    if body.playback_rate is not None:
        p.playback_rate = body.playback_rate
    p.updated_at = utcnow()
    db.commit()
    db.refresh(p)
    return progress_dict(r, p)


@router.get("/progress/{rid}")
def get_progress(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    r = get_rendition_or_404(db, rid)
    return progress_dict(r, _get_progress(db, user, rid))


@router.put("/progress/{rid}")
def put_progress(rid: int, body: ProgressUpdate, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    return save_progress(db, user, rid, body)


@router.post("/progress/{rid}/beacon")
async def beacon_progress(rid: int, request: Request, user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict:
    """sendBeacon alias of PUT; the body may arrive as text/plain, so parse it ourselves."""
    raw = await request.body()
    try:
        body = ProgressUpdate.model_validate(json.loads(raw or b"{}"))
    except (ValueError, ValidationError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid progress body: {e}") from e
    return save_progress(db, user, rid, body)


@router.get("/renditions/{rid}/bookmarks")
def list_bookmarks(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    get_rendition_or_404(db, rid)
    return [bookmark_dict(b) for b in user_bookmarks(db, user, rid)]


@router.post("/renditions/{rid}/bookmarks")
def create_bookmark(rid: int, body: BookmarkCreate, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    get_rendition_or_404(db, rid)
    b = Bookmark(user_id=user.id, rendition_id=rid, chapter_index=body.chapter_index, position=body.position,
                 note=(body.note or "").strip() or None)
    db.add(b)
    db.commit()
    db.refresh(b)
    return bookmark_dict(b)


def _own_bookmark(db: Session, user: User, bookmark_id: int) -> Bookmark:
    b = db.get(Bookmark, bookmark_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")
    return b


@router.put("/bookmarks/{bookmark_id}")
def update_bookmark(bookmark_id: int, body: BookmarkUpdate, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    b = _own_bookmark(db, user, bookmark_id)
    b.note = (body.note or "").strip() or None
    db.commit()
    db.refresh(b)
    return bookmark_dict(b)


@router.delete("/bookmarks/{bookmark_id}")
def delete_bookmark(bookmark_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own_bookmark(db, user, bookmark_id))
    db.commit()
    return {"ok": True}
