"""Real transports — the only place that touches httpx / instagrapi.

Each implements one of the adapter transport Protocols. Third-party imports are lazy
(inside methods) so these modules import without the deps present and unit tests can
inject fakes instead. Swap the underlying API here; adapters stay untouched."""
from __future__ import annotations

import asyncio
from typing import Any

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
        if "ctd" in low or "ad" in low:
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

    async def fetch_threads(self) -> list[dict[str, Any]]:
        from .ig_parse import item_content  # noqa: PLC0415

        client = self._ensure_client()
        own_id = str(client.user_id) if client.user_id else None
        out: list[dict[str, Any]] = []
        seen_threads: set[str] = set()
        # Raw private API gives ad_context_data / send_attribution not in the pydantic
        # model, and survives shared-media items that crash instagrapi's own extractor.
        # inbox/ = accepted chats; pending_inbox/ = message requests (cold ad leads).
        for endpoint in ("direct_v2/inbox/", "direct_v2/pending_inbox/"):
            try:
                threads = await asyncio.to_thread(_paged_threads, client, endpoint)
            except Exception as exc:  # noqa: BLE001
                import logging  # noqa: PLC0415
                logging.getLogger(__name__).warning("IG %s failed: %s", endpoint, exc)
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
                    out.append({
                        **base,
                        # client_context is IG's own idempotency key — fall back to it so
                        # an item without item_id still gets a STABLE id (else the synthetic
                        # fallback drifts by timestamp precision and dupes the message).
                        "item_id": str(item.get("item_id") or item.get("client_context") or "")
                        or None,
                        "direction": "out" if (own_id and uid == own_id) else "in",
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
        except Exception as exc:  # instagrapi raises ChallengeRequired/LoginRequired here
            return "challenge" if "challenge" in type(exc).__name__.lower() else "expired"
        return "ok"

    async def fetch_user_stats(self, ig_user_id: str) -> dict[str, Any]:
        """Follower/following counts for one IG user (heavy private-API call)."""
        client = self._ensure_client()
        info = await asyncio.to_thread(client.user_info, int(ig_user_id))
        return {
            "follower_count": getattr(info, "follower_count", None),
            "following_count": getattr(info, "following_count", None),
            "username": getattr(info, "username", None) or None,
            "full_name": getattr(info, "full_name", None) or None,
            "avatar_url": str(getattr(info, "profile_pic_url", "") or "") or None,
        }

    async def download_media(self, url: str) -> bytes:
        """Fetch raw media bytes from a CDN url (instagrapi item ids not needed here)."""
        import httpx  # lazy: real transport only, never imported by unit tests

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url)
        r.raise_for_status()
        return r.content


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

        return httpx.AsyncClient(
            base_url=self._base,
            params={"access_token": self._token},
            timeout=30,
        )

    async def fetch_conversations(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(
                f"/{self._account_id}/conversations",
                params={"fields": "id,messages{from,message,created_time}"},
            )
        r.raise_for_status()
        out: list[dict[str, Any]] = []
        for conv in r.json().get("data", []):
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
        return out

    async def send_message(self, recipient_id: str, text: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.post(
                f"/{self._account_id}/messages",
                json={"recipient": {"id": recipient_id}, "message": {"text": text}},
            )
        data = r.json()
        return {"message_id": data.get("message_id"), "error": data.get("error")}

    async def token_debug(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get("/debug_token", params={"input_token": self._token})
        r.raise_for_status()
        data = (r.json().get("data") or {})
        return {"is_valid": data.get("is_valid", False), "window_open": True}
