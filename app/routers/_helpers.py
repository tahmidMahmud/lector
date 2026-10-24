"""Shared helpers for the API routers: runtime settings, permissions, JSON serialisers."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import Book, Bookmark, Chapter, Progress, Rendition, Setting, User
from ..tts.base import describe_voice

APP_NAME = "Lector"
APP_VERSION = "0.1.0"

# Runtime-editable settings and the env default each one falls back to.
RUNTIME_SETTING_KEYS = ("default_voice", "default_speed", "allow_user_requests", "allow_registration")


# -- runtime settings -----------------------------------------------------------
def get_setting(db: Session, key: str, default: Any = None) -> Any:
    """Value stored in the settings table (JSON-decoded), or ``default`` when absent."""
    row = db.get(Setting, key)
    if row is None or row.value is None:
        return default
    try:
        return json.loads(row.value)
    except ValueError:
        return row.value


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.add(row)
    row.value = json.dumps(value)


def effective_settings(db: Session) -> dict[str, Any]:
    return {
        "default_voice": str(get_setting(db, "default_voice", settings.default_voice)),
        "default_speed": float(get_setting(db, "default_speed", settings.default_speed)),
        "allow_user_requests": bool(get_setting(db, "allow_user_requests", settings.allow_user_requests)),
        "allow_registration": bool(get_setting(db, "allow_registration", settings.allow_registration)),
    }


def effective_setting(db: Session, key: str) -> Any:
    return effective_settings(db)[key]


# -- errors / permissions ---------------------------------------------------------
def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, what)


def require_requester(db: Session, user: User) -> None:
    """Admins always; regular users only while allow_user_requests is on."""
    if user.is_admin or effective_setting(db, "allow_user_requests"):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can request narrations")


def get_rendition_or_404(db: Session, rid: int) -> Rendition:
    r = db.get(Rendition, rid)
    if r is None:
        raise not_found("Rendition not found")
    return r


def get_book_or_404(db: Session, book_id: int) -> Book:
    b = db.get(Book, book_id)
    if b is None:
        raise not_found("Book not found")
    return b


def normalize_speed(speed: float) -> float:
    return round(float(speed), 2)


# -- serialisers -------------------------------------------------------------------
def iso(dt: datetime | None) -> str | None:
    """DB datetimes are naive UTC; emit ISO-8601 with an explicit Z."""
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"


def user_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "is_admin": bool(u.is_admin), "created_at": iso(u.created_at),
        "playback_rate": u.playback_rate, "skip_back_seconds": u.skip_back_seconds,
        "skip_forward_seconds": u.skip_forward_seconds, "preferred_voice": u.preferred_voice,
        "theme": u.theme,
    }


def voice_dict(v: dict) -> dict:
    return {"id": v["id"], "name": v.get("name", v["id"]), "language": v.get("language", ""),
            "gender": v.get("gender", "")}


def has_m4b(r: Rendition) -> bool:
    return bool(r.m4b_path) and Path(r.m4b_path).exists()


def rendition_dict(r: Rendition) -> dict:
    return {
        "id": r.id, "book_id": r.book_id, "voice": r.voice, "voice_name": describe_voice(r.voice)["name"],
        "speed": r.speed, "engine": r.engine, "status": r.status, "progress": round(r.progress or 0.0, 4),
        "done_chapters": r.done_chapters, "total_chapters": r.total_chapters,
        "total_duration": round(r.total_duration or 0.0, 3), "error": r.error,
        "created_at": iso(r.created_at), "finished_at": iso(r.finished_at), "has_m4b": has_m4b(r),
    }


def chapter_dict(c: Chapter) -> dict:
    return {"index": c.index, "title": c.title, "status": c.status, "duration": round(c.duration or 0.0, 3),
            "char_count": c.char_count, "error": c.error}


def _done_durations(r: Rendition) -> dict[int, float]:
    return {c.index: float(c.duration or 0.0) for c in r.chapters if c.status == "done"}


def progress_position(r: Rendition, chapter_index: int, position: float, finished: bool) -> tuple[float, float]:
    """(percent 0..100, remaining_seconds) over the rendition's narrated (done) chapters."""
    durations = _done_durations(r)
    total = sum(durations.values())
    if total <= 0:
        return (100.0 if finished else 0.0), 0.0
    if finished:
        return 100.0, 0.0
    elapsed = sum(d for i, d in durations.items() if i < chapter_index)
    elapsed += min(max(position, 0.0), durations.get(chapter_index, position))
    elapsed = min(elapsed, total)
    return round(100.0 * elapsed / total, 2), round(total - elapsed, 1)


