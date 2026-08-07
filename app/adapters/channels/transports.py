"""Real transports — the only place that touches httpx / instagrapi.

Each implements one of the adapter transport Protocols. Third-party imports are lazy
(inside methods) so these modules import without the deps present and unit tests can
inject fakes instead. Swap the underlying API here; adapters stay untouched."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.adapters.channels import graph_parse
from app.config import settings
from app.domain.clock import as_naive_utc

logger = logging.getLogger(__name__)

_MEDIA_MAX_BYTES = 60 * 1024 * 1024  # 60 MB — a DM video well past this is dropped
_MEDIA_TIMEOUT = 90.0                 # a large video CDN fetch needs more than 30s

# Ad click-to-DM attribution codes. Word-boundary matched so an ordinary name that merely
# CONTAINS these letters ("Ahmad", "Nadia", "Murad") is not misfiled as an ad lead.
_AD_ATTR_RE = re.compile(r"\b(ctd|ads?|click.?to.?(dm|direct|message))\b")

# Live polling only needs the most-recent threads (new messages surface at the top);
# deep pagination every minute is slow (each page costs an IG call + 2-5s delay) and
# would push a poll cycle past the cron interval → overlapping runs. Backfill of old
# history is a separate, on-demand path.
_LIVE_THREADS = 20

# How many of a thread's most recent messages Graph returns per poll. One was the old
# behaviour and lost anything a lead sent in a burst; 25 covers any realistic burst between
# two polls while keeping the payload small. Already-seen ones are dropped by external_id.
_MSGS_PER_THREAD = 25


def _paged_threads(client: Any, endpoint: str, amount: int = _LIVE_THREADS) -> list[dict]:
    """Raw inbox threads with cursor pagination — instagrapi extractor bypassed."""
    out: list[dict] = []
    cursor = None
    for _ in range(max(1, (amount + 19) // 20)):
        params = {"visual_message_return_type": "unseen",
                  "thread_message_limit": "10", "persistentBadging": "true", "limit": "20"}
        if cursor:
            params["cursor"] = cursor
            params["direction"] = "older"
        res = client.private_request(endpoint, params=params)
        inbox = res.get("inbox", {})
        out.extend(inbox.get("threads", []))
        if len(out) >= amount or not inbox.get("has_older"):
            break
        cursor = inbox.get("oldest_cursor")
        if not cursor:
            break
    return out[:amount]


def _lead_seen(thread: dict, lead_pk: str | None) -> int | None:
    """Lead's read-receipt for this thread (last_seen_at[pk].timestamp, µs) or None."""
    lsa = thread.get("last_seen_at") or {}
    entry = lsa.get(lead_pk) if lead_pk and isinstance(lsa, dict) else None
    if isinstance(entry, dict):
        ts = entry.get("timestamp")
        if ts is not None:
            try:
                return int(ts)
            except (ValueError, TypeError):
                return None
    return None


def _detect_lead_source(thread: dict, lead_pk: Any) -> str | None:
    """Infer how the lead found us from IG thread send_attribution metadata."""
    attrs = thread.get("send_attribution") or {}
    lead_str = str(lead_pk) if lead_pk else ""
    pairs: list[tuple] = (
        list(attrs.items()) if isinstance(attrs, dict)
        else [(a.get("user_id"), a.get("display_name", "")) for a in attrs if isinstance(a, dict)]
    )
    for uid, sa in pairs:
        if str(uid) != lead_str:
            continue
        low = (sa or "").lower()
        if _AD_ATTR_RE.search(low):
            return "ad_clicktomsg"
        if "story" in low:
            return "story"
    return None


