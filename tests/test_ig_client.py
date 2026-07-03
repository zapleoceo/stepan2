"""build_ig_client factory: proxy + geo alignment applied consistently (S1 anti-ban)."""
from __future__ import annotations

import sys
import types

from app.adapters.channels.ig_client import build_ig_client, geo_for_lang


class _FakeClient:
    def __init__(self) -> None:
        self.delay_range = None
        self.settings = None
        self.calls: dict[str, object] = {}

    def set_settings(self, s):
        self.settings = s

    def set_proxy(self, p):
        self.calls["proxy"] = p

    def set_country(self, c):
        self.calls["country"] = c

    def set_locale(self, loc):
        self.calls["locale"] = loc

    def set_timezone_offset(self, off):
        self.calls["tz"] = off


def _fake_instagrapi(monkeypatch):
    mod = types.ModuleType("instagrapi")
    mod.Client = _FakeClient
    monkeypatch.setitem(sys.modules, "instagrapi", mod)


def test_geo_for_lang_maps_known_and_falls_back() -> None:
    assert geo_for_lang("ms") == ("MY", "ms_MY")
    assert geo_for_lang("id") == ("ID", "id_ID")
    assert geo_for_lang("ZZ") == ("US", "en_US")  # unknown → default


def test_build_applies_proxy_and_geo(monkeypatch) -> None:
    _fake_instagrapi(monkeypatch)
    cl = build_ig_client({"uuid": "x"}, proxy="http://p:1", lang="ms", tz_offset_h=8)
    assert cl.settings == {"uuid": "x"}
    assert cl.delay_range == [2, 5]
    assert cl.calls == {"proxy": "http://p:1", "country": "MY",
                        "locale": "ms_MY", "tz": 8 * 3600}


def test_build_skips_geo_without_proxy(monkeypatch) -> None:
    """No proxy → no proxy/geo calls (a regional locale over a datacenter IP is worse)."""
    _fake_instagrapi(monkeypatch)
    cl = build_ig_client({"uuid": "x"}, lang="ms", tz_offset_h=8)
    assert cl.calls == {}
    assert cl.delay_range == [2, 5]
