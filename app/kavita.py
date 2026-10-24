"""Minimal Kavita REST client (auth, libraries, series, volumes/chapters, covers, downloads).

Tested against Kavita 0.9.x. Auth is either an API key (exchanged for a JWT via
``POST /api/Plugin/authenticate``) or a username/password (``POST /api/Account/login``).
Every network or protocol failure surfaces as :class:`KavitaError` with a readable message.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("lector.kavita")

LIBRARY_FIELD = 19       # FilterField.Libraries
FILTER_EQUAL = 0         # FilterComparison.Equal
COMBINATION_AND = 1
SORT_BY_SORT_NAME = 1    # SortField.SortName

# Kavita uses sentinel numbers for "no volume" (loose leaf) / "no chapter" and for specials.
LOOSE_LEAF_NUMBER = -100000
SPECIAL_NUMBER = 100000
_SENTINEL_STRINGS = {"", "-100000", "100000", "-100000.0", "100000.0"}

TRANSIENT_STATUS = {502, 503, 504}
TOKEN_MAX_AGE = 12 * 3600  # Kavita JWTs last days; refresh proactively.


class KavitaError(RuntimeError):
    pass


class KavitaAuthError(KavitaError):
    pass


def _short(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:n]


class KavitaClient:
    def __init__(self, base_url: str, api_key: str = "", username: str = "", password: str = "",
                 plugin_name: str = "Lector", timeout: float = 60.0, connect_timeout: float = 10.0,
                 retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = api_key or ""
        self.username = username or ""
        self.password = password or ""
        self.plugin_name = plugin_name
        self.retries = max(0, retries)
        self._token: str | None = None
        self._token_time = 0.0
        self._lock = threading.Lock()
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        kw: dict[str, Any] = {"timeout": self._timeout, "follow_redirects": True,
                              "headers": {"Accept": "application/json", "User-Agent": f"{plugin_name}/1.0"}}
        if transport is not None:
            kw["transport"] = transport
        self._client = httpx.Client(**kw)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KavitaClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- low level ------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self.api_key or (self.username and self.password)))

    def _url(self, path: str) -> str:
        if not self.base_url.startswith(("http://", "https://")):
            raise KavitaError(f"KAVITA_URL must start with http:// or https:// (got {self.base_url!r})")
        return f"{self.base_url}{path}"

    def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """One HTTP call with retries on connection errors, timeouts and 502/503/504."""
        url = self._url(path)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._client.request(method, url, **kw)
            except httpx.TimeoutException as e:
                last = KavitaError(f"Kavita {method} {path} timed out ({type(e).__name__})")
            except httpx.ConnectError as e:
                last = KavitaError(f"cannot connect to Kavita at {self.base_url}: {e}")
            except httpx.HTTPError as e:
                last = KavitaError(f"Kavita {method} {path} failed: {type(e).__name__}: {e}")
            else:
                if r.status_code not in TRANSIENT_STATUS or attempt == self.retries:
                    return r
                last = KavitaError(f"Kavita {method} {path} -> HTTP {r.status_code}")
            if attempt < self.retries:
                time.sleep(0.5 * (3 ** attempt))
        assert last is not None
        raise last

    @staticmethod
    def _json(r: httpx.Response, what: str) -> Any:
        if not r.content:
            return None
        try:
            return r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise KavitaError(f"{what}: Kavita returned non-JSON ({r.headers.get('content-type', '?')}): "
                              f"{_short(r.text, 120)}") from e

    # -- auth ---------------------------------------------------------------
    def _login(self) -> str:
        if self.api_key:
            r = self._send("POST", "/api/Plugin/authenticate",
                           params={"apiKey": self.api_key, "pluginName": self.plugin_name})
            if r.status_code in (401, 403):
                raise KavitaAuthError(f"Kavita rejected the API key (HTTP {r.status_code}); check KAVITA_API_KEY")
            if r.status_code != 200:
                raise KavitaAuthError(f"Kavita API key login failed: HTTP {r.status_code} {_short(r.text, 200)}")
            data = self._json(r, "API key login") or {}
        elif self.username and self.password:
            r = self._send("POST", "/api/Account/login",
                           json={"username": self.username, "password": self.password, "apiKey": ""})
            if r.status_code in (400, 401, 403):
                raise KavitaAuthError(f"Kavita rejected the username/password (HTTP {r.status_code}): "
                                      f"{_short(r.text, 200)}")
            if r.status_code != 200:
                raise KavitaAuthError(f"Kavita password login failed: HTTP {r.status_code} {_short(r.text, 200)}")
            data = self._json(r, "password login") or {}
            if not self.api_key and data.get("apiKey"):
                self.api_key = data["apiKey"]  # lets us fetch covers with ?apiKey=
        else:
            raise KavitaError("Kavita is not configured (set KAVITA_API_KEY or KAVITA_USERNAME/KAVITA_PASSWORD)")
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise KavitaAuthError("Kavita login returned no token")
        self._token = token
        self._token_time = time.time()
        return token

    def token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.time() - self._token_time > TOKEN_MAX_AGE:
                self._login()
            return self._token  # type: ignore[return-value]

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = dict(kw.pop("headers", None) or {})
        for attempt in (0, 1):
            headers["Authorization"] = f"Bearer {self.token(force=attempt == 1)}"
            r = self._send(method, path, headers=headers, **kw)
            if r.status_code == 401 and attempt == 0:
                continue  # token expired or revoked: log in again once
            if r.status_code >= 400:
                raise KavitaError(f"Kavita {method} {path} -> HTTP {r.status_code}: {_short(r.text)}")
            return r
        raise KavitaError(f"Kavita {method} {path} -> HTTP 401 after re-login")  # pragma: no cover

    def get(self, path: str, **kw: Any) -> Any:
        return self._json(self._request("GET", path, **kw), f"GET {path}")

    def post(self, path: str, **kw: Any) -> Any:
        r = self._request("POST", path, **kw)
        if r.content and r.headers.get("content-type", "").startswith("application/json"):
            return self._json(r, f"POST {path}")
        return None

    # -- read API -------------------------------------------------------------
    def health(self) -> bool:
        try:
            return self._client.get(self._url("/api/health"), timeout=5).status_code == 200
        except (httpx.HTTPError, KavitaError):
            return False

    def server_info(self) -> dict:
        try:
            return self.get("/api/Server/server-info-slim") or {}
        except KavitaError:
            return {}

    def libraries(self) -> list[dict]:
        return self.get("/api/Library/libraries") or []

    def series_in_library(self, library_id: int, page_size: int = 100) -> list[dict]:
        """All series of a library, following Kavita's ``Pagination`` response header."""
        body = {
            "statements": [{"comparison": FILTER_EQUAL, "field": LIBRARY_FIELD, "value": str(library_id)}],
            "combination": COMBINATION_AND,
            "sortOptions": {"sortField": SORT_BY_SORT_NAME, "isAscending": True},
            "limitTo": 0,
        }
        out: list[dict] = []
        seen: set[int] = set()
        page = 1
        while page <= 10_000:
            r = self._request("POST", "/api/Series/all-v2", params={"pageNumber": page, "pageSize": page_size},
                              json=body)
            items = self._json(r, "series list") or []
            if not isinstance(items, list):
                raise KavitaError(f"series list: unexpected response {type(items).__name__}")
            for it in items:
                if it.get("id") not in seen:  # guard against overlap if the library changes mid-listing
                    seen.add(it.get("id"))
                    out.append(it)
            total_pages = parse_pagination(r.headers.get("Pagination")).get("totalPages")
            if total_pages is not None:
                if page >= int(total_pages) or not items:
                    break
            elif len(items) < page_size:  # old Kavita without the header
                break
            page += 1
        return out

    def series(self, series_id: int) -> dict:
        return self.get(f"/api/Series/{series_id}") or {}

    def series_metadata(self, series_id: int) -> dict:
        return self.get("/api/Series/metadata", params={"seriesId": series_id}) or {}

    def volumes(self, series_id: int) -> list[dict]:
        return self.get("/api/Series/volumes", params={"seriesId": series_id}) or []

    def _image(self, path: str, params: dict) -> bytes | None:
        params = dict(params)
        params["apiKey"] = self.api_key or ""
        try:
            r = self._request("GET", path, params=params)
        except KavitaError as e:
            log.debug("image %s %s failed: %s", path, params.get("seriesId") or params.get("chapterId"), e)
            return None
        if r.content and r.headers.get("content-type", "").startswith("image/"):
            return r.content
        return None

    def cover_bytes(self, series_id: int) -> bytes | None:
        return self._image("/api/Image/series-cover", {"seriesId": series_id})

    def chapter_cover_bytes(self, chapter_id: int) -> bytes | None:
        return self._image("/api/Image/chapter-cover", {"chapterId": chapter_id})

    def download_chapter(self, chapter_id: int, dest: Path) -> Path:
        """Stream a chapter's file to ``dest`` (atomically, via a .part file)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        url = self._url("/api/Download/chapter")
        try:
            for attempt in (0, 1):
                headers = {"Authorization": f"Bearer {self.token(force=attempt == 1)}"}
                with self._client.stream("GET", url, params={"chapterId": chapter_id}, headers=headers,
                                         timeout=httpx.Timeout(300.0, connect=self._timeout.connect)) as r:
                    if r.status_code == 401 and attempt == 0:
                        continue
                    if r.status_code >= 400:
                        r.read()
                        hint = " (the Kavita user needs the Download role)" if r.status_code in (401, 403) else ""
                        raise KavitaError(f"download chapter {chapter_id}: HTTP {r.status_code}{hint}: "
                                          f"{_short(r.text, 200)}")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                    break
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise KavitaError(f"download chapter {chapter_id}: empty response")
            tmp.replace(dest)
            return dest
        except httpx.HTTPError as e:
            raise KavitaError(f"download chapter {chapter_id} failed: {type(e).__name__}: {e}") from e
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # -- write back (optional) ------------------------------------------------
    def mark_series_read(self, series_id: int) -> None:
        try:
            self.post("/api/Reader/mark-read", json={"seriesId": series_id})
        except KavitaError as e:
            log.warning("mark-read failed: %s", e)


def parse_pagination(raw: str | None) -> dict:
    """Parse Kavita's ``Pagination: {"currentPage":1,"itemsPerPage":..,"totalItems":..,"totalPages":..}``."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Volumes/chapters -> narratable files
