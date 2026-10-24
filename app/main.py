"""FastAPI application: API routers, static files, SPA fallback, background services."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import admin, auth, library, media, progress
from .routers._helpers import APP_NAME, APP_VERSION
from .sync import scheduler
from .worker import worker

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

PLACEHOLDER_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Lector</title></head>
<body style="font-family:sans-serif;padding:2rem"><h1>Lector</h1><p>Lector UI not built.</p></body></html>"""

logging.basicConfig(level=settings.log_level,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lector")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.ensure_dirs()
    init_db()
    worker.start()
    scheduler.start()
    log.info("Lector %s started (data dir %s)", APP_VERSION, settings.data_dir)
    try:
        yield
    finally:
        worker.stop(timeout=20)
        scheduler.stop()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


def _format_validation_error(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "Invalid request"


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": _format_validation_error(exc)}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


for module in (auth, library, media, progress, admin):
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str) -> None:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _static_file(name: str, media_type: str) -> Response:
    p = STATIC_DIR / name
    if not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(p, media_type=media_type, headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    return _static_file("manifest.webmanifest", "application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> Response:
    resp = _static_file("sw.js", "application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> Response:
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})
    return HTMLResponse(PLACEHOLDER_HTML, headers={"Cache-Control": "no-cache"})
