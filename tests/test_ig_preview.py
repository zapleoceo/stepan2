"""IG ad-creative preview resolver + proxy route: og:image extraction, in-process cache,
same-origin byte proxy, and the numeric-id guard."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

import app.api._ig_preview as igp  # noqa: E402
from app.api.main import app  # noqa: E402

_MID = "3932267938260790752"  # a real numeric IG media id → resolvable permalink


class _FakeResp:
    def __init__(self, status: int, text: str = "", content: bytes = b"",
                 ctype: str = "image/jpeg") -> None:
        self.status_code = status
        self.text = text
        self.content = content
        self.headers = {"content-type": ctype}


class _FakeClient:
    def __init__(self, handler) -> None:  # noqa: ANN001
        self._handler = handler

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *a) -> bool:  # noqa: ANN002
        return False

    async def get(self, url, headers=None):  # noqa: ANN001, ANN201, ARG002
        return self._handler(url)


def _patch_client(monkeypatch, handler) -> list:  # noqa: ANN001
    calls: list = []

    def factory(*a, **k):  # noqa: ANN002, ANN003, ANN202, ARG001
        def wrapped(url):  # noqa: ANN001, ANN202
            calls.append(url)
            return handler(url)
        return _FakeClient(wrapped)

    monkeypatch.setattr(igp.httpx, "AsyncClient", factory)
    return calls


async def test_og_image_extracted_and_cached(monkeypatch) -> None:
    igp._CACHE.clear()
    html = '<meta property="og:image" content="https://cdn.example/x.jpg?a=1&amp;b=2">'
    calls = _patch_client(monkeypatch, lambda url: _FakeResp(200, text=html))
    got = await igp.og_image_for_media(_MID)
    assert got == "https://cdn.example/x.jpg?a=1&b=2"  # &amp; decoded
    await igp.og_image_for_media(_MID)  # cached — no second fetch
    assert len(calls) == 1


async def test_og_image_none_when_no_meta(monkeypatch) -> None:
    igp._CACHE.clear()
    _patch_client(monkeypatch, lambda url: _FakeResp(200, text="<html>nothing</html>"))
    assert await igp.og_image_for_media(_MID) is None


async def test_fetch_creative_bytes_returns_image(monkeypatch) -> None:
    igp._CACHE.clear()
    html = '<meta property="og:image" content="https://cdn.example/y.jpg">'
    seq = [_FakeResp(200, text=html), _FakeResp(200, content=b"JPGDATA", ctype="image/png")]
    _patch_client(monkeypatch, lambda url: seq.pop(0))
    got = await igp.fetch_creative_bytes(_MID)
    assert got == (b"JPGDATA", "image/png")


def test_ig_preview_route_rejects_non_numeric() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/ui/ig-preview/not-a-number").status_code == 404


def test_ig_preview_route_returns_image(monkeypatch) -> None:
    import app.api._routes_admin as ra

    async def fake(mid: str):  # noqa: ANN202
        return (b"IMGBYTES", "image/png")

    monkeypatch.setattr(ra, "fetch_creative_bytes", fake)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(f"/ui/ig-preview/{_MID}")
    assert r.status_code == 200
    assert r.content == b"IMGBYTES"
    assert r.headers["content-type"] == "image/png"
    assert "max-age" in r.headers.get("cache-control", "")