async def download_bounded(url: str, *, follow_redirects: bool = False) -> bytes:
    """Stream raw media bytes from a CDN url, bounded so a huge video can't OOM the worker.

    Deliberately unauthenticated: these are signed CDN links, and attaching the channel's
    token would ship a live page/session credential to a host that never needs it.

    Redirects are OFF by default — that is exactly what the IG path has always done, and
    branch 1 runs it. Graph opts in because its attachment urls are lookaside redirectors."""
    import httpx  # lazy: real transport only, never imported by unit tests  # noqa: PLC0415

    # connect kept short; read stretched for a big video over a slow CDN.
    timeout = httpx.Timeout(_MEDIA_TIMEOUT, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as c, \
            c.stream("GET", url) as r:
        r.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in r.aiter_bytes():
            total += len(chunk)
            if total > _MEDIA_MAX_BYTES:
                raise ValueError(
                    f"media exceeds {_MEDIA_MAX_BYTES} bytes — refusing to buffer")
            chunks.append(chunk)
    return b"".join(chunks)


class InstagrapiTransport:
    """Implements channels.instagram.IGTransport by wrapping a logged-in instagrapi client."""

    def __init__(self, *, username: str, session_settings: dict[str, Any],
                 proxy: str = "", lang: str = "", tz_offset_h: int | None = None) -> None:
        self._username = username
        self._session_settings = session_settings
        self._proxy = proxy
        self._lang = lang
        self._tz_offset_h = tz_offset_h
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            # Same factory as the login route → identical proxy+geo, no checkpoint.
            from app.adapters.channels.ig_client import build_ig_client  # noqa: PLC0415

            self._client = build_ig_client(
                self._session_settings, proxy=self._proxy,
                lang=self._lang, tz_offset_h=self._tz_offset_h)
        return self._client

    def _resolve_own_id(self, client: Any) -> str:
        """Our IG account's numeric id — REQUIRED to tell our own sent items apart from the
        lead's. `client.user_id` is set by instagrapi's login() flow, NOT by set_settings();
        we rebuild the client from a stored session dump and never re-login, so it stays
        unset. `authorization_data.ds_user_id` DOES survive set_settings() and equals the
        user_id IG stamps on our own polled items — always prefer it.

        Fail-CLOSED if neither is available: direction is decided by `uid == own_id`, so a
        blank own_id would classify EVERY item as inbound, silently filing our own outgoing
        messages as if the lead sent them (corrupts the transcript AND the LLM's turn-taking
        — this was a real prod incident: 1401 of our sends mislabeled inbound). Raising skips
        the whole poll (logged by the caller) instead of writing corrupt rows."""
        own = str((self._session_settings.get("authorization_data") or {}).get("ds_user_id") or "")
        if not own and client.user_id:
            own = str(client.user_id)
        if not own:
            raise RuntimeError(
                "cannot resolve own IG user id (no ds_user_id / client.user_id); skipping "
                "poll to avoid misclassifying our own messages as inbound lead messages"
            )
        return own

    async def fetch_threads(self) -> list[dict[str, Any]]:
        from .ig_parse import item_content  # noqa: PLC0415

        client = self._ensure_client()
        own_id = self._resolve_own_id(client)  # raises → caller skips this poll, no corrupt rows
        out: list[dict[str, Any]] = []
        seen_threads: set[str] = set()
        # Raw private API gives ad_context_data / send_attribution not in the pydantic
        # model, and survives shared-media items that crash instagrapi's own extractor.
        # inbox/ = accepted chats; pending_inbox/ = message requests (cold ad leads).
        for endpoint in ("direct_v2/inbox/", "direct_v2/pending_inbox/"):
            try:
                threads = await asyncio.to_thread(_paged_threads, client, endpoint)
            except Exception as exc:  # noqa: BLE001
                logger.warning("IG %s failed: %s", endpoint, exc)
                continue
            for t in threads:
                items = t.get("items") or []
                tid = str(t.get("thread_id", ""))
                if t.get("is_group") or not items or tid in seen_threads:
                    continue  # a thread mid pending→accepted appears in both — process once
                seen_threads.add(tid)
                users = t.get("users") or []
                lead_u = next((u for u in users if str(u.get("pk", "")) != own_id), None)
                lead_pk = str((lead_u or {}).get("pk") or "") or None
                acd = t.get("ad_context_data") or {}
                pm = t.get("professional_metadata") or {}
                base = {
                    "thread_id": str(t.get("thread_id", "")),
                    "lead_ig_user_id": lead_pk,
                    "sender_username": (lead_u or {}).get("username") or None,
                    "sender_name": (lead_u or {}).get("full_name") or None,
                    "sender_avatar": str((lead_u or {}).get("profile_pic_url") or "") or None,
                    "ad_id": str(acd["ad_id"]) if acd.get("ad_id") else None,
                    "ad_media_id": str(pm["ad_ig_media_id"]) if pm.get("ad_ig_media_id")
                    else None,
                    "ad_preview_url": acd.get("ad_picture_url") or None,
                    "lead_source": _detect_lead_source(t, lead_pk),
                    "lead_seen_at": _lead_seen(t, lead_pk),
                }
                # ALL items, oldest first — a burst of lead messages between polls, and
                # lead messages sitting behind our own reply, must not be lost. Our own
                # items come through too (direction=out) so a manual reply from the IG app
                # moves last_out_at and the bot never answers over a human.
                for item in reversed(items):
                    content = item_content(item)
                    if content is None:
                        continue
                    uid = str(item.get("user_id", ""))
                    if not uid:
                        # No sender on the item — we cannot know whose it is, and guessing
                        # 'in' is exactly how our own messages got mislabeled. Skip it; a
                        # real message re-surfaces on a later poll with its user_id present.
                        continue
                    out.append({
                        **base,
                        # client_context is IG's own idempotency key — fall back to it so
                        # an item without item_id still gets a STABLE id (else the synthetic
                        # fallback drifts by timestamp precision and dupes the message).
                        "item_id": str(item.get("item_id") or item.get("client_context") or "")
                        or None,
                        # own_id is guaranteed non-empty here (see _resolve_own_id), so this
                        # is a definite ownership test, never a fall-through-to-inbound default.
                        "direction": "out" if uid == own_id else "in",
                        "sender_id": uid,
                        "timestamp": item.get("timestamp"),
                        **content,
                    })
        return out

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]:
        client = self._ensure_client()
        message = await asyncio.to_thread(
            client.direct_send, text, thread_ids=[int(thread_id)]
        )
        return {"item_id": message.id}

    async def mark_seen(self, thread_id: str) -> None:
        """Mark the thread read (humanlike: a person reads before replying)."""
        client = self._ensure_client()
        await asyncio.to_thread(client.direct_send_seen, int(thread_id))

    async def revoke_direct(self, thread_id: str, item_id: str) -> None:
        """Unsend our own message in IG (raises on failure — caller keeps the flag)."""
        client = self._ensure_client()
        await asyncio.to_thread(client.direct_message_delete, int(thread_id), item_id)

    async def account_health(self) -> str:
        client = self._ensure_client()
        try:
            await asyncio.to_thread(client.get_timeline_feed)
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "challenge" in name:
                return "challenge"
            if "login" in name:  # LoginRequired → the session genuinely needs re-auth
                return "expired"
            # A transport blip (timeout/connection/throttle) or any unrecognized error must NOT
            # be reported as 'expired': that triggers a needless re-login, and a fresh login from
            # a datacenter IP is exactly the checkpoint/blacklist path we avoid. Assume the
            # session is still valid and retry next tick.
            logger.warning("account_health inconclusive (%s): %s — treating as ok",
                           type(exc).__name__, exc)
            return "ok"
        return "ok"

    async def fetch_user_stats(self, ig_user_id: str) -> dict[str, Any]:
        """Follower/following + name/avatar for one IG user via the PRIVATE API only.

        Uses user_info_v1 directly (the same private API the inbox reads), never the public
        GraphQL path: instagrapi's user_info falls back to public GraphQL on any failure, and
        that endpoint now returns an anti-bot HTML page → a noisy JSON-parse crash. Skipping
        it keeps this call on the private surface that isn't blocked."""
        client = self._ensure_client()
        info = await asyncio.to_thread(client.user_info_v1, str(ig_user_id))
        return {
            "follower_count": getattr(info, "follower_count", None),
            "following_count": getattr(info, "following_count", None),
            "username": getattr(info, "username", None) or None,
            "full_name": getattr(info, "full_name", None) or None,
            "avatar_url": str(getattr(info, "profile_pic_url", "") or "") or None,
        }

    async def download_media(self, url: str) -> bytes:
        return await download_bounded(url)

    # How many recent posts to scan for new comments per run, and how many comments per post.
    # Small on purpose: a comment walk is a private-API call sequence, and IG throttles it hard.
    _COMMENT_POSTS_SCAN = 12
    _COMMENT_PER_POST = 50

    async def fetch_own_comments(self, since_epoch_us: int | None) -> list[dict[str, Any]]:
        """Comments under OUR OWN recent posts, excluding our own comments. `since_epoch_us`
        bounds the walk cheaply — comments older than the last run are skipped. Best-effort per
        post: one post's extractor crash never aborts the whole walk."""
        client = self._ensure_client()
        own_id = self._resolve_own_id(client)
        since_dt = (
            datetime.fromtimestamp(since_epoch_us / 1_000_000, tz=UTC).replace(tzinfo=None)
            if since_epoch_us else None)
        medias = await asyncio.to_thread(
            client.user_medias_v1, int(own_id), self._COMMENT_POSTS_SCAN)
        out: list[dict[str, Any]] = []
        for m in medias:
            media_pk = str(getattr(m, "pk", "") or "")
            if not media_pk:
                continue
            try:
                comments = await asyncio.to_thread(
                    client.media_comments, media_pk, self._COMMENT_PER_POST)
            except Exception as exc:  # noqa: BLE001 — one post's failure isn't fatal
                logger.warning("IG media_comments failed media=%s: %s", media_pk, exc)
                continue
            code = str(getattr(m, "code", "") or "")
            caption = str(getattr(m, "caption_text", "") or "") or None
            permalink = f"https://www.instagram.com/p/{code}/" if code else None
            for c in comments:
                author = getattr(c, "user", None)
                author_pk = str(getattr(author, "pk", "") or "") or None
                if author_pk == own_id:
                    continue  # our own reply/comment — never react to ourselves
                created = getattr(c, "created_at_utc", None) or getattr(c, "created_at", None)
                created_naive = as_naive_utc(created) if created else None
                if since_dt and created_naive and created_naive <= since_dt:
                    continue
                out.append({
                    "comment_id": str(getattr(c, "pk", "") or ""),
                    "media_id": media_pk,
                    "text": str(getattr(c, "text", "") or ""),
                    "timestamp": int(created_naive.replace(tzinfo=UTC).timestamp() * 1_000_000)
                    if created_naive else None,
                    "author_pk": author_pk,
                    "author_username": str(getattr(author, "username", "") or "") or None,
                    "media_caption": caption,
                    "media_permalink": permalink,
                })
        return out

    async def fetch_user_posts(self, user_pk: str, amount: int = 3) -> list[dict[str, Any]]:
        """Recent posts of ANOTHER account. Same private-API call the own-comment walk uses,
        pointed at someone else's pk — the only difference is whose feed it reads.

        Kept to a handful: we want their latest, not their archive, and each call is a private
        endpoint that IG throttles per account."""
        client = self._ensure_client()
        medias = await asyncio.to_thread(client.user_medias_v1, int(user_pk), amount)
        out: list[dict[str, Any]] = []
        for m in medias:
            media_pk = str(getattr(m, "pk", "") or "")
            if not media_pk:
                continue
            code = str(getattr(m, "code", "") or "")
            taken = getattr(m, "taken_at", None)
            out.append({
                "media_id": media_pk,
                "caption": str(getattr(m, "caption_text", "") or ""),
                "taken_at": as_naive_utc(taken) if taken else None,
                "permalink": f"https://www.instagram.com/p/{code}/" if code else None,
                "like_count": int(getattr(m, "like_count", 0) or 0),
                "comment_count": int(getattr(m, "comment_count", 0) or 0),
            })
        return out

    async def comment_on_media(self, media_pk: str, text: str) -> dict[str, Any]:
        """Top-level comment under someone else's post — `media_comment` with no replied-to id.
        The same endpoint as a reply under our own post; what differs is whose media it is,
        which is why the caller counts these against a separate, much smaller budget."""
        client = self._ensure_client()
        result = await asyncio.to_thread(client.media_comment, media_pk, text)
        return {"pk": str(getattr(result, "pk", "") or "")}

    async def send_comment_reply(self, comment_id: str, text: str) -> dict[str, Any]:
        """Publicly reply to a comment. instagrapi threads a reply by media + replied-to id;
        we look up the comment's media from our own stored row, passed as `comment_id` in the
        form 'media_pk:comment_pk' so the transport needs no extra fetch."""
        client = self._ensure_client()
        media_pk, _, replied_to = comment_id.partition(":")
        result = await asyncio.to_thread(
            client.media_comment, media_pk, text,
            replied_to_comment_id=int(replied_to) if replied_to else None)
        return {"pk": str(getattr(result, "pk", "") or "")}

    async def delete_comment(self, comment_id: str) -> None:
        """Delete a comment under OUR OWN post (spam/abuse moderation). The private API has no
        'hide', but the post owner may delete any comment under their media — the public
        result is the same: it disappears. `comment_id` is 'media_pk:comment_pk'. Raises on
        failure (caller keeps the DB flag and retries next tick)."""
        client = self._ensure_client()
        media_pk, _, comment_pk = comment_id.partition(":")
        await asyncio.to_thread(
            client.comment_bulk_delete, media_pk, [int(comment_pk)])