# ---------------------------------------------------------------------------
def is_sentinel(value: Any) -> bool:
    """True for Kavita's placeholder titles/numbers ("-100000", "100000", empty)."""
    if value is None:
        return True
    s = str(value).strip()
    if s in _SENTINEL_STRINGS:
        return True
    try:
        return abs(float(s)) == SPECIAL_NUMBER
    except ValueError:
        return False


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if abs(f) == SPECIAL_NUMBER else f


def _fmt_num(f: float) -> str:
    return str(int(f)) if f == int(f) else f"{f:g}"


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.casefold())


_SEP_CHARS = " \t-—–:,;|/·."


def _strip_series_prefix(title: str, series_name: str) -> str:
    """"The Works of Poe — Volume 1" with series "The Works of Poe" -> "Volume 1"."""
    if series_name and title.casefold().startswith(series_name.casefold()):
        rest = title[len(series_name):]
        if rest[:1] in tuple(_SEP_CHARS) + ("(",):
            rest = rest.lstrip(_SEP_CHARS).strip()
            if rest.startswith("(") and rest.endswith(")"):
                rest = rest[1:-1].strip()
            if rest:
                return rest
    return title


def chapter_title(ch: dict, series_name: str = "") -> str | None:
    """A meaningful chapter title, ignoring sentinels and the series name itself."""
    for cand in (ch.get("titleName"), ch.get("title"), ch.get("range")):
        if is_sentinel(cand):
            continue
        t = str(cand).strip()
        if series_name and _key(t) == _key(series_name):
            continue
        t = _strip_series_prefix(t, series_name)
        if t and not is_sentinel(t):
            return t
    return None


