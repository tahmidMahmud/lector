"""Pydantic request bodies for the HTTP API (responses are plain dicts, see routers/_helpers.py)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]{2,64}$"
SPEED_MIN, SPEED_MAX = 0.5, 2.0


def _check_password(pw: str) -> str:
    if len(pw) < 6:
        raise ValueError("password must be at least 6 characters")
    if len(pw.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return pw


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Credentials(_Body):
    """Setup / register: enforces the username and password rules."""
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _check_password(v)


class LoginBody(_Body):
    """Login: no format rules (a bad value is simply a 401)."""
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


class MeUpdate(_Body):
    playback_rate: float | None = Field(default=None, ge=0.5, le=3.0)
    skip_back_seconds: int | None = Field(default=None, ge=1, le=600)
    skip_forward_seconds: int | None = Field(default=None, ge=1, le=600)
    preferred_voice: str | None = Field(default=None, max_length=64)
    theme: Literal["dark", "light", "auto"] | None = None


class PasswordChange(_Body):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _check_password(v)


class RenditionCreate(_Body):
    voice: str = Field(min_length=1, max_length=64)
    speed: float | None = Field(default=None, ge=SPEED_MIN, le=SPEED_MAX)


class ProgressUpdate(_Body):
    chapter_index: int = Field(ge=0)
    position: float = Field(ge=0)
    finished: bool | None = None
    playback_rate: float | None = Field(default=None, ge=0.5, le=3.0)


class BookmarkCreate(_Body):
    chapter_index: int = Field(ge=0)
    position: float = Field(ge=0)
    note: str | None = Field(default=None, max_length=4000)


class BookmarkUpdate(_Body):
    note: str | None = Field(default=None, max_length=4000)


class AdminUserCreate(Credentials):
    is_admin: bool = False


class AdminUserUpdate(_Body):
    password: str | None = None
    is_admin: bool | None = None

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return None if v is None else _check_password(v)


class SettingsUpdate(_Body):
    default_voice: str | None = Field(default=None, min_length=1, max_length=64)
    default_speed: float | None = Field(default=None, ge=SPEED_MIN, le=SPEED_MAX)
    allow_user_requests: bool | None = None
    allow_registration: bool | None = None