class EvolutionTransport:
    """Implements channels.whatsapp.WhatsAppTransport over the Evolution API (HTTP)."""

    def __init__(self, *, base_url: str, instance: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._instance = instance
        self._key = api_key

    def _client(self) -> Any:
        import httpx  # lazy: real transport only, never imported by unit tests

        return httpx.AsyncClient(
            base_url=self._base, headers={"apikey": self._key}, timeout=30
        )

    async def fetch_messages(self) -> list[dict[str, Any]]:
        """Newest page of the instance's messages, both directions.

        POST with a body, not GET: v1's GET form answers 404 on v2 and the ingest fails
        with the channel showing "active" — verified against the live server, which
        returns {"messages":{"total":…,"records":[…]}} newest-first."""
        async with self._client() as c:
            r = await c.post(f"/chat/findMessages/{self._instance}", json={})
        r.raise_for_status()
        return [_wa_message(m) for m in _wa_records(r.json())]

    async def send_message(self, remote_jid: str, text: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(
                f"/message/sendText/{self._instance}",
                json={"number": remote_jid, "text": text},
            )
        r.raise_for_status()
        return r.json()

    async def connection_state(self) -> str:
        async with self._client() as c:
            r = await c.get(f"/instance/connectionState/{self._instance}")
        r.raise_for_status()
        return ((r.json().get("instance") or {}).get("state")) or "close"

    async def pair(self) -> str:
        """Create the instance if new, then return the QR as a data: URI to render.

        Evolution answers 409 for an instance that already exists; that is the normal path
        for "the QR expired, show me another one", not an error."""
        async with self._client() as c:
            r = await c.post(
                "/instance/create",
                json={"instanceName": self._instance, "integration": "WHATSAPP-BAILEYS",
                      "qrcode": True},
            )
            if r.status_code not in (409, 403):
                r.raise_for_status()
                qr = _qr_data_uri(r.json())
                if qr:
                    return qr
            r = await c.get(f"/instance/connect/{self._instance}")
        r.raise_for_status()
        return _qr_data_uri(r.json())

    async def forget(self) -> None:
        """Unlink and drop the instance — the manager's phone loses our linked device."""
        async with self._client() as c:
            await c.delete(f"/instance/logout/{self._instance}")
            await c.delete(f"/instance/delete/{self._instance}")


def _qr_data_uri(payload: dict[str, Any]) -> str:
    """Evolution returns the QR under `qrcode.base64` on create and flat on connect."""
    qr = payload.get("qrcode") or payload
    b64 = qr.get("base64") or ""
    if not b64:
        return ""
    return b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"


def _wa_records(payload: Any) -> list[dict[str, Any]]:
    """findMessages answers a bare list on some versions and {messages:{records:[…]}} on
    others. Reading only one shape returns an empty inbox instead of failing."""
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if not isinstance(payload, dict):
        return []
    inner = payload.get("messages")
    if isinstance(inner, list):
        return [m for m in inner if isinstance(m, dict)]
    if isinstance(inner, dict) and isinstance(inner.get("records"), list):
        return [m for m in inner["records"] if isinstance(m, dict)]
    return []


# Every shape whose text a human actually reads. Plain `conversation` covers only the
# simplest bubble: a reply, a link, a photo caption and a voice note all arrive under other
# keys, and reading just the one left most of a real chat looking blank.
_WA_TEXT_KEYS: tuple[tuple[str, str], ...] = (
    ("conversation", ""),
    ("extendedTextMessage", "text"),
    ("imageMessage", "caption"),
    ("videoMessage", "caption"),
    ("documentMessage", "caption"),
    ("documentWithCaptionMessage", "caption"),
    ("buttonsResponseMessage", "selectedDisplayText"),
    ("listResponseMessage", "title"),
    ("templateButtonReplyMessage", "selectedDisplayText"),
    ("ephemeralMessage", ""),
)

# Non-text bubbles we must still record. A voice note is where half an Indonesian sales
# conversation lives; filing it as an empty string would read as silence in the transcript.
_WA_MEDIA_KINDS: tuple[tuple[str, str], ...] = (
    ("imageMessage", "image"),
    ("videoMessage", "video"),
    ("audioMessage", "audio"),
    ("stickerMessage", "image"),
    ("documentMessage", "document"),
    ("documentWithCaptionMessage", "document"),
)


def _wa_body(message: Any) -> dict[str, Any]:
    """Unwrap the envelopes WhatsApp wraps a real message in (disappearing / view-once)."""
    body = message if isinstance(message, dict) else {}
    for _ in range(3):  # bounded: these nest at most twice in practice, never cyclically
        for envelope in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
                         "documentWithCaptionMessage"):
            inner = body.get(envelope)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                body = inner["message"]
                break
        else:
            return body
    return body


def _wa_text(message: Any) -> str:
    body = _wa_body(message)
    for key, field in _WA_TEXT_KEYS:
        value = body.get(key)
        if not field and isinstance(value, str):
            return value
        if field and isinstance(value, dict) and isinstance(value.get(field), str):
            return value[field]
    return ""


def _wa_media_kind(message: Any) -> str | None:
    body = _wa_body(message)
    for key, kind in _WA_MEDIA_KINDS:
        if isinstance(body.get(key), dict):
            return kind
    return None


def _wa_message(raw: dict[str, Any]) -> dict[str, Any]:
    """One Evolution record → the flat shape WhatsAppAdapter maps to InboundMessage.

    `fromMe` used to mean "drop it". On a manager's number that threw away the half we
    linked the device for: how they answer, how fast, what they say about price. It is now
    a DIRECTION, decided here where the raw flag is, not re-derived downstream from who the
    sender looks like — that guess is exactly what mislabelled 1401 Instagram messages."""
    key = raw.get("key") or {}
    message = raw.get("message")
    return {
        "remote_jid": key.get("remoteJid", ""),
        "sender_id": key.get("participant") or key.get("remoteJid", ""),
        "text": _wa_text(message),
        "message_timestamp": raw.get("messageTimestamp"),
        "external_id": key.get("id") or None,
        "direction": "out" if key.get("fromMe") else "in",
        "media_kind": _wa_media_kind(message),
    }


# Graph 400s the WHOLE request over one subfield it dislikes, and fetch_conversations turns
# that into a warning and moves on — so an `attachments` this Page's token is not allowed to
# read would take branch 7 from "photos ingest blank" to "nothing ingests at all", on BOTH
# platform calls, with nothing louder than a log line. These two sets are a ladder: ONLY the
# media field is dropped on a 400, and the poll keeps reading text. The choice is per
# transport instance, i.e. per tick — a transient 400 does not cost the media forever.
#
# `id` is on both rungs and must stay there. It is the message's identity (external_id), so a
# rung without it re-derives a synthetic id from thread+time+text — a different key for the
# same message. Every step of the ladder, in either direction, would then re-ingest the whole
# 25-message window as new rows, and media is excluded from ingest's content dedup, so exactly
# the messages this ladder exists for are the ones that would duplicate. Each duplicate enters
# _store as a fresh inbound: follow-up cycle reset, bot revived on a dormant thread.
_MSG_FIELDS_FULL = "id,from,message,created_time,attachments"
_MSG_FIELDS_TEXT_ONLY = "id,from,message,created_time"


class GraphTransportHTTP:
    """Implements channels.meta_business.GraphTransport over the official Graph API (HTTP)."""

    def __init__(self, *, base_url: str, account_id: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._account_id = account_id
        self._token = token
        self._own_ids_cache: set[str] | None = None
        self._msg_fields = _MSG_FIELDS_FULL

    def _client(self) -> Any:
        import httpx  # lazy: real transport only, never imported by unit tests

        # Token in the Authorization header, NOT a ?access_token= query param: a query-string
        # token lands in the request URL and then in the HTTPStatusError message the caller
        # logs on a 4xx/5xx (a Meta send 400 was printing a live page token to the logs).
        return httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30,
        )

    async def fetch_conversations(self) -> list[dict[str, Any]]:
        """Inbound threads across BOTH inboxes hanging off this Page.

        /{page-id}/conversations defaults to Messenger only. Instagram Direct lives behind the
        same endpoint with platform=instagram, so without that second call an Instagram message
        never arrives — the channel looks connected and healthy and silently reads half of what
        the business receives. Verified live on 2026-08-02: page 207513496325789 returned the
        Messenger history and nothing at all from Direct.

        A failure on one platform must not lose the other: Instagram Direct also answers
        "(#200) the account owner has disabled access to Instagram Direct messages" when the
        business has that setting off, which is a normal state, not an outage.
        """
        import httpx  # lazy: real transport only, never imported by unit tests  # noqa: PLC0415

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for platform in (None, "instagram"):
            try:
                msgs = await self._conversations_for(platform)
            except httpx.HTTPError as exc:
                logger.warning("meta conversations failed (platform=%s): %s",
                               platform or "messenger", exc)
                continue
            # Dedup per MESSAGE, not per thread. The two platform calls are meant to return
            # disjoint sets and something counted twice would be answered twice — but keying
            # on the thread alone collapsed a burst back to a single message, undoing the
            # whole point of reading the thread (caught by tests/test_meta_no_message_loss).
            for m in msgs:
                key = graph_parse.dedup_key(m)
                if key in seen:
                    continue
                seen.add(key)
                out.append(m)
        return out

    @staticmethod
    def _inbound_of(conv: dict[str, Any], msgs: list[dict[str, Any]],
                    who: dict[str, Any], own: set[str]) -> list[dict[str, Any]]:
        """Every message in this thread that the LEAD wrote, oldest first.

        Reading only the newest one silently dropped messages: a lead who sent two lines
        between two polls had the first line thrown away, and the agent answered half the
        question (reported live 2026-08-03). Returning them all costs nothing — ingest
        deduplicates on external_id, so anything already stored is skipped.

        Our own messages are excluded here, not later. Graph returns the whole thread, ours
        included, and the id it reports for a message we SENT differs from the id it reports
        when the same message is READ back — so dedup cannot recognise them and the agent
        would read its own replies as new lead messages and answer itself.
        """
        oldest_first = list(reversed(msgs))  # Graph returns newest-first
        return [
            graph_parse.row_of(conv, m, who, own)
            for m in oldest_first
            if str((m.get("from") or {}).get("id", "")) not in own
        ]

    @staticmethod
    def _participant(conv: dict[str, Any], own: set[str]) -> dict[str, Any]:
        """The human in this conversation: the participant that is not us.

        Matching the LAST message's author looked simpler and was wrong — when we answered
        last, the author is our own Page, and the lead was filed under our own name ("Zapleo
        Soft" showed up as a lead twice). `own` covers both platforms; see _own_ids.
        """
        people = ((conv.get("participants") or {}).get("data")) or []
        for p in people:
            if str(p.get("id", "")) not in own:
                return p
        return {}

    async def _conversations_for(self, platform: str | None) -> list[dict[str, Any]]:
        # The default /conversations page is ~25, so older-but-active chats were dropped. Page
        # through with the `after` cursor up to a config cap (header auth kept — following the
        # opaque paging.next URL would leak the token into logs; the cursor doesn't).
        cap = settings().meta_live_conversations
        page_size = max(1, min(50, cap))
        max_pages = max(1, -(-cap // page_size)) + 2  # safety: cap/page_size pages + slack
        out: list[dict[str, Any]] = []
        threads = 0
        cursor: str | None = None
        async with self._client() as c:
            own = await self._own_ids(c)
            for _ in range(max_pages):
                params: dict[str, Any] = {"limit": page_size}
                if platform:
                    params["platform"] = platform
                if cursor:
                    params["after"] = cursor
                body = await self._conversations_page(c, params)
                for conv in body.get("data", []):
                    msgs = (conv.get("messages") or {}).get("data") or []
                    if not msgs:
                        continue
                    threads += 1
                    who = self._participant(conv, own)
                    out.extend(self._inbound_of(conv, msgs, who, own))
                paging = body.get("paging") or {}
                cursor = ((paging.get("cursors") or {}).get("after"))
                if threads >= cap or not cursor or not paging.get("next"):
                    break
        return out[:cap]

    def _fields_param(self) -> str:
        # participants rides along in the same call — it is what carries the human's name.
        # Graph splits it by platform: Messenger gives `name`, Instagram gives `username`.
        # Without it every lead shows in the inbox as "Lead". `id` on the message is Graph's
        # native mid (dedup key, see graph_parse.dedup_key); `attachments` is the only place a
        # photo/video/voice DM exists — without it Graph reports those messages as an empty
        # string and they ingest blank.
        return ("id,participants,"
                f"messages.limit({_MSGS_PER_THREAD})"
                "{" + self._msg_fields + "}")

    async def _conversations_page(self, c: Any, params: dict[str, Any]) -> dict[str, Any]:
        """One /conversations page, degrading to the text-only field set if Graph refuses.

        A 400 here is the whole channel, not one message: fetch_conversations logs it and
        continues, so both platform calls would return nothing every tick. Text without media
        is a bad day; no messages at all is an outage nobody is paged for."""
        import httpx  # lazy: real transport only, never imported by unit tests  # noqa: PLC0415

        params = {**params, "fields": self._fields_param()}
        try:
            r = await c.get(f"/{self._account_id}/conversations", params=params)
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # getattr, not exc.response: an error raised without one (a wrapped transport
            # failure) must fall through to the caller as the outage it is, not be mistaken
            # for a field Graph rejected.
            if (self._msg_fields == _MSG_FIELDS_TEXT_ONLY
                    or getattr(exc.response, "status_code", None) != 400):
                raise
            logger.warning("meta conversations rejected the media fields (%s) — "
                           "polling text-only for this tick: %s", self._msg_fields, exc)
            self._msg_fields = _MSG_FIELDS_TEXT_ONLY
            params["fields"] = self._fields_param()
            r = await c.get(f"/{self._account_id}/conversations", params=params)
            r.raise_for_status()
        return r.json()

    async def find_conversation_id(self, user_id: str) -> str | None:
        """The conversation id for a person, given only their PSID/IGSID.

        The webhook identifies a sender by PSID and never carries the conversation id, while
        the poll keys every thread on that id — so without this translation the same chat is
        stored twice. /{page-id}/conversations?user_id=… is the documented reverse lookup.

        Both platforms are tried, Messenger first: a Page with Instagram Direct switched off
        answers "(#200) the account owner has disabled access" for the instagram call, which is
        a normal configuration, not an outage. None = we could not establish it, and the caller
        leaves the message to the reconcile poll rather than invent a key.
        """
        import httpx  # lazy: real transport only, never imported by unit tests  # noqa: PLC0415

        async with self._client() as c:
            for platform in (None, "instagram"):
                params: dict[str, Any] = {"user_id": user_id, "fields": "id", "limit": 1}
                if platform:
                    params["platform"] = platform
                try:
                    r = await c.get(f"/{self._account_id}/conversations", params=params)
                    r.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("meta conversation lookup failed (platform=%s): %s",
                                   platform or "messenger", exc)
                    continue
                data = r.json().get("data") or []
                if data and data[0].get("id"):
                    return str(data[0]["id"])
        return None

    async def _own_ids(self, c: Any) -> set[str]:
        """Every id that means "us" inside a participants list.

        On Messenger that is the Page id. On Instagram the participants carry IG-scoped ids and
        our side is the IG business account — a number this transport was never told. Comparing
        against the Page id alone therefore matched nobody, so "the participant that isn't us"
        returned the FIRST one, which is us: sends went to ourselves and Graph answered
        "(#100) no matching user" (subcode 2018001), while the inbox showed our own Page name
        where the lead's should be. Resolved once and cached — it does not change for a Page.
        """
        if self._own_ids_cache is None:
            ids = {str(self._account_id)}
            try:
                r = await c.get(f"/{self._account_id}",
                                params={"fields": "instagram_business_account"})
                r.raise_for_status()
                ig = (r.json().get("instagram_business_account") or {}).get("id")
                if ig:
                    ids.add(str(ig))
            except Exception as exc:  # noqa: BLE001 — a Page without Instagram is normal
                logger.warning("could not resolve own IG id for %s: %s", self._account_id, exc)
            self._own_ids_cache = ids
        return self._own_ids_cache

    async def _resolve_psid(self, thread_id: str, c: Any) -> str:
        """The Send API needs the lead's id, not the conversation id fetch_conversations hands
        back as thread_id — resolve it from the conversation's participants, excluding every id
        that means us (see _own_ids)."""
        own = await self._own_ids(c)
        r = await c.get(f"/{thread_id}", params={"fields": "participants"})
        r.raise_for_status()
        participants = ((r.json().get("participants") or {}).get("data")) or []
        for p in participants:
            if str(p.get("id", "")) not in own:
                return str(p["id"])
        raise RuntimeError(f"no non-self participant found for conversation {thread_id}")

    async def send_message(self, recipient_id: str, text: str) -> dict[str, Any]:
        async with self._client() as c:
            psid = await self._resolve_psid(recipient_id, c)
            r = await c.post(
                f"/{self._account_id}/messages",
                # messaging_type=RESPONSE is REQUIRED by the Send API for a reply inside the
                # standard 24h window — omitting it is a 400 ("param messaging_type must be
                # one of {RESPONSE, UPDATE, MESSAGE_TAG}"), which is exactly the error that
                # piled up on the Meta channel (2026-07-10). RESPONSE is the correct type for
                # answering a user message; an out-of-window send needs a MESSAGE_TAG and is
                # skipped upstream (OutboxSender's window check) rather than sent here.
                json={
                    "messaging_type": "RESPONSE",
                    "recipient": {"id": psid},
                    "message": {"text": text},
                },
            )
        # A 4xx/5xx from Graph must map to SendResult(ok=False) — but raise_for_status() drops
        # Graph's error BODY (subcode + message), leaving only "400 Bad Request" in the log,
        # undiagnosable. Surface the body instead (the URL carries no token — auth is a header).
        if r.status_code >= 400:
            raise RuntimeError(f"Graph send {r.status_code}: {r.text[:300]}")
        data = r.json()
        return {"message_id": data.get("message_id"), "error": data.get("error")}

    async def download_media(self, url: str) -> bytes:
        """Fetch a DM attachment's bytes for the media backfill.

        Redirects are followed here: Graph hands out lookaside.fbsbx.com urls that 302 to the
        real CDN host, and without this the backfill would store the redirect page as if it
        were the photo. Not our authenticated client — the token has no business on a CDN."""
        return await download_bounded(url, follow_redirects=True)

    async def token_debug(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get("/debug_token", params={"input_token": self._token})
        # /debug_token REQUIRES the token as a query param (it's the token being inspected), so
        # it can't move to a header — raise a sanitized error instead of raise_for_status(),
        # whose message embeds the URL (and thus the token) into the caller's log.
        if r.status_code >= 400:
            raise RuntimeError(f"debug_token failed: HTTP {r.status_code}")
        data = (r.json().get("data") or {})
        return {"is_valid": data.get("is_valid", False), "window_open": True}
