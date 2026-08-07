"""Pull a WhatsApp channel's whole synced history, not just the newest page.

The live poll reads page one — fifty messages — because that is all a minute-by-minute
cycle needs. A freshly linked device, though, arrives holding months: the first number came
with 1488 messages across thirty pages, and everything past page one was invisible.

This walks the pages oldest-last and hands each batch to the ordinary IngestService, so
dedup, identity resolution and the read-only rules are the same ones the live path uses.
Re-running it is therefore safe and cheap: already-stored messages are skipped by their
external id.

    docker compose run --rm --no-deps api python scripts/wa_backfill.py 18
    docker compose run --rm --no-deps api python scripts/wa_backfill.py --all
"""
from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text

from app.adapters.channels.transports import _wa_message, _wa_records
from app.adapters.db.models import Channel
from app.adapters.db.session import session_scope
from app.config import settings
from app.domain.enums import ChannelKind
from app.modules.leads.ingest import IngestService

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("wa_backfill")

_MAX_PAGES = 200  # a runaway guard, far above any real instance


async def _wa_channels(session, only: int | None) -> list[Channel]:  # noqa: ANN001
    q = "SELECT id FROM channel WHERE kind=:k AND is_active"
    params: dict = {"k": ChannelKind.WHATSAPP.value}
    if only is not None:
        q += " AND id=:id"
        params["id"] = only
    ids = [r[0] for r in (await session.execute(text(q), params)).all()]
    return [c for c in [await session.get(Channel, i) for i in ids] if c is not None]


async def _instance(session, channel_id: int) -> str | None:  # noqa: ANN001
    from app.connectors.session_store import active_session_settings

    dump = await active_session_settings(session, channel_id)
    return dump.get("instance") if dump else None


async def _page(client, instance: str, page: int) -> tuple[list[dict], int]:
    """One page of raw records plus the reported page count."""
    r = await client.post(f"/chat/findMessages/{instance}", json={"page": page})
    r.raise_for_status()
    payload = r.json()
    inner = payload.get("messages") if isinstance(payload, dict) else None
    pages = int(inner.get("pages", 1)) if isinstance(inner, dict) else 1
    return _wa_records(payload), pages


async def _profiles(client, instance: str) -> dict[str, tuple[str | None, str | None]]:
    try:
        r = await client.post(f"/chat/findChats/{instance}", json={})
        r.raise_for_status()
        rows = r.json()
    except Exception as exc:  # noqa: BLE001 — names are decoration, history is not
        logger.warning("  profiles unavailable: %s", exc)
        return {}
    out: dict[str, tuple[str | None, str | None]] = {}
    for row in rows if isinstance(rows, list) else []:
        jid = row.get("remoteJid") or row.get("id") if isinstance(row, dict) else None
        if jid:
            out[str(jid)] = (row.get("pushName") or row.get("name") or None,
                             row.get("profilePicUrl") or None)
    return out


async def _backfill_channel(channel: Channel) -> int:
    import httpx

    from app.adapters.channels.whatsapp import WhatsAppAdapter

    cfg = settings()
    async with session_scope() as session:
        instance = await _instance(session, channel.id or 0)
    if not instance:
        logger.warning("channel %s: no active session — skipped", channel.id)
        return 0

    stored = 0
    async with httpx.AsyncClient(
        base_url=cfg.evolution_url.rstrip("/"),
        headers={"apikey": cfg.evolution_api_key},
        timeout=120,
    ) as client:
        profiles = await _profiles(client, instance)
        _, pages = await _page(client, instance, 1)
        logger.info("channel %s (%s): %d pages", channel.id, channel.handle, pages)
        # Oldest first so a run interrupted halfway leaves a contiguous recent tail rather
        # than a hole in the middle of every conversation.
        for page in range(min(pages, _MAX_PAGES), 0, -1):
            records, _ = await _page(client, instance, page)
            batch = []
            for raw in records:
                msg = _wa_message(raw)
                name, avatar = profiles.get(msg["remote_jid"], (None, None))
                # The message's own pushName wins — the chat list had 4 names in 238 rows,
                # the messages carry one each. Overwriting with the list emptied the field
                # and left 135 nameable chats anonymous.
                msg["sender_name"] = msg.get("sender_name") or name
                msg["sender_avatar"] = avatar
                batch.append(msg)
            # Reuse the adapter's own mapping so the backfill and the live poll cannot
            # disagree about what a message is.
            adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
            inbound = [adapter._to_inbound(m) for m in batch]  # noqa: SLF001
            async with session_scope() as session:
                svc = IngestService(session, channel.branch_id, notifier=None)
                # No notifier: this is history, and a manager pinged about a three-week-old
                # message would learn nothing and stop trusting the alerts.

                written = await svc.ingest(channel.id or 0, inbound)
            stored += len(written)
            logger.info("  page %d/%d → %d new", page, pages, len(written))
    return stored


async def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "--all"
    only = None if arg == "--all" else int(arg)
    async with session_scope() as session:
        channels = await _wa_channels(session, only)
    if not channels:
        logger.warning("no active WhatsApp channels matched %s", arg)
        return
    total = 0
    for channel in channels:
        total += await _backfill_channel(channel)
    logger.info("done: %d new messages", total)


if __name__ == "__main__":
    asyncio.run(main())
