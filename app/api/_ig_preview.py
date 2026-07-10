"""Resolve an Instagram ad creative's thumbnail from its numeric media id.

We only store ad_media_id (a numeric IG media id). ig_post_url turns it into the public
/p/<code>/ permalink whose og:image is the creative thumbnail; we scrape that once and
cache the resolved CDN url in-process (creatives are immutable, so a hover never needs to
re-hit Instagram). The route then proxies the image bytes same-origin, which sidesteps IG
hotlink/referer/CORS restrictions on the raw cdninstagram url."""
from __future__ import annotations

import logging
import re

import httpx

from ._ui_html import ig_post_url

logger = logging.getLogger(__name__)

_OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"')
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_TIMEOUT = httpx.Timeout(6.0)
# The og:image url is scraped from an Instagram page, i.e. content we don't control — only
# fetch it if it points at an Instagram/Facebook CDN, and don't follow redirects, so a doctored
# post can't turn this same-origin proxy into an SSRF probe of internal addresses.
_ALLOWED_IMG_HOSTS = (".cdninstagram.com", ".fbcdn.net")
_CACHE: dict[str, str | None] = {}  # media_id -> og:image url (None caches a known miss)
_CACHE_CAP = 512


async def og_image_for_media(media_id: str) -> str | None:
    """The creative's thumbnail CDN url, or None if the permalink has no public preview."""
    if media_id in _CACHE:
        return _CACHE[media_id]
    url = ig_post_url(media_id)
    result: str | None = None
    if url:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
                resp = await c.get(url, headers={"User-Agent": _UA})
            if resp.status_code == 200:
                m = _OG_IMAGE_RE.search(resp.text)
                if m:
                    result = m.group(1).replace("&amp;", "&")
        except httpx.HTTPError:
            logger.warning("ig preview: permalink fetch failed media=%s", media_id)
    if len(_CACHE) >= _CACHE_CAP:
        _CACHE.clear()  # crude bound; creatives are static so a rare full reset is cheap
    _CACHE[media_id] = result
    return result


def _is_cdn_host(url: str) -> bool:
    from urllib.parse import urlparse  # noqa: PLC0415
    host = (urlparse(url).hostname or "").lower()
    return any(host == h.lstrip(".") or host.endswith(h) for h in _ALLOWED_IMG_HOSTS)


async def fetch_creative_bytes(media_id: str) -> tuple[bytes, str] | None:
    """(image bytes, content-type) for the creative thumbnail, or None if unavailable."""
    og = await og_image_for_media(media_id)
    if not og or not _is_cdn_host(og):
        return None
    try:
        # follow_redirects=False: the CDN host is validated above; a redirect could bounce us
        # off the allowlist to an internal address.
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as c:
            resp = await c.get(og, headers={"User-Agent": _UA})
    except httpx.HTTPError:
        logger.warning("ig preview: image fetch failed media=%s", media_id)
        return None
    if resp.status_code != 200:
        return None
    return resp.content, resp.headers.get("content-type", "image/jpeg")
