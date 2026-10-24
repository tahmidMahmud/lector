"""First-run setup, login/logout/registration and the signed-in user's own account."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import clear_session_cookie, current_user, hash_password, set_session_cookie, verify_password
from ..db import User, get_db
from ..schemas import Credentials, LoginBody, MeUpdate, PasswordChange
from ._helpers import APP_NAME, APP_VERSION, effective_setting, user_dict

router = APIRouter(prefix="/api", tags=["auth"])


def _user_count(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


def username_taken(db: Session, username: str) -> bool:
    return db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is not None


def create_user(db: Session, username: str, password: str, is_admin: bool) -> User:
    if username_taken(db, username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    u = User(username=username, password_hash=hash_password(password), is_admin=is_admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _signed_in(response: Response, u: User) -> dict:
    token = set_session_cookie(response, u.id)
    return {"user": user_dict(u), "token": token}


@router.get("/setup")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {"needs_setup": _user_count(db) == 0,
            "allow_registration": effective_setting(db, "allow_registration"),
            "app": APP_NAME, "version": APP_VERSION}


@router.post("/setup")
def setup(body: Credentials, response: Response, db: Session = Depends(get_db)) -> dict:
    if _user_count(db) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already complete")
    return _signed_in(response, create_user(db, body.username, body.password, is_admin=True))


@router.post("/auth/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)) -> dict:
    u = db.scalar(select(User).where(func.lower(User.username) == body.username.strip().lower()))
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    return _signed_in(response, u)


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/auth/register")
def register(body: Credentials, response: Response, db: Session = Depends(get_db)) -> dict:
    if not effective_setting(db, "allow_registration"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    return _signed_in(response, create_user(db, body.username, body.password, is_admin=False))


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return user_dict(user)


@router.put("/auth/me")
def update_me(body: MeUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "preferred_voice":
            user.preferred_voice = value or None  # "" / null clears it
        elif value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user_dict(user)


@router.put("/auth/password")
def change_password(body: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
