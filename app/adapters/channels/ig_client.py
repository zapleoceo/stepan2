"""Single instagrapi Client factory — proxy, geo, device delays in ONE place (S1 parity).

Anti-ban invariant (learned the hard way in S1): login AND the worker must reach IG
through the SAME proxy/IP with a MATCHING geo locale. Log in via an Indonesian proxy
then poll from the German server IP → Instagram sees a geo mismatch and issues a
checkpoint. The proxy is runtime Client state (never in dump_settings), so it must be
re-applied on every build — hence this factory. Geo is aligned ONLY when a proxy is set
(a regional locale over a datacenter IP is a worse mismatch than none)."""
from __future__ import annotations

from contextlib import suppress
from typing import Any

# instagrapi's internal pause between private-API calls; the send path adds its own
# humanlike delay on top of this.
DELAY_RANGE = (2, 5)

# branch language → (IG country, IG locale). Aligns device geo with a regional proxy.
_LANG_GEO: dict[str, tuple[str, str]] = {
    "id": ("ID", "id_ID"),
    "ms": ("MY", "ms_MY"),
    "en": ("US", "en_US"),
    "ru": ("RU", "ru_RU"),
    "th": ("TH", "th_TH"),
    "vi": ("VN", "vi_VN"),
    "hi": ("IN", "hi_IN"),
    "ko": ("KR", "ko_KR"),
    "ja": ("JP", "ja_JP"),
    "es": ("ES", "es_ES"),
    "pt": ("PT", "pt_PT"),
    "tr": ("TR", "tr_TR"),
    "fr": ("FR", "fr_FR"),
    "de": ("DE", "de_DE"),
    "ar": ("SA", "ar_SA"),
}


def geo_for_lang(lang: str) -> tuple[str, str]:
    """(country, locale) for a branch language; falls back to US/en_US."""
    return _LANG_GEO.get((lang or "").lower(), ("US", "en_US"))


def build_ig_client(
    session_settings: dict[str, Any] | None = None,
    *,
    proxy: str = "",
    lang: str = "",
    tz_offset_h: int | None = None,
) -> Any:
    """Construct an instagrapi Client with proxy, geo and delays applied.

    session_settings — instagrapi dump_settings (device/uuids/cookies). Proxy and geo
    are applied AFTER set_settings because restoring settings can reset them."""
    from instagrapi import Client  # noqa: PLC0415 (lazy: keep instagrapi out of tests)

    cl = Client()
    cl.delay_range = list(DELAY_RANGE)
    if session_settings:
        cl.set_settings(session_settings)
    if proxy:
        cl.set_proxy(proxy)
        country, locale = geo_for_lang(lang)
        with suppress(Exception):  # older instagrapi builds may lack a setter
            cl.set_country(country)
            cl.set_locale(locale)
            if tz_offset_h is not None:
                cl.set_timezone_offset(int(tz_offset_h) * 3600)
    return cl
