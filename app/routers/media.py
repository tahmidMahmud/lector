"""Chapter audio (with HTTP Range), read-along timelines, and M4B export."""
from __future__ import annotations

import json
import logging
import re
import threading
from email.utils import formatdate
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import Chapter, User, get_db
from ..worker import rendition_dir, worker
from ._helpers import get_rendition_or_404, has_m4b, require_requester

log = logging.getLogger("lector.api")
router = APIRouter(prefix="/api", tags=["media"])

CHUNK_SIZE = 64 * 1024
RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.IGNORECASE)
MEDIA_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp4": "audio/mp4"}

_m4b_building: set[int] = set()
_m4b_state_lock = threading.Lock()


# -- range helpers ---------------------------------------------------------------------
def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single ``bytes=`` range into inclusive (start, end). None = whole file.

    Raises 416 for unsatisfiable ranges. Only the first range of a multi-range request is honoured.
    """
    if not header:
        return None
    m = RANGE_RE.match(header.split(",", 1)[0])
    if not m or (not m.group(1) and not m.group(2)):
        return None  # malformed -> ignore, serve the full body (RFC 9110 allows this)
    first, last = m.group(1), m.group(2)
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:  # suffix range: last N bytes
        n = int(last)
        if n == 0:
            raise _unsatisfiable(size)
        start, end = max(0, size - n), size - 1
    if start >= size or start > end:
        raise _unsatisfiable(size)
    return start, end


def _unsatisfiable(size: int) -> HTTPException:
    return HTTPException(status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, "Requested range not satisfiable",
                         headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})


def iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            data = f.read(min(CHUNK_SIZE, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def ranged_file_response(request: Request, path: Path, media_type: str) -> Response:
    st = path.stat()
    size = st.st_size
    headers = {"Accept-Ranges": "bytes", "Last-Modified": formatdate(st.st_mtime, usegmt=True),
               "Cache-Control": "private, max-age=3600"}
    rng = parse_range(request.headers.get("range"), size) if size > 0 else None
    if rng is None:
        start, end, code = 0, size - 1, status.HTTP_200_OK
    else:
        (start, end), code = rng, status.HTTP_206_PARTIAL_CONTENT
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return Response(status_code=code, headers=headers, media_type=media_type)
    return StreamingResponse(iter_file(path, start, length), status_code=code, headers=headers,
                             media_type=media_type)


def media_type_for(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower()) or MEDIA_TYPES.get(f".{settings.audio_format}", "audio/mpeg")


# -- chapter audio / timeline ------------------------------------------------------------
def _chapter(db: Session, rid: int, index: int) -> Chapter:
    get_rendition_or_404(db, rid)
    c = db.scalar(select(Chapter).where(Chapter.rendition_id == rid, Chapter.index == index))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chapter not found")
    return c


@router.api_route("/renditions/{rid}/chapters/{index}/audio", methods=["GET", "HEAD"])
def chapter_audio(rid: int, index: int, request: Request, _: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> Response:
    c = _chapter(db, rid, index)
    if c.status != "done" or not c.file_path or not Path(c.file_path).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chapter audio not available yet")
    path = Path(c.file_path)
    return ranged_file_response(request, path, media_type_for(path))


def _timeline_path(c: Chapter, book_id: int) -> Path:
    if c.file_path:
        return Path(c.file_path).with_suffix(".json")
    return rendition_dir(book_id, c.rendition_id) / f"{c.index:03d}.json"


@router.get("/renditions/{rid}/chapters/{index}/timeline")
def chapter_timeline(rid: int, index: int, _: User = Depends(current_user), db: Session = Depends(get_db)) -> list:
    c = _chapter(db, rid, index)
    p = _timeline_path(c, c.rendition.book_id)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("bad timeline file %s", p)
        return []
    return data if isinstance(data, list) else []


# -- m4b -------------------------------------------------------------------------------
def _m4b_up_to_date(r) -> bool:  # noqa: ANN001 - Rendition
    if not has_m4b(r):
        return False
    built = Path(r.m4b_path).stat().st_mtime
    files = [Path(c.file_path) for c in r.chapters if c.status == "done" and c.file_path]
    return all(not f.exists() or f.stat().st_mtime <= built for f in files)


def _build_m4b_thread(rid: int) -> None:
    try:
        worker.build_m4b(rid)
        log.info("m4b built for rendition %s", rid)
    except Exception:  # noqa: BLE001
        log.exception("m4b build failed for rendition %s", rid)
    finally:
        with _m4b_state_lock:
            _m4b_building.discard(rid)


@router.post("/renditions/{rid}/m4b")
def build_m4b(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_requester(db, user)
    r = get_rendition_or_404(db, rid)
    if not any(c.status == "done" and c.file_path for c in r.chapters):
        raise HTTPException(status.HTTP_409_CONFLICT, "No narrated chapters yet")
    with _m4b_state_lock:
        if rid in _m4b_building:
            return {"status": "building"}
        if _m4b_up_to_date(r):
            return {"status": "ready"}
        _m4b_building.add(rid)
    threading.Thread(target=_build_m4b_thread, args=(rid,), name=f"lector-m4b-{rid}", daemon=True).start()
    return {"status": "building"}


@router.get("/renditions/{rid}/m4b")
def download_m4b(rid: int, _: User = Depends(current_user), db: Session = Depends(get_db)) -> FileResponse:
    r = get_rendition_or_404(db, rid)
    if not has_m4b(r):  # a rebuild replaces the file atomically, so an old copy stays valid
        raise HTTPException(status.HTTP_404_NOT_FOUND, "M4B not built yet")
    title = re.sub(r'[\\/:*?"<>|\r\n]+', " ", r.book.title).strip() or "audiobook"
    return FileResponse(r.m4b_path, media_type="audio/mp4", filename=f"{title}.m4b")
