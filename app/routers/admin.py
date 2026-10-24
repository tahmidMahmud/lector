"""Admin-only endpoints: server status, Kavita sync, users, runtime settings."""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from .. import sync
from ..auth import admin_user, hash_password
from ..config import settings
from ..db import Rendition, SyncLog, User, get_db
from ..schemas import AdminUserCreate, AdminUserUpdate, SettingsUpdate
from ..tts import get_engine
from ..worker import worker
from ._helpers import effective_settings, iso, rendition_dict, set_setting, user_dict
from .auth import create_user
from .library import validate_voice

log = logging.getLogger("lector.api")
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)])

QUEUE_STATUSES = ("running", "queued", "paused", "error")


# -- status ---------------------------------------------------------------------------
def _kavita_status() -> dict:
    kv = sync.kavita()
    out = {"url": settings.kavita_url, "configured": kv.configured, "reachable": False, "version": None}
    try:
        out["reachable"] = kv.health()
        if out["reachable"] and kv.configured:
            version = kv.server_info().get("kavitaVersion")
            out["version"] = str(version) if version is not None else None
    except Exception as e:  # noqa: BLE001 - login errors surface as httpx/Kavita errors
        log.warning("kavita status check failed: %s", e)
    return out


def _engine_status() -> dict:
    engine = get_engine()
    try:
        ok, msg = engine.ready()
    except Exception as e:  # noqa: BLE001
        ok, msg = False, str(e)
    return {"name": engine.name, "ready": ok, "message": msg}


def _dir_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _storage_status() -> dict:
    try:
        free = shutil.disk_usage(settings.audio_dir).free
    except OSError:
        free = None
    return {"audio_bytes": _dir_bytes(settings.audio_dir), "free_bytes": free}


def _queue(db: Session) -> list[dict]:
    order = case({s: i for i, s in enumerate(QUEUE_STATUSES)}, value=Rendition.status)
    rows = db.scalars(select(Rendition).options(selectinload(Rendition.book))
                      .where(Rendition.status.in_(QUEUE_STATUSES)).order_by(order, Rendition.created_at))
    return [{**rendition_dict(r), "book_title": r.book.title if r.book else None} for r in rows]


def _last_sync(db: Session) -> dict | None:
    row = db.scalars(select(SyncLog).order_by(SyncLog.id.desc())).first()
    if row is None:
        return None
    return {"started_at": iso(row.started_at), "finished_at": iso(row.finished_at), "status": row.status,
            "message": row.message, "books_seen": row.books_seen}


@router.get("/status")
async def admin_status(db: Session = Depends(get_db)) -> dict:
    kavita_info = await run_in_threadpool(_kavita_status)
    engine_info = await run_in_threadpool(_engine_status)
    storage = await run_in_threadpool(_storage_status)
    return {"kavita": kavita_info, "engine": engine_info, "worker": worker.status.as_dict(),
            "queue": _queue(db), "last_sync": _last_sync(db), "storage": storage,
            "settings": effective_settings(db)}


@router.post("/sync")
def admin_sync() -> dict:
    if sync._lock.locked():  # noqa: SLF001 - sync exposes no public "busy" check
        return {"status": "busy"}
    threading.Thread(target=sync.run_sync, name="lector-sync-manual", daemon=True).start()
    return {"status": "started"}


# -- users ----------------------------------------------------------------------------
def _get_user_or_404(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return u


def _admin_count(db: Session) -> int:
    return db.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))) or 0


@router.get("/users")
def list_users(db: Session = Depends(get_db)) -> list[dict]:
    return [user_dict(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post("/users")
def add_user(body: AdminUserCreate, db: Session = Depends(get_db)) -> dict:
    return user_dict(create_user(db, body.username, body.password, is_admin=body.is_admin))


@router.put("/users/{user_id}")
def update_user(user_id: int, body: AdminUserUpdate, db: Session = Depends(get_db)) -> dict:
    u = _get_user_or_404(db, user_id)
    if body.is_admin is False and u.is_admin and _admin_count(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot demote the last admin")
    if body.is_admin is not None:
        u.is_admin = body.is_admin
    if body.password is not None:
        u.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(u)
    return user_dict(u)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, me: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    if user_id == me.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete yourself")
    db.delete(_get_user_or_404(db, user_id))
    db.commit()
    return {"ok": True}


# -- runtime settings ---------------------------------------------------------------------
@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return effective_settings(db)


@router.put("/settings")
async def put_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "default_voice" in changes:
        await validate_voice(changes["default_voice"])
    if "default_speed" in changes:
        changes["default_speed"] = round(changes["default_speed"], 2)
    for key, value in changes.items():
        set_setting(db, key, value)
    db.commit()
    return effective_settings(db)
