"""Meta webhook payload → normalized message events. Pure: no DB, no HTTP, no clock.

The POST handler must ack in milliseconds (Meta retries hard and disables a slow endpoint),
so everything that can be decided from the bytes alone is decided here, and the result is
what travels to the worker. Keeping it pure is also what makes the shape testable without a
Page, a token or a queue.

Shape (identical for object='page' and object='instagram'):
    entry[].messaging[] = {sender:{id}, recipient:{id}, timestamp, message:{mid, text, ...}}
Note what is NOT in there: the conversation id the poll keys threads on. That reconciliation
needs I/O and lives in webhook_threads.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.domain.clock import as_naive_utc

_MEDIA_KINDS = {"image": "image", "video": "video", "audio": "audio", "file": "image"}
_MEDIA_TEXT = {"image": IMAGE_PENDING_PH, "video": IMAGE_PENDING_PH, "audio": VOICE_PENDING_PH}


@dataclass(frozen=True)
class WebhookMessage:
    """One inbound message as the webhook describes it, before thread reconciliation."""

    page_id: str
    sender_id: str
    mid: str
    text: str
    occurred_at: datetime
    ad_id: str | None = None
    ad_media_id: str | None = None
    ad_preview_url: str | None = None
    lead_source: str | None = None
    media_url: str | None = None
    media_kind: str | None = None
    link_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form for the queue — the API and the worker are separate containers and
        are restarted separately, so a job in flight across a deploy must not depend on both
        sides holding the same class."""
        d = self.__dict__.copy()
        d["occurred_at"] = self.occurred_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WebhookMessage:
        data = dict(raw)
        data["occurred_at"] = as_naive_utc(data.get("occurred_at"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def parse_meta_messages(payload: dict[str, Any]) -> list[WebhookMessage]:
    """Every ingestible inbound message in one webhook body. Unknown/irrelevant events
    (delivery receipts, read receipts, reactions, postbacks) yield nothing rather than raise —
    Meta adds event types without warning, and a 500 here means an endless retry storm."""
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return []
    out: list[WebhookMessage] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        page_id = str(entry.get("id") or "")
        for item in _messaging(entry):
            parsed = _one(page_id, item)
            if parsed is not None:
                out.append(parsed)
    return out


def _messaging(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """entry.messaging only. `standby` carries the same shape for a thread another app
    currently owns under the handover protocol — ingesting those would have us answering
    conversations we are not the active receiver for."""
    items = entry.get("messaging")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _one(page_id: str, item: dict[str, Any]) -> WebhookMessage | None:
    message = _sub(item, "message")
    if message is None:
        return None
    if message.get("is_echo") or message.get("is_deleted"):
        # An echo is OUR OWN send coming back. OutboxSender already recorded everything we
        # sent, under the send API's own id — the echo carries a different mid, so external-id
        # dedup would miss it and the reply would appear twice in the thread and in the LLM
        # context. Manual replies typed in the IG app still arrive via the poll.
        return None
    mid = str(message.get("mid") or "")
    sender_id = str(_sub(item, "sender", default={}).get("id") or "")
    if not mid or not sender_id:
        return None
    media_url, media_kind, link_url = _attachment(message)
    text = str(message.get("text") or "").strip()
    if not text:
        text = _MEDIA_TEXT.get(media_kind or "", "") or (f"🔗 {link_url}" if link_url else "")
    if not text and not media_url:
        return None  # nothing a human or the model could read
    ad_id, ad_media_id, ad_preview_url, lead_source = _referral(item, message)
    return WebhookMessage(
        page_id=page_id,
        sender_id=sender_id,
        mid=mid,
        text=text,
        occurred_at=_occurred_at(item),
        ad_id=ad_id,
        ad_media_id=ad_media_id,
        ad_preview_url=ad_preview_url,
        lead_source=lead_source,
        media_url=media_url,
        media_kind=media_kind,
        link_url=link_url,
    )


def _occurred_at(item: dict[str, Any]) -> datetime:
    """Webhook timestamps are epoch MILLIseconds; Graph's created_time (the poll's source) is
    whole seconds. Truncating here makes the two paths produce the identical occurred_at for
    the same message, which is what lets the ±2s content dedup in IngestService recognise a
    polled message as one the webhook already stored."""
    raw = item.get("timestamp")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return as_naive_utc(int(raw // 1000))
    return as_naive_utc(raw)


def _sub(obj: dict[str, Any], key: str, *, default: dict | None = None) -> dict[str, Any] | None:
    value = obj.get(key)
    return value if isinstance(value, dict) else default


def _attachment(message: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(media_url, media_kind, link_url) of the first attachment we can do something with.

    A share / story_mention / template has a url but no downloadable media — it is a link,
    and storing it as media would leave a permanently pending MediaAsset."""
    atts = message.get("attachments")
    if not isinstance(atts, list):
        return None, None, None
    for att in atts:
        if not isinstance(att, dict):
            continue
        url = str(_sub(att, "payload", default={}).get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        kind = _MEDIA_KINDS.get(str(att.get("type") or ""))
        if kind:
            return url, kind, None
        return None, None, url
    return None, None, None


def _referral(
    item: dict[str, Any], message: dict[str, Any]
) -> tuple[str | None, str | None, str | None, str | None]:
    """Ad/story attribution. Meta puts it under message.referral for a click-to-message ad
    and at the top level for an OPEN_THREAD referral — both mean the same thing here."""
    ref = _sub(message, "referral") or _sub(item, "referral")
    if ref is None:
        return None, None, None, None
    ad_id = str(ref.get("ad_id") or "") or None
    source = str(ref.get("source") or "").upper()
    ctx = _sub(ref, "ads_context_data", default={}) or {}
    ad_media_id = str(ctx.get("post_id") or "") or None
    preview = str(ctx.get("photo_url") or ctx.get("video_url") or "") or None
    lead_source = None
    if ad_id or source == "ADS":
        lead_source = "ad_clicktomsg"
    elif source in ("IG_STORY", "STORY"):
        lead_source = "story"
    return ad_id, ad_media_id, preview, lead_source
