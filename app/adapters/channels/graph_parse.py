"""Pure parsing of an official Graph API message payload — no I/O, no httpx.

Graph delivers a photo, video or voice DM as an `attachments` array with an EMPTY
`message` string, so without this the whole item ingested as a blank row: nothing for the
model to read and nothing for the media backfill to fetch.

The kinds and placeholders here are deliberately the ones the instagrapi path already
produces (see ig_parse) — one convention, so the backfill worker, the transcribe/describe
swap and the reply hold cannot tell the two connectors apart.

Nothing in here may raise on a payload it does not recognise. These shapes have never been
checked against live Graph traffic, and an exception raised here is not an httpx error, so
it escapes the poll's own handler, escapes fetch_inbound (the worker catches only
RuntimeError there) and is stopped only by the worker's blanket except — one odd attachment
would take the whole channel to zero messages for that tick, every tick. Media we cannot
read costs the media, never the message and never the channel.
"""
from __future__ import annotations

import logging
from typing import Any

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH

logger = logging.getLogger(__name__)

# Graph nests the CDN url per attachment type; file_url is the catch-all an Instagram
# voice note arrives under.
_BLOCKS: tuple[tuple[str, str], ...] = (("image_data", "image"), ("video_data", "video"))

_MIME_KIND: tuple[tuple[str, str], ...] = (
    ("image/", "image"), ("video/", "video"), ("audio/", "audio"))

# Last resort when a file_url arrives with no usable mime. Extension only, and a miss means
# "no media": guessing turns a PDF into a fake image, the fake image goes to the vision
# describer, the describer fails, and the placeholder holds the thread silent for the full
# 6h media window. Branch 7 is a Page inbox — leads attach documents there routinely.
_EXT_KIND: dict[str, str] = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".webp": "image", ".heic": "image", ".bmp": "image",
    ".mp4": "video", ".mov": "video", ".webm": "video", ".m4v": "video",
    ".mp3": "audio", ".m4a": "audio", ".ogg": "audio", ".oga": "audio",
    ".opus": "audio", ".aac": "audio", ".wav": "audio",
}

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


def _kind_from_url(url: str) -> str | None:
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    dot = path.rfind(".")
    return _EXT_KIND.get(path[dot:]) if dot > 0 else None


def _url_of(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    return str(block.get("url") or "").strip() or None


def _attachment_data(message: Any) -> list[Any]:
    """Attachments of a Graph message, in every container shape it is known to arrive in.

    The poll wraps them as {"data": [...]}; the Messenger webhook (S8) sends a bare list, and
    `.get("data")` on that list raised AttributeError — a dead channel, per the module
    docstring, not a lost photo."""
    if not isinstance(message, dict):
        return []
    atts = message.get("attachments")
    if isinstance(atts, dict):
        atts = atts.get("data")
    return atts if isinstance(atts, list) else []


def media_of(message: dict[str, Any]) -> tuple[str, str] | None:
    """(kind, url) of the first attachment we can CLASSIFY on a Graph message, or None.

    mime_type wins over the nesting when both are present — Graph puts an animated GIF in
    image_data with an image/gif mime and a voice note in file_url with audio/mp4, and the
    mime is the only one of the two that is ever explicit about audio.

    An attachment whose kind we cannot establish is skipped rather than guessed, so a
    document sent alongside a photo still lets the photo through."""
    for att in _attachment_data(message):
        if not isinstance(att, dict):
            continue
        mime_kind = _kind_from_mime(att.get("mime_type"))
        for key, block_kind in _BLOCKS:
            url = _url_of(att.get(key))
            if url:
                return mime_kind or block_kind, url
        url = str(att.get("file_url") or "").strip() or None
        kind = (mime_kind or _kind_from_url(url)) if url else None
        if url and kind:
            return kind, url
    return None


def placeholder_for(kind: str) -> str:
    return _PLACEHOLDER.get(kind, IMAGE_PENDING_PH)


def _media_or_none(m: dict[str, Any]) -> tuple[str, str] | None:
    # media_of is written to be total; this is the belt to those braces. The invariant that
    # has to hold for a live channel is that a payload we misread costs the media, not the
    # tick — so the boundary swallows and logs instead of propagating.
    try:
        return media_of(m)
    except Exception:  # noqa: BLE001 — module docstring: never take the channel down
        logger.warning("graph attachment unparseable, ingesting without media: mid=%s",
                       m.get("id"), exc_info=True)
        return None


def row_of(conv: dict[str, Any], m: dict[str, Any], who: dict[str, Any],
           own: set[str]) -> dict[str, Any]:
    """One Graph message → the flat dict MetaBusinessAdapter maps to an InboundMessage."""
    from_id = str((m.get("from") or {}).get("id", ""))
    text = str(m.get("message") or "")
    kind_url = _media_or_none(m)
    if not text and kind_url:
        # A photo/video/voice DM arrives with an EMPTY message string — ingesting that
        # verbatim stored a blank turn the model could not read and the backfill could not
        # find. Same placeholder the instagrapi path writes; the recognizer swaps it later.
        text = placeholder_for(kind_url[0])
    return {
        "thread_id": conv.get("id", ""),
        # Graph's own message id: what lets a webhook delivery and a poll of the same message
        # dedup to ONE row. The synthetic thread+time+text id differs between those two paths.
        # Empty when Graph omits it; ingest falls back to the synthetic id then.
        "mid": str(m.get("id") or ""),
        # Derived, not assumed — but note the poll filters our own ids out before this point,
        # so on the poll path it can only read "in" today. It is computed from the payload so
        # that the webhook path (S8), which DOES deliver echoes of our own sends, gets the
        # truth out of the same builder instead of a default that happens to be right.
        "direction": "out" if from_id and from_id in own else "in",
        "from_id": from_id,
        "message": text,
        "created_time": m.get("created_time"),
        "sender_name": who.get("name") or "",
        "sender_username": who.get("username") or "",
        "media_kind": kind_url[0] if kind_url else None,
        "media_url": kind_url[1] if kind_url else None,
    }


def dedup_key(row: dict[str, Any]) -> str:
    """Identity of a polled Graph message for cross-platform dedup.

    The native mid when Graph gives one. Otherwise thread+time+sender+TEXT: Graph timestamps
    are second-resolution, so two lines typed quickly share a created_time, and a key without
    the text drops the second — the very loss this dedup must not cause."""
    mid = str(row.get("mid") or "")
    if mid:
        return mid
    return "\x00".join((str(row.get("thread_id", "")), str(row.get("created_time", "")),
                        str(row.get("from_id", "")), str(row.get("message", ""))))