def progress_dict(r: Rendition, p: Progress | None) -> dict:
    """Progress for a rendition; zeros (not null) when the user has none."""
    if p is None:
        percent, remaining = progress_position(r, 0, 0.0, False)
        return {"rendition_id": r.id, "chapter_index": 0, "position": 0.0, "finished": False,
                "playback_rate": None, "updated_at": None, "percent": percent, "remaining_seconds": remaining}
    percent, remaining = progress_position(r, p.chapter_index, p.position, bool(p.finished))
    return {"rendition_id": r.id, "chapter_index": p.chapter_index, "position": round(p.position, 3),
            "finished": bool(p.finished), "playback_rate": p.playback_rate, "updated_at": iso(p.updated_at),
            "percent": percent, "remaining_seconds": remaining}


def bookmark_dict(b: Bookmark) -> dict:
    return {"id": b.id, "rendition_id": b.rendition_id, "chapter_index": b.chapter_index,
            "position": round(b.position, 3), "note": b.note, "created_at": iso(b.created_at)}


def cover_url(b: Book) -> str | None:
    return f"/api/books/{b.id}/cover" if b.cover_cached else None


def user_progress_map(db: Session, user: User, rendition_ids: Iterable[int] | None = None) -> dict[int, Progress]:
    q = select(Progress).where(Progress.user_id == user.id)
    if rendition_ids is not None:
        ids = list(rendition_ids)
        if not ids:
            return {}
        q = q.where(Progress.rendition_id.in_(ids))
    return {p.rendition_id: p for p in db.scalars(q)}


def book_summary(b: Book, progress_by_rid: dict[int, Progress]) -> dict:
    """BookSummary; ``progress`` is the user's most recently updated progress across renditions."""
    renditions = sorted(b.renditions, key=lambda r: r.id)
    latest: tuple[Rendition, Progress] | None = None
    for r in renditions:
        p = progress_by_rid.get(r.id)
        if p is not None and (latest is None or p.updated_at > latest[1].updated_at):
            latest = (r, p)
    return {
        "id": b.id, "title": b.title, "series_title": b.series_title, "volume_label": b.volume_label,
        "authors": b.authors, "summary": b.summary, "cover_url": cover_url(b), "word_count": b.word_count,
        "file_format": b.file_format, "is_gutenberg": bool(b.is_gutenberg), "missing": bool(b.missing),
        "renditions": [rendition_dict(r) for r in renditions],
        "progress": progress_dict(*latest) if latest else None,
    }


def user_bookmarks(db: Session, user: User, rid: int) -> list[Bookmark]:
    return list(db.scalars(select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.rendition_id == rid)
                          .order_by(Bookmark.chapter_index, Bookmark.position, Bookmark.id)))


def rendition_full(db: Session, r: Rendition, user: User, progress_by_rid: dict[int, Progress]) -> dict:
    """Rendition + {chapters, progress (user's Progress|null), bookmarks}.

    Note: per the contract the user's Progress object replaces the 0..1 generation
    ``progress`` here; the generation fraction is kept as ``generation_progress``.
    """
    d = rendition_dict(r)
    p = progress_by_rid.get(r.id)
    d["generation_progress"] = d["progress"]
    d["chapters"] = [chapter_dict(c) for c in r.chapters]
    d["progress"] = progress_dict(r, p) if p is not None else None
    d["bookmarks"] = [bookmark_dict(b) for b in user_bookmarks(db, user, r.id)]
    return d


def book_detail(db: Session, b: Book, user: User) -> dict:
    progress_by_rid = user_progress_map(db, user, [r.id for r in b.renditions])
    d = book_summary(b, progress_by_rid)
    d["renditions"] = [rendition_full(db, r, user, progress_by_rid) for r in sorted(b.renditions, key=lambda r: r.id)]
    return d


BOOK_LOAD_OPTIONS = (selectinload(Book.renditions).selectinload(Rendition.chapters),)