def volume_label(vol: dict, ch: dict, series_name: str = "") -> str | None:
    """Label for one file of a multi-file series: "Volume 2", a named volume, or the chapter title."""
    name = vol.get("name")
    if not is_sentinel(name):
        n = str(name).strip()
        num = _num(n)
        if num is not None:
            return f"Volume {_fmt_num(num)}"
        if not series_name or _key(n) != _key(series_name):
            return n
    return chapter_title(ch, series_name)


def _volume_sort_key(vol: dict) -> tuple:
    num = _num(vol.get("minNumber", vol.get("number")))
    raw = vol.get("minNumber", vol.get("number"))
    try:
        rawf = float(raw)
    except (TypeError, ValueError):
        rawf = 0.0
    if num is not None and num != LOOSE_LEAF_NUMBER:
        group = 0          # numbered volumes first
    elif rawf == SPECIAL_NUMBER:
        group = 2          # specials last
    else:
        group = 1          # loose-leaf chapters
    return (group, num if group == 0 and num is not None else 0.0, vol.get("id") or 0)


def _chapter_sort_key(ch: dict) -> tuple:
    num = _num(ch.get("minNumber"))
    has_num = num is not None and num != LOOSE_LEAF_NUMBER
    sort_order = _num(ch.get("sortOrder"))
    return (bool(ch.get("isSpecial")), 0 if has_num else 1, num if has_num else 0.0,
            sort_order if sort_order is not None and sort_order != LOOSE_LEAF_NUMBER else 0.0,
            ch.get("id") or 0)


