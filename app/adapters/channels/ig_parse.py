"""Pure parsing of raw IG direct-thread items — no I/O, no instagrapi (S1 parity).

instagrapi's own extractor crashes on shared media (a shared post/reel item carries an
`instagram://` URL that fails pydantic validation) and takes the whole ingest down with
it. So the transport pulls the raw private-API JSON and this module turns each item into
content: text, a clickable link, a preview and any real in-DM media. Nothing is lost —
link/card shares become a captioned line, media items become a placeholder + a media url
the backfill worker downloads later."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# item_type values that are system events, not messages — skipped.
_ITEM_SKIP = {"action_log", "video_call_event", "placeholder"}

# emoji prefix for shared XMA cards (content is pulled from the payload).
_XMA_EMOJI = {
    "xma_link": "🔗", "generic_xma": "📎", "xma_media_share": "📷",
    "xma_clip": "🎬", "xma_reel_share": "🎬", "xma_story_share": "📖",
    "xma_profile_share": "👤", "xma_product_share": "🛍",
}

# placeholder for item types with no extractable text (or unknown).
_MEDIA_PH = {
    "xma_link": "🔗 link", "generic_xma": "📎 attachment", "link": "🔗 link",
    "xma_media_share": "📷 post", "xma_clip": "🎬 reel", "xma_reel_share": "🎬 reel",
    "xma_story_share": "📖 story", "xma_profile_share": "👤 profile",
    "xma_product_share": "🛍 product", "media": "🖼 media", "raven_media": "🖼 media",
    "animated_media": "GIF", "voice_media": "🎤 voice", "store_sticker": "🏷 sticker",
}
# The untranscribed-voice placeholder. Once the broker transcribes, the message text
# becomes "🎤 <words>" (longer), so an exact match means transcription is still pending.
VOICE_PENDING_PH = _MEDIA_PH["voice_media"]
# Same idea for an image the broker hasn't described yet — becomes "🖼 <description>".
IMAGE_PENDING_PH = _MEDIA_PH["media"]

_URL_RE = re.compile(r"https?://[^\s]+")


def canonical_ig_media(url: str, item_type: str | None = None) -> str:
    """instagram.com/{reel|reels|p|tv}/{code}/… → native permalink.

    Media type comes from item_type (clip/reel_share → /reel/, else /p/), NOT the path:
    a sponsored clip-ad shares a normal POST under a /reel/ URL — the path lies and
    /reel/ won't open the post. Without item_type, universal /p/ opens anything."""
    try:
        pr = urlparse(url)
        if not pr.netloc.endswith("instagram.com"):
            return url
        seg = [s for s in pr.path.split("/") if s]
        if len(seg) >= 2 and seg[0] in ("reel", "reels", "p", "tv"):
            kind = "reel" if item_type in ("xma_clip", "xma_reel_share") else "p"
            return f"https://www.instagram.com/{kind}/{seg[1]}/"
    except ValueError:
        pass
    return url


def clean_url(url: Any, item_type: str | None = None) -> str | None:
    """Unwrap l.instagram.com/?u=… + canonicalize IG media to its native type."""
    if not url:
        return None
    u = str(url).strip()
    if not u:
        return None
    if "l.instagram.com" in u and "u=" in u:
        try:
            real = parse_qs(urlparse(u).query).get("u", [None])[0]
            if real:
                u = unquote(real)
        except ValueError:
            pass
    return canonical_ig_media(u, item_type) or None


def _xma_payload(item: dict) -> dict | None:
    ty = item.get("item_type", "")
    p = item.get(ty)
    if p is None and ty.startswith("xma_"):
        p = item.get(ty[4:])
    if isinstance(p, list):
        p = p[0] if p else None
    return p if isinstance(p, dict) else None


def _xma_text(p: dict) -> str | None:
    """Meaningful card text: title / subtitle / caption, deduped."""
    parts: list[str] = []
    for k in ("header_title_text", "header_subtitle_text", "title_text",
              "subtitle_text", "caption_body_text"):
        v = p.get(k)
        if v and str(v).strip():
            parts.append(str(v).strip())
    lc = p.get("link_context")
    if not parts and isinstance(lc, dict):
        for k in ("link_title", "link_summary"):
            v = lc.get(k)
            if v and str(v).strip():
                parts.append(str(v).strip())
    return " · ".join(dict.fromkeys(parts)) or None


def _xma_emoji(ty: str, text: str) -> str:
    t = (text or "").lower()
    if "appointment" in t:
        return "📅"
    if "phone" in t:
        return "📱"
    return _XMA_EMOJI.get(ty, "📎")


def _versions_url(med: dict) -> tuple[str, str] | None:
    vv = med.get("video_versions")
    if vv:
        return "video", vv[0].get("url")
    cands = (med.get("image_versions2") or {}).get("candidates") or []
    if cands:
        return "image", cands[0].get("url")
    return None


def media_url(item: dict) -> tuple[str, str] | None:
    """(kind, url) of real in-DM media (photo/video/gif/voice). Shared posts/reels are
    NOT here (their preview comes from the XMA payload). None when there is no media."""
    ty = item.get("item_type")
    if ty in ("media", "raven_media"):
        # A disappearing / view-once photo (item_type raven_media) carries its real media
        # under item['visual_media']['media'], NOT item['raven_media'] — reading only the
        # latter dropped every such photo as a bare '🖼 media' with no asset. Try every
        # nesting the private API is known to use.
        meds = [item.get(ty) or {}, (item.get("visual_media") or {}).get("media") or {}]
        for base in list(meds):
            if isinstance(base.get("media"), dict):
                meds.append(base["media"])
        for med in meds:
            got = _versions_url(med)
            if got and got[1]:
                return got
    elif ty == "animated_media":
        imgs = (item.get("animated_media") or {}).get("images") or {}
        for k in ("fixed_height", "original", "fixed_width"):
            u = (imgs.get(k) or {}).get("url")
            if u:
                return "image", u
    elif ty == "voice_media":
        au = ((item.get("voice_media") or {}).get("media") or {}).get("audio") or {}
        if au.get("audio_src"):
            return "audio", au["audio_src"]
    return None


def item_content(item: dict) -> dict | None:
    """Raw thread item → {text, link_url, preview_url, media_url, media_kind}, or None
    for system events / empty text items. Takes ALL types — content from .text or the XMA
    payload, target/preview from the payload. Nothing is silently dropped."""
    ty = item.get("item_type")
    if ty in _ITEM_SKIP:
        return None
    text = (item.get("text") or "").strip()
    link = preview = None
    p = _xma_payload(item)
    if p:
        lc = p.get("link_context") if isinstance(p.get("link_context"), dict) else {}
        link = clean_url(p.get("target_url") or lc.get("link_url"), ty)
        pu = p.get("preview_url") or lc.get("link_image_url")
        if pu and str(pu).startswith(("http://", "https://")):
            preview = str(pu)
        if not text:
            text = (p.get("text") or "").strip()
    if not text:
        xt = _xma_text(p) if p else None
        if xt:
            text = f"{_xma_emoji(ty, xt)} {xt}"
        elif link and ty in ("link", "xma_link"):
            text = f"🔗 {link}"
        elif ty == "text":
            return None
        else:
            text = _MEDIA_PH.get(ty, f"[{ty}]")
    if not link:
        mu = _URL_RE.search(text)
        if mu:
            link = canonical_ig_media(mu.group(0).rstrip(".,)"), ty)
    mk = media_url(item)
    return {
        "text": text,
        "link_url": link,
        "preview_url": preview,
        "media_url": mk[1] if mk else None,
        "media_kind": mk[0] if mk else None,
    }
