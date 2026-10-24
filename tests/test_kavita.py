"""Kavita client: volume/chapter flattening, labels, pagination, auth and error handling."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LECTOR_DATA_DIR", tempfile.mkdtemp(prefix="lector-test-"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import kavita as K  # noqa: E402
from app.kavita import KavitaAuthError, KavitaClient, KavitaError, file_entries, is_sentinel  # noqa: E402


def chapter(cid, file_path, title="-100000", title_name="", rng="-100000", min_number=-100000,
            is_special=False, sort_order=-100000, files=None, word_count=0):
    return {
        "id": cid, "title": title, "titleName": title_name, "range": rng, "minNumber": min_number,
        "number": str(min_number), "isSpecial": is_special, "sortOrder": sort_order, "wordCount": word_count,
        "files": files if files is not None else [{"filePath": file_path, "format": 3, "pages": 10, "bytes": 1234}],
    }


def volume(vid, name, min_number, chapters):
    return {"id": vid, "name": name, "number": min_number, "minNumber": min_number, "maxNumber": min_number,
            "chapters": chapters}


# The exact shape Kavita 0.9.1 returns for a two-file book series (metadata off).
POE_VOLUMES = [
    volume(2, "2", 2, [chapter(2, "/books/Poe/Vol 2.epub")]),
    volume(1, "1", 1, [chapter(1, "/books/Poe/Vol 1.epub")]),
]


# ---------------------------------------------------------------------------
# file_entries / labels
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value, expected", [
    ("-100000", True), ("100000", True), ("", True), (None, True), (-100000, True), (100000.0, True),
    ("1", False), ("Volume One", False), (2, False), ("The Works", False),
])
def test_is_sentinel(value, expected):
    assert is_sentinel(value) is expected


def test_sentinel_titles_never_become_labels():
    entries = file_entries(POE_VOLUMES, "The Works of Edgar Allan Poe")
    assert [e["label"] for e in entries] == ["Volume 1", "Volume 2"]
    assert all(e["title"] is None for e in entries)
    assert all("-100000" not in (e["label"] or "") for e in entries)


def test_multi_volume_ordering_by_number_not_api_order():
    vols = [
        volume(30, "10", 10, [chapter(30, "/b/10.epub")]),
        volume(10, "2", 2, [chapter(10, "/b/2.epub")]),
        volume(20, "1", 1, [chapter(20, "/b/1.epub")]),
        volume(40, "1.5", 1.5, [chapter(40, "/b/1.5.epub")]),
    ]
    entries = file_entries(vols, "S")
    assert [e["chapter_id"] for e in entries] == [20, 40, 10, 30]
    assert [e["label"] for e in entries] == ["Volume 1", "Volume 1.5", "Volume 2", "Volume 10"]
    assert [e["volume_number"] for e in entries] == [1.0, 1.5, 2.0, 10.0]
    assert [e["volume_id"] for e in entries] == [20, 40, 10, 30]


def test_entry_fields():
    e = file_entries(POE_VOLUMES, "Poe")[0]
    assert e["chapter_id"] == 1 and e["volume_id"] == 1
    assert e["file_path"] == "/books/Poe/Vol 1.epub"
    assert e["format"] == 3 and e["pages"] == 10 and e["bytes"] == 1234
    assert e["is_special"] is False


def test_single_book_loose_leaf_volume():
    # A single EPUB with metadata on: volume "-100000", chapter title == series name.
    vols = [volume(6, "-100000", -100000, [
        chapter(6, "/books/Dickens/A Tale of Two Cities.epub", title="A Tale of Two Cities",
                title_name="A Tale of Two Cities", rng="A Tale of Two Cities", is_special=True)])]
    (e,) = file_entries(vols, "A Tale of Two Cities")
    assert e["label"] is None and e["title"] is None
    assert e["volume_number"] is None


def test_chapter_title_prefixed_with_series_name_is_stripped():
    vols = [volume(5, "-100000", -100000, [
        chapter(1, "/b/1.epub", title_name="The Works of Poe — Part One", min_number=1, sort_order=1),
        chapter(2, "/b/2.epub", title_name="The Works of Poe: Part Two", min_number=2, sort_order=2),
    ])]
    assert [e["label"] for e in file_entries(vols, "The Works of Poe")] == ["Part One", "Part Two"]


def test_named_volume_used_verbatim():
    vols = [volume(1, "The Early Years", 1, [chapter(1, "/b/a.epub")]),
            volume(2, "The Later Years", 2, [chapter(2, "/b/b.epub")])]
    assert [e["label"] for e in file_entries(vols, "Memoirs")] == ["The Early Years", "The Later Years"]


def test_numbered_volumes_before_loose_leaf_before_specials():
    vols = [
        volume(9, "100000", 100000, [chapter(90, "/b/special.epub", title_name="Bonus Story", is_special=True)]),
        volume(8, "-100000", -100000, [chapter(80, "/b/loose.epub", title_name="Loose Chapter", min_number=3,
                                               sort_order=3)]),
        volume(1, "1", 1, [chapter(10, "/b/v1.epub")]),
    ]
    entries = file_entries(vols, "S")
    assert [e["chapter_id"] for e in entries] == [10, 80, 90]
    assert [e["label"] for e in entries] == ["Volume 1", "Loose Chapter", "Bonus Story"]


def test_duplicate_labels_are_disambiguated():
    vols = [volume(1, "1", 1, [
        chapter(1, "/b/1a.epub", title_name="Prologue", min_number=1, sort_order=1),
        chapter(2, "/b/1b.epub", min_number=2, sort_order=2),
    ])]
    labels = [e["label"] for e in file_entries(vols, "S")]
    assert labels == ["Volume 1: Prologue", "Volume 1, part 2"]
    assert len(set(labels)) == 2


def test_chapter_with_several_files_yields_one_entry():
    files = [{"filePath": "/b/z.epub", "format": 3}, {"filePath": "/b/a.epub", "format": 3}]
    (e,) = file_entries([volume(1, "1", 1, [chapter(1, None, files=files)])], "S")
    assert e["file_path"] == "/b/a.epub"


def test_chapters_without_files_are_skipped():
    vols = [volume(1, "1", 1, [chapter(1, None, files=[])]), volume(2, "2", 2, [chapter(2, "/b/2.epub")])]
    assert [e["chapter_id"] for e in file_entries(vols, "S")] == [2]


def test_empty_volumes():
    assert file_entries([], "S") == []
    assert file_entries(None, "S") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HTTP behaviour (httpx.MockTransport)
# ---------------------------------------------------------------------------
def make_series(n):
    return [{"id": i, "name": f"Series {i}", "libraryId": 1} for i in range(1, n + 1)]


class FakeKavita:
    """Tiny in-process Kavita imitation."""

    def __init__(self, n_series=7, api_key="KEY", send_pagination=True, expire_first_token=False):
        self.series = make_series(n_series)
        self.api_key = api_key
        self.send_pagination = send_pagination
        self.calls: list[tuple[str, str]] = []
        self.logins = 0
        self.expire_first_token = expire_first_token

    def valid_token(self, request):
        auth = request.headers.get("Authorization", "")
        if self.expire_first_token and auth == "Bearer tok-1":
            return False
        return auth.startswith("Bearer tok-")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/api/Plugin/authenticate":
            if request.url.params.get("apiKey") != self.api_key:
                return httpx.Response(401, text="bad key")
            self.logins += 1
            return httpx.Response(200, json={"token": f"tok-{self.logins}", "username": "lector"})
        if path == "/api/Account/login":
            body = json.loads(request.content)
            if (body["username"], body["password"]) != ("reader", "secret"):
                return httpx.Response(401, text="Your credentials are not correct")
            self.logins += 1
            return httpx.Response(200, json={"token": f"tok-{self.logins}", "apiKey": "FROM-LOGIN"})
        if not self.valid_token(request):
            return httpx.Response(401)
        if path == "/api/Series/all-v2":
            page = int(request.url.params["pageNumber"])
            size = int(request.url.params["pageSize"])
            items = self.series[(page - 1) * size: page * size]
            headers = {}
            if self.send_pagination:
                total_pages = -(-len(self.series) // size)
                headers["Pagination"] = json.dumps({"currentPage": page, "itemsPerPage": size,
                                                    "totalItems": len(self.series), "totalPages": total_pages})
            return httpx.Response(200, json=items, headers=headers)
        if path == "/api/Library/libraries":
            return httpx.Response(200, json=[{"id": 1, "name": "Books", "type": 2}])
        if path == "/api/Image/series-cover":
            return httpx.Response(200, content=b"\x89PNG fake", headers={"content-type": "image/png"})
        if path == "/api/Download/chapter":
            return httpx.Response(200, content=b"PK epub bytes", headers={"content-type": "application/epub+zip"})
        if path == "/api/Series/volumes":
            return httpx.Response(200, text="<html>oops</html>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found")


def client(fake, **kw):
    return KavitaClient("http://kavita.test", transport=httpx.MockTransport(fake), retries=0, **kw)


def test_api_key_login_and_libraries():
    fake = FakeKavita()
    kv = client(fake, api_key="KEY")
    assert kv.configured
    assert kv.libraries() == [{"id": 1, "name": "Books", "type": 2}]
    assert fake.calls[0] == ("POST", "/api/Plugin/authenticate")
    kv.libraries()
    assert fake.logins == 1  # token reused


def test_password_login_picks_up_api_key():
    fake = FakeKavita()
    kv = client(fake, username="reader", password="secret")
    kv.libraries()
    assert ("POST", "/api/Account/login") in fake.calls
    assert kv.api_key == "FROM-LOGIN"


def test_bad_api_key_is_a_clear_auth_error():
    kv = client(FakeKavita(), api_key="WRONG")
    with pytest.raises(KavitaAuthError, match="rejected the API key"):
        kv.libraries()


def test_bad_password_is_a_clear_auth_error():
    kv = client(FakeKavita(), username="reader", password="nope")
    with pytest.raises(KavitaAuthError, match="username/password"):
        kv.libraries()


def test_not_configured():
    kv = KavitaClient("http://kavita.test")
    assert not kv.configured
    with pytest.raises(KavitaError, match="not configured"):
        kv.libraries()


def test_expired_token_triggers_one_relogin():
    fake = FakeKavita(expire_first_token=True)
    kv = client(fake, api_key="KEY")
    assert kv.libraries()
    assert fake.logins == 2


@pytest.mark.parametrize("page_size", [1, 3, 7, 50])
def test_series_paging_uses_pagination_header(page_size):
    fake = FakeKavita(n_series=7)
    kv = client(fake, api_key="KEY")
    out = kv.series_in_library(1, page_size=page_size)
    assert [s["id"] for s in out] == list(range(1, 8))
    pages = [c for c in fake.calls if c[1] == "/api/Series/all-v2"]
    assert len(pages) == -(-7 // page_size)  # exactly totalPages requests, no extra empty page


def test_series_paging_without_header_falls_back():
    fake = FakeKavita(n_series=6, send_pagination=False)
    kv = client(fake, api_key="KEY")
    assert len(kv.series_in_library(1, page_size=3)) == 6


def test_parse_pagination():
    assert K.parse_pagination('{"currentPage":1,"itemsPerPage":3,"totalItems":7,"totalPages":3}')["totalPages"] == 3
    assert K.parse_pagination(None) == {}
    assert K.parse_pagination("garbage") == {}


def test_non_json_response_raises_kavita_error():
    kv = client(FakeKavita(), api_key="KEY")
    with pytest.raises(KavitaError, match="non-JSON"):
        kv.volumes(1)


def test_http_error_status_raises_kavita_error():
    kv = client(FakeKavita(), api_key="KEY")
    with pytest.raises(KavitaError, match="HTTP 404"):
        kv.series(99)


def test_connection_error_is_wrapped():
    def boom(request):
        raise httpx.ConnectError("connection refused", request=request)

    kv = KavitaClient("http://kavita.test", api_key="KEY", transport=httpx.MockTransport(boom), retries=1)
    with pytest.raises(KavitaError, match="cannot connect to Kavita"):
        kv.libraries()


def test_timeout_is_wrapped():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    kv = KavitaClient("http://kavita.test", api_key="KEY", transport=httpx.MockTransport(slow), retries=0)
    with pytest.raises(KavitaError, match="timed out"):
        kv.libraries()


def test_transient_503_is_retried(monkeypatch):
    monkeypatch.setattr(K.time, "sleep", lambda s: None)
    state = {"n": 0}
    fake = FakeKavita()

    def flaky(request):
        if request.url.path == "/api/Library/libraries":
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(503)
        return fake(request)

    kv = KavitaClient("http://kavita.test", api_key="KEY", transport=httpx.MockTransport(flaky), retries=2)
    assert kv.libraries()
    assert state["n"] == 2


def test_bad_url_scheme():
    kv = KavitaClient("kavita:5000", api_key="KEY")
    with pytest.raises(KavitaError, match="http"):
        kv.libraries()
    assert kv.health() is False


def test_cover_and_download(tmp_path):
    kv = client(FakeKavita(), api_key="KEY")
    assert kv.cover_bytes(1).startswith(b"\x89PNG")
    dest = kv.download_chapter(5, tmp_path / "books" / "5.epub")
    assert dest.read_bytes() == b"PK epub bytes"
    assert not (tmp_path / "books" / "5.epub.part").exists()


def test_failed_download_leaves_no_partial_file(tmp_path):
    def deny(request):
        if request.url.path == "/api/Plugin/authenticate":
            return httpx.Response(200, json={"token": "tok-1"})
        return httpx.Response(403, text="no download role")

    kv = KavitaClient("http://kavita.test", api_key="KEY", transport=httpx.MockTransport(deny), retries=0)
    with pytest.raises(KavitaError, match="Download role"):
        kv.download_chapter(5, tmp_path / "5.epub")
    assert list(tmp_path.iterdir()) == []
