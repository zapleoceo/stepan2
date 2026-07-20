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

from app.config import settings
from app.domain.clock import as_naive_utc

logger = logging.getLogger(__name__)

# Ad click-to-DM attribution codes. Word-boundary matched so an ordinary name that merely
# CONTAINS these letters ("Ahmad", "Nadia", "Murad") is not misfiled as an ad lead.
_AD_ATTR_RE = re.compile(r"\b(ctd|ads?|click.?to.?(dm|direct|message))\b")

# Live polling only needs the most-recent threads (new messages surface at the top);
# deep pagination every minute is slow (each page costs an IG call + 2-5s delay) and
# would push a poll cycle past the cron interval → overlapping runs. Backfill of old
# history is a separate, on-demand path.
_LIVE_THREADS = 20


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

    _MEDIA_MAX_BYTES = 60 * 1024 * 1024  # 60 MB — a DM video well past this is dropped
    _MEDIA_TIMEOUT = 90.0                 # a large video CDN fetch needs more than 30s

    async def download_media(self, url: str) -> bytes:
        """Stream raw media bytes from a CDN url, bounded so a huge video can't OOM the
        worker (the old `r.content` loaded the whole file into memory with a 30s cap)."""
        import httpx  # lazy: real transport only, never imported by unit tests

        # connect kept short; read stretched for a big video over a slow CDN.
        timeout = httpx.Timeout(self._MEDIA_TIMEOUT, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as c, \
                c.stream("GET", url) as r:
            r.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > self._MEDIA_MAX_BYTES:
                    raise ValueError(
                        f"media exceeds {self._MEDIA_MAX_BYTES} bytes — refusing to buffer")
                chunks.append(chunk)
        return b"".join(chunks)

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
        async with self._client() as c:
            r = await c.get(f"/chat/findMessages/{self._instance}")
        r.raise_for_status()
        out: list[dict[str, Any]] = []
        for m in r.json():
            key = m.get("key") or {}
            if key.get("fromMe"):
                continue
            out.append(
                {
                    "remote_jid": key.get("remoteJid", ""),
                    "sender_id": key.get("participant") or key.get("remoteJid", ""),
                    "text": (m.get("message") or {}).get("conversation", ""),
                    "message_timestamp": m.get("messageTimestamp"),
                }
            )
        return out

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


class GraphTransportHTTP:
    """Implements channels.meta_business.GraphTransport over the official Graph API (HTTP)."""

    def __init__(self, *, base_url: str, account_id: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._account_id = account_id
        self._token = token

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
        # The default /conversations page is ~25, so older-but-active chats were dropped. Page
        # through with the `after` cursor up to a config cap (header auth kept — following the
        # opaque paging.next URL would leak the token into logs; the cursor doesn't).
        cap = settings().meta_live_conversations
        page_size = max(1, min(50, cap))
        max_pages = max(1, -(-cap // page_size)) + 2  # safety: cap/page_size pages + slack
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        async with self._client() as c:
            for _ in range(max_pages):
                params: dict[str, Any] = {
                    "fields": "id,messages{from,message,created_time}", "limit": page_size}
                if cursor:
                    params["after"] = cursor
                r = await c.get(f"/{self._account_id}/conversations", params=params)
                r.raise_for_status()
                body = r.json()
                for conv in body.get("data", []):
                    msgs = (conv.get("messages") or {}).get("data") or []
                    if not msgs:
                        continue
                    last = msgs[0]
                    out.append(
                        {
                            "thread_id": conv.get("id", ""),
                            "from_id": (last.get("from") or {}).get("id", ""),
                            "message": last.get("message", ""),
                            "created_time": last.get("created_time"),
                        }
                    )
                paging = body.get("paging") or {}
                cursor = ((paging.get("cursors") or {}).get("after"))
                if len(out) >= cap or not cursor or not paging.get("next"):
                    break
        return out[:cap]

    async def _resolve_psid(self, thread_id: str, c: Any) -> str:
        """The Send API needs the lead's PSID, not the conversation id fetch_conversations
        hands back as thread_id — resolve it via the conversation's participants, picking
        whichever participant isn't our own Page/IG account."""
        r = await c.get(f"/{thread_id}", params={"fields": "participants"})
        r.raise_for_status()
        participants = ((r.json().get("participants") or {}).get("data")) or []
        for p in participants:
            if str(p.get("id", "")) != str(self._account_id):
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
