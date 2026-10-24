"""Minimal Kavita REST client (auth, libraries, series, volumes/chapters, covers, downloads)."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("lector.kavita")

LIBRARY_FIELD = 19       # FilterField.Libraries
FILTER_EQUAL = 0         # FilterComparison.Equal
COMBINATION_AND = 1


class KavitaError(RuntimeError):
    pass


class KavitaClient:
    def __init__(self, base_url: str, api_key: str = "", username: str = "", password: str = "",
                 plugin_name: str = "Lector") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.username = username
        self.password = password
        self.plugin_name = plugin_name
        self._token: str | None = None
        self._token_time = 0.0
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True)

    # -- auth ---------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self.api_key or (self.username and self.password)))

    def _login(self) -> str:
        if self.api_key:
            r = self._client.post(f"{self.base_url}/api/Plugin/authenticate",
                                  params={"apiKey": self.api_key, "pluginName": self.plugin_name})
            if r.status_code != 200:
                raise KavitaError(f"Kavita API key login failed: HTTP {r.status_code} {r.text[:200]}")
            data = r.json()
        elif self.username and self.password:
            r = self._client.post(f"{self.base_url}/api/Account/login",
                                  json={"username": self.username, "password": self.password, "apiKey": ""})
            if r.status_code != 200:
                raise KavitaError(f"Kavita password login failed: HTTP {r.status_code} {r.text[:200]}")
            data = r.json()
            if not self.api_key and data.get("apiKey"):
                self.api_key = data["apiKey"]  # lets us fetch covers without a bearer token
        else:
            raise KavitaError("Kavita is not configured (set KAVITA_API_KEY or KAVITA_USERNAME/KAVITA_PASSWORD)")
        token = data.get("token")
        if not token:
            raise KavitaError("Kavita login returned no token")
        self._token = token
        self._token_time = time.time()
        return token

    def token(self, force: bool = False) -> str:
        with self._lock:
            # Kavita tokens last days; refresh proactively every 12 h.
            if force or not self._token or time.time() - self._token_time > 12 * 3600:
                self._login()
            return self._token  # type: ignore[return-value]

    def _request(self, method: str, path: str, retry: bool = True, **kw: Any) -> httpx.Response:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token()}"
        r = self._client.request(method, f"{self.base_url}{path}", headers=headers, **kw)
        if r.status_code == 401 and retry:
            self.token(force=True)
            return self._request(method, path, retry=False, headers=headers, **kw)
        if r.status_code >= 400:
            raise KavitaError(f"Kavita {method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r

    def get(self, path: str, **kw: Any) -> Any:
        r = self._request("GET", path, **kw)
        return r.json() if r.content else None

    def post(self, path: str, **kw: Any) -> Any:
        r = self._request("POST", path, **kw)
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    # -- read API -------------------------------------------------------------
    def health(self) -> bool:
        try:
            return self._client.get(f"{self.base_url}/api/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    def server_info(self) -> dict:
        try:
            return self.get("/api/Server/server-info-slim") or {}
        except KavitaError:
            return {}

    def libraries(self) -> list[dict]:
        return self.get("/api/Library/libraries") or []

    def series_in_library(self, library_id: int, page_size: int = 100) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            body = {
                "statements": [{"comparison": FILTER_EQUAL, "field": LIBRARY_FIELD, "value": str(library_id)}],
                "combination": COMBINATION_AND,
                "sortOptions": {"sortField": 1, "isAscending": True},
                "limitTo": 0,
            }
            r = self._request("POST", "/api/Series/all-v2", params={"pageNumber": page, "pageSize": page_size},
                              json=body)
            items = r.json() or []
            out.extend(items)
            if len(items) < page_size:
                break
            page += 1
            if page > 1000:
                break
        return out

    def series(self, series_id: int) -> dict:
        return self.get(f"/api/Series/{series_id}")

    def series_metadata(self, series_id: int) -> dict:
        return self.get("/api/Series/metadata", params={"seriesId": series_id}) or {}

    def volumes(self, series_id: int) -> list[dict]:
        return self.get("/api/Series/volumes", params={"seriesId": series_id}) or []

    def cover_bytes(self, series_id: int) -> bytes | None:
        try:
            r = self._request("GET", "/api/Image/series-cover",
                              params={"seriesId": series_id, "apiKey": self.api_key or ""})
            if r.content and r.headers.get("content-type", "").startswith("image/"):
                return r.content
        except KavitaError as e:
            log.debug("cover fetch failed: %s", e)
        return None

    def download_chapter(self, chapter_id: int, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self._client.stream("GET", f"{self.base_url}/api/Download/chapter",
                                 params={"chapterId": chapter_id},
                                 headers={"Authorization": f"Bearer {self.token()}"}) as r:
            if r.status_code >= 400:
                raise KavitaError(f"download chapter {chapter_id}: HTTP {r.status_code}")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.rename(dest)
        return dest

    # -- write back (optional) ------------------------------------------------
    def mark_series_read(self, series_id: int) -> None:
        try:
            self.post("/api/Reader/mark-read", json={"seriesId": series_id})
        except KavitaError as e:
            log.warning("mark-read failed: %s", e)


def file_entries(volumes: list[dict]) -> list[dict]:
    """Flatten Kavita volumes -> [{chapter_id, volume_id, file_path, format, pages, title}] in reading order."""
    out: list[dict] = []
    for vol in sorted(volumes, key=lambda v: (v.get("minNumber", v.get("number", 0)) or 0)):
        for ch in sorted(vol.get("chapters", []), key=lambda c: (c.get("minNumber", 0) or 0)):
            for f in ch.get("files", []):
                out.append({
                    "chapter_id": ch.get("id"), "volume_id": vol.get("id"),
                    "file_path": f.get("filePath"), "format": f.get("format"),
                    "pages": f.get("pages"), "title": ch.get("titleName") or ch.get("title") or vol.get("name"),
                    "created": ch.get("created") or f.get("created"),
                })
    return out


# Kavita MangaFormat enum
FORMAT_NAMES = {0: "image", 1: "archive", 2: "unknown", 3: "epub", 4: "pdf"}
