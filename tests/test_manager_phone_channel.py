"""Номер менеджера: кто решает, уйдёт ли строка.

Раньше решал канал — и отказывал всем, включая самого менеджера. Это и сломало возврат
лида: из 302 человек на таких номерах 295 не имеют другого канала, значит «верни Степану»
было кнопкой, после которой не могло произойти ничего и никогда.

Теперь решает состояние ЛИДА. Приём переводит его в стадию «менеджер» и гасит тумблер;
пока тумблер выключен, ответ не генерируется и не отправляется. Включает его менеджер, и
тогда Степан пишет туда, где человек написал последним — включая воцап менеджера, потому
что другого места у 295 из 302 просто нет.

Строка при этом уходит от имени менеджера, с его номера. Это осознанное решение владельца
(10.08.2026), а не побочный эффект: см. docs/whatsapp-manager-numbers.md.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.adapters.channels.whatsapp import WhatsAppAdapter
from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.outbox import OutboxSender
from app.modules.settings.service import invalidate
from app.ports.channel import SendResult

# ── адаптер больше не хранит разрешений ───────────────────────────────────────


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def fetch_messages(self) -> list[dict[str, Any]]:
        return []

    async def send_message(self, remote_jid: str, text: str) -> dict[str, Any]:
        self.sent.append((remote_jid, text))
        return {"key": {"id": "wa-1"}}

    async def connection_state(self) -> str:
        return "open"


async def test_any_linked_instance_can_send() -> None:
    """Адаптер знает, КАК отправить, и не знает, СТОИТ ли — это вопрос про лида, и
    отвечать на него в адаптере значило бы держать ответ в двух местах сразу."""
    t = _Transport()
    result = await WhatsAppAdapter(t, instance="i").send_text("62811@s", "hi")

    assert result.ok is True
    assert t.sent == [("62811@s", "hi")]
    assert not hasattr(WhatsAppAdapter(t, instance="i"), "read_only")


# ── отправщик пропускает и человека, и бота ───────────────────────────────────


class _FakeChannel:
    kind = ChannelKind.WHATSAPP

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append((external_thread_id, text))
        return SendResult(ok=True, external_message_id="wa-1")

    async def session_status(self) -> Any:
        return None


async def _queued(s, *, source: str = "agent") -> tuple[int, int]:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP, manager_phone=True)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch.id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=channel.id, external_thread_id="62811@s",
    )
    s.add(thread)
    await s.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    s.add(Outbox(
        branch_id=branch.id, thread_id=thread.id, text="halo", source=source,
        status="pending", scheduled_at=now - timedelta(seconds=5),
    ))
    await s.flush()
    invalidate(branch.id)
    return branch.id, thread.id


@pytest.mark.parametrize("source", ["agent", "followup", "manager"])
async def test_a_queued_line_leaves_a_managers_number(db_session, source: str) -> None:  # noqa: ANN001
    """Запрет снят целиком, а не только для менеджера: раз тумблер лида разрешил боту
    работать этого человека, отвечать ему больше некуда — другого канала у него нет."""
    bid, tid = await _queued(db_session, source=source)
    channel = _FakeChannel()

    row = await OutboxSender(db_session, bid, channel).send_next(tid)

    assert row is not None and row.status == "sent"
    assert channel.sent == [("62811@s", "halo")]


# ── что теперь держит бота: состояние лида ────────────────────────────────────


async def _thread_on(s, *, stage: Stage, bot: bool) -> tuple[int, int]:  # noqa: ANN001
    now = datetime.now(UTC).replace(tzinfo=None)
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP, manager_phone=True)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=stage, agent_enabled=bot)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=channel.id,
                           external_thread_id="62811@s", last_in_at=now)
    s.add(thread)
    await s.flush()
    return branch.id, thread.id


async def test_a_lead_the_manager_holds_is_not_picked_for_a_reply(db_session) -> None:  # noqa: ANN001
    """Состояние, в которое приём ставит каждого, кто впервые написал на такой номер."""
    from app.worker import wiring

    bid, _ = await _thread_on(db_session, stage=Stage.MANAGER, bot=False)
    assert await wiring.threads_awaiting_reply(db_session, bid) == []


async def test_handing_the_lead_back_makes_the_thread_answerable(db_session) -> None:  # noqa: ANN001
    """Ради этого запрет и снят. Раньше здесь был бы пустой список — навсегда, для 295
    лидов из 302, у которых воцап менеджера единственный канал."""
    from app.worker import wiring

    bid, tid = await _thread_on(db_session, stage=Stage.QUALIFYING, bot=True)
    assert await wiring.threads_awaiting_reply(db_session, bid) == [tid]


# ── куда именно отвечать, когда каналов несколько ─────────────────────────────


async def _lead_with(s, *, newest_on_manager_phone: bool):  # noqa: ANN001, ANN202
    """Лид с двумя тредами: наш канал и номер менеджера. Меняется только то, куда пришло
    последнее сообщение."""
    now = datetime.now(UTC).replace(tzinfo=None)
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ig = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM, manager_phone=False)
    wa = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP, manager_phone=True)
    s.add(ig)
    s.add(wa)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=Stage.QUALIFYING)
    s.add(lead)
    await s.flush()
    old, new = (now - timedelta(hours=2), now)
    ig_t = ChannelThread(lead_id=lead.id, channel_id=ig.id, external_thread_id="ig",
                         last_in_at=old if newest_on_manager_phone else new)
    wa_t = ChannelThread(lead_id=lead.id, channel_id=wa.id, external_thread_id="wa",
                         last_in_at=new if newest_on_manager_phone else old)
    s.add(ig_t)
    s.add(wa_t)
    await s.flush()
    return branch.id, ig_t.id, wa_t.id


async def test_the_reply_goes_where_the_lead_last_wrote(db_session) -> None:  # noqa: ANN001
    from app.worker import wiring

    bid, ig_tid, _ = await _lead_with(db_session, newest_on_manager_phone=False)
    assert await wiring.threads_awaiting_reply(db_session, bid) == [ig_tid]


async def test_an_old_thread_is_not_answered_instead_of_the_live_one(db_session) -> None:  # noqa: ANN001
    """Правило «отвечаем туда, где написали последним» никуда не делось и осталось
    единственной защитой от второго голоса: пока человек пишет в воцап, отвечать ему в
    покинутый инстаграм-тред нельзя. Отвечаем в воцап — туда, где он есть."""
    from app.worker import wiring

    bid, ig_tid, wa_tid = await _lead_with(db_session, newest_on_manager_phone=True)
    picked = await wiring.threads_awaiting_reply(db_session, bid)

    assert picked == [wa_tid]
    assert ig_tid not in picked
