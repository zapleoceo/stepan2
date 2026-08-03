"""Pure parsing of an official Graph API message payload — no I/O, no httpx.

Graph delivers a photo, video or voice DM as an `attachments` array with an EMPTY
`message` string, so without this the whole item ingested as a blank row: nothing for the
model to read and nothing for the media backfill to fetch.

The kinds and placeholders here are deliberately the ones the instagrapi path already
produces (see ig_parse) — one convention, so the backfill worker, the transcribe/describe
swap and the reply hold cannot tell the two connectors apart.
"""
from __future__ import annotations

from typing import Any

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH

# Graph nests the CDN url per attachment type; file_url is the catch-all an Instagram
# voice note arrives under.
_BLOCKS: tuple[tuple[str, str], ...] = (("image_data", "image"), ("video_data", "video"))

_MIME_KIND: tuple[tuple[str, str], ...] = (
    ("image/", "image"), ("video/", "video"), ("audio/", "audio"))

# A video shares the image placeholder because the instagrapi path does: IG item_type
# 'media' covers photo AND video and yields '🖼 media' for both. Only audio is distinct.
_PLACEHOLDER: dict[str, str] = {
    "image": IMAGE_PENDING_PH, "video": IMAGE_PENDING_PH, "audio": VOICE_PENDING_PH}


def _kind_from_mime(mime: Any) -> str | None:
    low = str(mime or "").lower()
    for prefix, kind in _MIME_KIND:
        if low.startswith(prefix):
            return kind
    return None


def _url_of(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    return str(block.get("url") or "").strip() or None


def media_of(message: dict[str, Any]) -> tuple[str, str] | None:
    """(kind, url) of the first real attachment on a Graph message, or None.

    mime_type wins over the nesting when both are present — Graph puts an animated GIF in
    image_data with an image/gif mime and a voice note in file_url with audio/mp4, and the
    mime is the only one of the two that is ever explicit about audio."""
    for att in (message.get("attachments") or {}).get("data") or []:
        if not isinstance(att, dict):
            continue
        mime_kind = _kind_from_mime(att.get("mime_type"))
        for key, block_kind in _BLOCKS:
            url = _url_of(att.get(key))
            if url:
                return mime_kind or block_kind, url
        url = str(att.get("file_url") or "").strip() or None
        if url:
            # Unknown mime + no typed block: call it an image so the stub still reaches the
            # backfill worker (ingest defaults the same way) rather than being dropped.
            return mime_kind or "image", url
    return None


def placeholder_for(kind: str) -> str:
    return _PLACEHOLDER.get(kind, IMAGE_PENDING_PH)
