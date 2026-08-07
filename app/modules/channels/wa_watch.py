"""Watch the linked WhatsApp devices, because a dead one looks exactly like a quiet day.

A linked device drops for ordinary reasons — the phone loses signal, WhatsApp rotates the
session, the manager restarts their handset — and Evolution reconnects on its own within
minutes. It also drops for reasons only a human can fix: the manager unlinked us, or the
phone stayed offline past WhatsApp's fourteen-day limit.

Both look identical from here: messages simply stop. Nothing raises, nothing turns red, and
the first sign is somebody eventually noticing an inbox that has not moved. So the answer is
not an alert on every blip — those get muted within a week — but an alert on a session that
has stayed down long enough to mean something, and a silent restore for one that healed.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel, ChannelSession
from app.config import settings
from app.domain.enums import ChannelKind, SessionStatus
from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

# Long enough that a reconnect finishes unseen, short enough that a real break is caught
# the same morning. Evolution's own retries settle inside a couple of minutes.
DOWN_GRACE = timedelta(minutes=15)


async def _is_linked(row: ChannelSession, channel: Channel) -> bool | None:
    """True when the phone is linked, False when it is not, None when we could not ask.

    Built straight off the session row rather than through the connector's port builder:
    that one hands back only ACTIVE sessions, so the moment the health gate froze a channel
    it also became unprobeable — and a channel nobody can probe never recovers.

    None matters too. An Evolution we cannot reach says nothing about the manager's phone,
    and reporting our own outage as theirs would send someone to re-scan a working QR."""
    from app.adapters.channels.transports import EvolutionTransport  # noqa: PLC0415
    from app.adapters.crypto import decrypt  # noqa: PLC0415

    try:
        dump = json.loads(decrypt(row.secret_enc))
        cfg = settings()
        transport = EvolutionTransport(
            base_url=dump.get("base_url") or cfg.evolution_url,
            instance=dump["instance"],
            api_key=dump.get("api_key") or cfg.evolution_api_key,
        )
        return await transport.connection_state() == "open"
    except Exception as exc:  # noqa: BLE001 — an unreachable server is a state, not a crash
        logger.warning("wa watch: cannot read state of channel %s: %s", channel.id, exc)
        return None


async def _sessions(session: AsyncSession, channel_id: int) -> ChannelSession | None:
    rows = await session.exec(
        select(ChannelSession).where(ChannelSession.channel_id == channel_id)
    )
    return rows.scalars().first()


async def watch(
    session: AsyncSession, branch_id: int, notifier: NotifierPort | None = None,
) -> int:
    """Probe every WhatsApp channel of one branch. Returns how many are down.

    Restores a session that healed by itself: the ingest health gate freezes a channel out
    of ACTIVE on any non-open state, and a frozen channel is never probed again by the
    normal loops — so without this a two-minute reconnect would have cost a permanent
    outage and a pointless QR re-scan."""
    channels = (await session.exec(
        select(Channel).where(
            Channel.branch_id == branch_id,
            Channel.kind == ChannelKind.WHATSAPP,
            Channel.is_active.is_(True),  # type: ignore[attr-defined]
        )
    )).scalars().all()

    now = datetime.now(UTC).replace(tzinfo=None)
    down = 0
    for channel in channels:
        row = await _sessions(session, channel.id or 0)
        if row is None:
            continue  # never paired — not a failure, just an empty channel
        linked = await _is_linked(row, channel)
        if linked is None:
            continue  # our side is unreachable — say nothing about their phone
        if linked:
            await _recover(session, row, channel, now)
            continue
        down += 1
        await _report_down(session, row, channel, now, notifier)
    return down


async def _recover(
    session: AsyncSession, row: ChannelSession, channel: Channel, now: datetime,
) -> None:
    row.last_ok_at = now
    if row.status != SessionStatus.ACTIVE:
        row.status = SessionStatus.ACTIVE
        logger.info("wa watch: channel %s (%s) came back on its own",
                    channel.id, channel.handle)
    session.add(row)


async def _report_down(
    session: AsyncSession,
    row: ChannelSession,
    channel: Channel,
    now: datetime,
    notifier: NotifierPort | None,
) -> None:
    """Alert once per outage, and only after the grace window.

    Keyed off last_ok_at rather than a counter so a worker restart cannot reset the clock
    and hide an outage that has been running for hours."""
    since = row.last_ok_at
    if since is None:
        row.last_ok_at = now  # first sighting — start the clock, do not alert yet
        session.add(row)
        return
    if now - since < DOWN_GRACE:
        return  # still inside the window Evolution usually heals in
    if row.status == SessionStatus.EXPIRED:
        return  # already reported this outage
    row.status = SessionStatus.EXPIRED
    session.add(row)
    minutes = int((now - since).total_seconds() // 60)
    logger.error("wa watch: channel %s (%s) down for %d min",
                 channel.id, channel.handle, minutes)
    if notifier is None:
        return
    await notifier.send(text=(
        f"⚠️ WhatsApp «{channel.handle}» отключён {minutes} мин.\n\n"
        "Диалоги по этому номеру не приходят. Обычно связь восстанавливается сама; "
        "если нет — откройте канал в панели и привяжите телефон заново по QR."
    ))
