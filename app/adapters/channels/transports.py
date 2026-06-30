"""Real transports — the only place that touches httpx / instagrapi.

Each implements one of the adapter transport Protocols. Third-party imports are lazy
(inside methods) so these modules import without the deps present and unit tests can
inject fakes instead. Swap the underlying API here; adapters stay untouched."""
from __future__ import annotations

import asyncio
from typing import Any


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
                 proxy: str = "") -> None:
        self._username = username
        self._session_settings = session_settings
        self._proxy = proxy
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from instagrapi import Client  # lazy: keep instagrapi out of import path/tests

            client = Client()
            if self._proxy:
                client.set_proxy(self._proxy)  # та же гео, что при логине — иначе checkpoint
            client.set_settings(self._session_settings)
            self._client = client
        return self._client

    async def fetch_threads(self) -> list[dict[str, Any]]:
        client = self._ensure_client()
        own_id = str(client.user_id) if client.user_id else None
        out: list[dict[str, Any]] = []
        # Raw private API gives ad_context_data / send_attribution not in the pydantic model
        for endpoint in ("direct_v2/inbox/", "direct_v2/pending_inbox/"):
            try:
                raw = await asyncio.to_thread(
                    client.private_request, endpoint,
                    params={"visual_message_return_type": "unseen", "limit": "20"},
                )
            except Exception:
                continue
            for t in (raw.get("inbox") or {}).get("threads", []):
                items = t.get("items") or []
                if not items:
                    continue
                last = items[0]
                last_uid = str(last.get("user_id", ""))
                if own_id and last_uid == own_id:
                    continue  # last msg is ours — no new inbound
                users = t.get("users") or []
                lead_u = next((u for u in users if str(u.get("pk", "")) != own_id), None)
                acd = t.get("ad_context_data") or {}
                pm = t.get("professional_metadata") or {}
                out.append({
                    "thread_id": str(t.get("thread_id", "")),
                    "sender_id": last_uid,
                    "sender_username": (lead_u or {}).get("username") or None,
                    "sender_name": (lead_u or {}).get("full_name") or None,
                    "sender_avatar": str((lead_u or {}).get("profile_pic_url") or "") or None,
                    "text": last.get("text") or "",
                    "timestamp": last.get("timestamp"),
                    "ad_id": str(acd["ad_id"]) if acd.get("ad_id") else None,
                    "ad_media_id": str(pm["ad_ig_media_id"]) if pm.get("ad_ig_media_id") else None,
                    "ad_preview_url": acd.get("ad_picture_url") or None,
                    "lead_source": _detect_lead_source(t, (lead_u or {}).get("pk")),
                })
        return out

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]:
        client = self._ensure_client()
        message = await asyncio.to_thread(
            client.direct_send, text, thread_ids=[int(thread_id)]
        )
        return {"item_id": message.id}

    async def account_health(self) -> str:
        client = self._ensure_client()
        try:
            await asyncio.to_thread(client.get_timeline_feed)
        except Exception as exc:  # instagrapi raises ChallengeRequired/LoginRequired here
            return "challenge" if "challenge" in type(exc).__name__.lower() else "expired"
        return "ok"


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
