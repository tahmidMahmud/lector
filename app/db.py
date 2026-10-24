"""SQLAlchemy models and session helpers (SQLite)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    # Preferences
    playback_rate = Column(Float, default=1.0, nullable=False)
    skip_back_seconds = Column(Integer, default=15, nullable=False)
    skip_forward_seconds = Column(Integer, default=30, nullable=False)
    preferred_voice = Column(String(64), nullable=True)
    theme = Column(String(16), default="dark", nullable=False)


class Book(Base):
    """One narratable file (a Kavita chapter/volume) inside a Kavita series.

    Most books are one series with one file. Multi-volume works (e.g. "The Works
    of Poe" vol. 1 & 2) produce one Book row per volume, grouped by series_id.
    """
    __tablename__ = "books"
    id = Column(Integer, primary_key=True)
    kavita_series_id = Column(Integer, nullable=False, index=True)
    kavita_library_id = Column(Integer, nullable=False)
    kavita_volume_id = Column(Integer, nullable=True)
    kavita_chapter_id = Column(Integer, unique=True, nullable=False, index=True)
    series_title = Column(String(512), nullable=False)
    volume_label = Column(String(128), nullable=True)   # "Volume 1" when the series has several files
    volume_number = Column(Float, nullable=True)
    title = Column(String(512), nullable=False)
    sort_title = Column(String(512), nullable=True)
    authors = Column(String(512), nullable=True)
    summary = Column(Text, nullable=True)
    file_path = Column(Text, nullable=True)      # path as Kavita reports it
    local_path = Column(Text, nullable=True)     # mapped path inside this container
    file_format = Column(String(16), nullable=True)
    word_count = Column(Integer, nullable=True)
    is_gutenberg = Column(Boolean, default=False, nullable=False)
    gutenberg_id = Column(String(32), nullable=True)
    cover_cached = Column(Boolean, default=False, nullable=False)
    kavita_updated = Column(String(64), nullable=True)
    missing = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    last_synced = Column(DateTime, default=utcnow, nullable=False)

    renditions = relationship("Rendition", back_populates="book", cascade="all, delete-orphan")


class Rendition(Base):
    """One generated audiobook (book x voice x speed)."""
    __tablename__ = "renditions"
    __table_args__ = (UniqueConstraint("book_id", "voice", "speed", name="uq_rendition"),)
    id = Column(Integer, primary_key=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True)
    voice = Column(String(64), nullable=False)
    speed = Column(Float, default=1.0, nullable=False)
    engine = Column(String(32), nullable=False)
    status = Column(String(16), default="queued", nullable=False)  # queued|running|paused|done|error|cancelled
    error = Column(Text, nullable=True)
    progress = Column(Float, default=0.0, nullable=False)  # 0..1 over all chapters
    total_chapters = Column(Integer, default=0, nullable=False)
    done_chapters = Column(Integer, default=0, nullable=False)
    total_duration = Column(Float, default=0.0, nullable=False)
    requested_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    m4b_path = Column(Text, nullable=True)

    book = relationship("Book", back_populates="renditions")
    chapters = relationship("Chapter", back_populates="rendition", cascade="all, delete-orphan",
                            order_by="Chapter.index")


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("rendition_id", "index", name="uq_chapter"),)
    id = Column(Integer, primary_key=True)
    rendition_id = Column(Integer, ForeignKey("renditions.id", ondelete="CASCADE"), nullable=False, index=True)
    index = Column(Integer, nullable=False)
    title = Column(String(512), nullable=False)
    status = Column(String(16), default="pending", nullable=False)  # pending|running|done|error|skipped
    char_count = Column(Integer, default=0, nullable=False)
    duration = Column(Float, default=0.0, nullable=False)
    file_path = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    text_hash = Column(String(64), nullable=True)

    rendition = relationship("Rendition", back_populates="chapters")


class Progress(Base):
    """Where a user is in a rendition."""
    __tablename__ = "progress"
    __table_args__ = (UniqueConstraint("user_id", "rendition_id", name="uq_progress"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    rendition_id = Column(Integer, ForeignKey("renditions.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_index = Column(Integer, default=0, nullable=False)
    position = Column(Float, default=0.0, nullable=False)  # seconds within chapter
    finished = Column(Boolean, default=False, nullable=False)
    playback_rate = Column(Float, nullable=True)  # per-book override
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    started_at = Column(DateTime, default=utcnow, nullable=False)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    rendition_id = Column(Integer, ForeignKey("renditions.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_index = Column(Integer, nullable=False)
    position = Column(Float, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Setting(Base):
    """Runtime-editable settings (admin UI), key/value."""
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)


class SyncLog(Base):
    __tablename__ = "sync_log"
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(16), default="running", nullable=False)
    message = Column(Text, nullable=True)
    books_seen = Column(Integer, default=0, nullable=False)


_engine = None
SessionLocal: sessionmaker | None = None


def init_db() -> None:
    global _engine, SessionLocal
    settings.ensure_dirs()
    _engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    Base.metadata.create_all(_engine)
    SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    assert SessionLocal is not None, "init_db() not called"
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    assert SessionLocal is not None, "init_db() not called"
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