def file_entries(volumes: list[dict], series_name: str = "") -> list[dict]:
    """Flatten Kavita volumes into one entry per chapter (= one narratable file), in reading order.

    Each entry: ``chapter_id, volume_id, file_path, format, pages, bytes, title, label,
    volume_number, word_count, is_special, created``. ``label`` is what to call this file
    when the series has several (``"Volume 1"``, a named volume, or the chapter title);
    it is ``None`` when nothing meaningful is known, and labels are de-duplicated.
    """
    out: list[dict] = []
    for vol in sorted(volumes or [], key=_volume_sort_key):
        vnum = _num(vol.get("minNumber", vol.get("number")))
        if vnum == LOOSE_LEAF_NUMBER:
            vnum = None
        if vnum is None and not is_sentinel(vol.get("name")):
            vnum = _num(vol.get("name"))
        for ch in sorted(vol.get("chapters") or [], key=_chapter_sort_key):
            files = sorted((f for f in (ch.get("files") or []) if f.get("filePath")),
                           key=lambda f: f.get("filePath") or "")
            if not files:
                continue
            if len(files) > 1:
                log.info("chapter %s has %d files; using %s", ch.get("id"), len(files), files[0]["filePath"])
            f = files[0]
            out.append({
                "chapter_id": ch.get("id"), "volume_id": vol.get("id") or ch.get("volumeId"),
                "file_path": f.get("filePath"), "format": f.get("format"),
                "pages": f.get("pages") or ch.get("pages"), "bytes": f.get("bytes"),
                "title": chapter_title(ch, series_name),
                "label": volume_label(vol, ch, series_name),
                "volume_number": vnum,
                "word_count": ch.get("wordCount") or None,
                "is_special": bool(ch.get("isSpecial")),
                "created": ch.get("created") or f.get("created"),
            })
    # Make labels unique ("Volume 1" twice -> use the chapter title, else "Volume 1, part 2").
    counts: dict[str, int] = {}
    for e in out:
        if e["label"]:
            counts[e["label"]] = counts.get(e["label"], 0) + 1
    seen: dict[str, int] = {}
    for e in out:
        lab = e["label"]
        if lab and counts[lab] > 1:
            seen[lab] = seen.get(lab, 0) + 1
            t = e["title"]
            e["label"] = f"{lab}: {t}" if t and _key(t) != _key(lab) else f"{lab}, part {seen[lab]}"
    return out


# Kavita MangaFormat enum
FORMAT_NAMES = {0: "image", 1: "archive", 2: "unknown", 3: "epub", 4: "pdf"}
