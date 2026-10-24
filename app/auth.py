"""Password hashing, session cookies, and FastAPI auth dependencies."""
from __future__ import annotations

import bcrypt
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .config import settings
from .db import User, get_db

COOKIE_NAME = "lector_session"
_serializer: URLSafeTimedSerializer | None = None


def _ser() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(settings.load_or_create_secret(), salt="lector-session")
    return _serializer


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def make_token(user_id: int) -> str:
    return _ser().dumps({"uid": user_id})


def read_token(token: str) -> int | None:
    try:
        data = _ser().loads(token, max_age=settings.session_days * 86400)
    except BadSignature:
        return None
    return data.get("uid")


def set_session_cookie(response: Response, user_id: int) -> str:
    token = make_token(user_id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=settings.session_days * 86400, httponly=True,
        samesite="lax", secure=settings.cookie_secure, path="/",
    )
    return token


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    tok = request.cookies.get(COOKIE_NAME)
    if tok:
        return tok
    # Media elements can't send headers; allow ?token= for audio/cover URLs.
    return request.query_params.get("token")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    tok = _token_from_request(request)
    if not tok:
        return None
    uid = read_token(tok)
    if uid is None:
        return None
    return db.get(User, uid)


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
