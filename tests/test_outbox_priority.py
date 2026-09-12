"""send_outbox must never let a proactive follow-up crowd out a real reply when the
hourly/daily send cap is tight — a thread with a reply pending drains before a
thread with only a follow-up pending. And an INACTIVE channel's rows must not be
picked at all: being the oldest, they monopolised every batch slot and starved the
live channels' sends (2026-07-13, the switched-off Meta channel)."""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import ChannelKind, Stage
from app.worker import wiring


async def _branch(db_session) -> int:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    return b.id


async def _channel(db_session, bid: int, *, active: bool = True) -> int:
    ch = Channel(branch_id=bid, kind=ChannelKind.INSTAGRAM, is_active=active)
    db_session.add(ch)
    await db_session.flush()
    return ch.id


async def _thread(db_session, bid: int, cid: int, ext: str) -> int:
    lead = Lead(branch_id=bid, stage=Stage.QUALIFYING)
    db_session.add(lead)
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=cid, external_thread_id=ext)
    db_session.add(th)
    await db_session.flush()
    return th.id


async def test_reply_thread_drains_before_followup_only_thread(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    t1 = await _thread(db_session, bid, cid, "a")
    t2 = await _thread(db_session, bid, cid, "b")
    # t2's followup was queued FIRST (lower id) — without priority it would drain first
    db_session.add(Outbox(branch_id=bid, thread_id=t2, text="nudge", source="followup"))
    db_session.add(Outbox(branch_id=bid, thread_id=t1, text="reply", source="agent"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [t1, t2]  # reply-bearing thread first despite the later queue slot


async def test_thread_with_both_counts_as_reply_priority(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    t5 = await _thread(db_session, bid, cid, "a")
    t6 = await _thread(db_session, bid, cid, "b")
    db_session.add(Outbox(branch_id=bid, thread_id=t5, text="nudge", source="followup"))
    db_session.add(Outbox(branch_id=bid, thread_id=t5, text="reply", source="manager"))
    db_session.add(Outbox(branch_id=bid, thread_id=t6, text="nudge only", source="followup"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [t5, t6]  # mixed thread still ranks as reply-priority


async def test_oldest_queued_breaks_ties_within_a_tier(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    t8 = await _thread(db_session, bid, cid, "a")
    t9 = await _thread(db_session, bid, cid, "b")
    db_session.add(Outbox(branch_id=bid, thread_id=t8, text="earlier reply", source="agent"))
    db_session.add(Outbox(branch_id=bid, thread_id=t9, text="later reply", source="agent"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [t8, t9]  # lower outbox id (queued first) wins within the same tier


async def test_inactive_channel_rows_never_enter_the_send_batch(db_session) -> None:
    """The starvation bug: a dead channel's rows are the oldest, fill every batch slot, are
    unsendable (channels.get → None), and the live channel's rows never make the batch — the
    whole outbox looks frozen. Inactive-channel rows must be excluded from selection."""
    bid = await _branch(db_session)
    dead = await _channel(db_session, bid, active=False)
    live = await _channel(db_session, bid, active=True)
    dead_threads = [await _thread(db_session, bid, dead, f"d{i}") for i in range(3)]
    live_thread = await _thread(db_session, bid, live, "live")
    for tid in dead_threads:  # queued FIRST — oldest ids, would win every tie-break
        db_session.add(Outbox(branch_id=bid, thread_id=tid, text="stuck", source="agent"))
    db_session.add(Outbox(branch_id=bid, thread_id=live_thread, text="reply", source="agent"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [live_thread]  # only the live channel's thread — no starvation


async def test_reactivation_drains_after_a_real_reply(db_session) -> None:
    """A dormant-reactivation touch is proactive, not a reply — it must go BEHIND a live
    agent reply so a mass dormant harvest never delays an answer to an active lead."""
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    tr = await _thread(db_session, bid, cid, "react")
    ta = await _thread(db_session, bid, cid, "reply")
    # the reactivation was queued FIRST (lower id) — without deprioritization it would win
    db_session.add(Outbox(branch_id=bid, thread_id=tr, text="eh Kak", source="reactivation"))
    db_session.add(Outbox(branch_id=bid, thread_id=ta, text="reply", source="agent"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [ta, tr]  # the live reply drains before the reactivation


async def test_sweep_recovers_outbox_rows_orphaned_in_sending(db_session) -> None:
    """A worker restart (every deploy) between the committed 'sending' claim and the send
    result strands the row in 'sending' forever — the thread hangs mute because ~pending counts
    it as "reply in flight" (live 2026-07-20: threads 4655/4661/4689, 45 rows back to 07-17).
    The branch-wide sweep marks a stale claim 'failed' so the thread re-enters awaiting for a
    fresh reply; a freshly-claimed row still mid-send is left alone."""
    from datetime import UTC, datetime, timedelta

    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    t1 = await _thread(db_session, bid, cid, "a")
    t2 = await _thread(db_session, bid, cid, "b")
    now = datetime.now(UTC).replace(tzinfo=None)
    stale = Outbox(branch_id=bid, thread_id=t1, text="stuck", source="agent",
                   status="sending", sent_at=now - timedelta(minutes=30))
    fresh = Outbox(branch_id=bid, thread_id=t2, text="mid-send", source="agent",
                   status="sending", sent_at=now - timedelta(minutes=1))
    db_session.add(stale)
    db_session.add(fresh)
    await db_session.flush()

    swept = await wiring.sweep_stale_sending(db_session, bid, now)
    assert swept == 1  # only the 30-min-old claim, not the 1-min-old one
    await db_session.refresh(stale)
    await db_session.refresh(fresh)
    # 'canceled', NOT 'failed': a crashed-mid-send is a silent artifact (unknown outcome, never
    # retried), so it must not show as a red '✗ retry?' bubble — fetch_pending hides 'canceled'.
    assert stale.status == "canceled" and stale.error
    assert fresh.status == "sending"  # still inside the grace window — never touched


# ─── канал, который сейчас не доставит, не должен занимать слоты ────────────────

async def _switch_off(db_session, bid: int, cid: int, key: str) -> None:  # noqa: ANN001
    """Поканальный переключатель в false — как его ставит оператор в редакторе канала."""
    from app.adapters.db.models import AppSetting  # noqa: PLC0415
    from app.modules.settings.service import invalidate  # noqa: PLC0415

    db_session.add(AppSetting(branch_id=bid, key=key, value="false", channel_id=cid))
    await db_session.flush()
    invalidate(bid)


async def test_a_read_only_channel_does_not_eat_the_batch(db_session) -> None:
    """12.09.2026: на CRM Jakarta (режим чтения) висели 53 строки от 08–09.09. Они самые
    старые, а `call_failed`/`crm_followthrough` считаются ОТВЕТОМ и сортируются впереди —
    они забирали все 15 слотов каждый тик, и филиал не отправил ничего трое суток, включая
    сообщения менеджера с реквизитами оплаты.

    Тот же отказ, что у выключенного канала в 2026-07-13, только через другую дверь."""
    bid = await _branch(db_session)
    quiet = await _channel(db_session, bid)
    live = await _channel(db_session, bid)
    await _switch_off(db_session, bid, quiet, "replies_enabled")
    stuck = await _thread(db_session, bid, quiet, "quiet")
    fresh = await _thread(db_session, bid, live, "live")
    # Строка на замолчавшем канале СТАРШЕ и считается ответом — без фильтра она была бы первой
    db_session.add(Outbox(branch_id=bid, thread_id=stuck, text="old", source="crm_followthrough"))
    db_session.add(Outbox(branch_id=bid, thread_id=fresh, text="new", source="agent"))
    await db_session.flush()

    assert await wiring.threads_with_pending_outbox(db_session, bid) == [fresh]


async def test_a_paused_channel_does_not_eat_the_batch_either(db_session) -> None:
    """Пауза отправки (бан/чекпоинт) держит очередь намеренно — но занимать ею слоты
    живых каналов незачем: send_next для такой строки возвращает None, не расходуя её."""
    bid = await _branch(db_session)
    paused = await _channel(db_session, bid)
    live = await _channel(db_session, bid)
    await _switch_off(db_session, bid, paused, "sending_enabled")
    held = await _thread(db_session, bid, paused, "paused")
    fresh = await _thread(db_session, bid, live, "live")
    db_session.add(Outbox(branch_id=bid, thread_id=held, text="old", source="agent"))
    db_session.add(Outbox(branch_id=bid, thread_id=fresh, text="new", source="agent"))
    await db_session.flush()

    assert await wiring.threads_with_pending_outbox(db_session, bid) == [fresh]


async def test_the_read_only_rows_are_retired_but_the_paused_ones_are_kept(db_session) -> None:
    """Режим чтения — решение «здесь мы не отвечаем», строка мертва. Пауза временная, её
    очередь обязана дождаться снятия. И сообщение ЧЕЛОВЕКА не стирается ни в каком случае."""
    from sqlmodel import select  # noqa: PLC0415

    bid = await _branch(db_session)
    quiet = await _channel(db_session, bid)
    paused = await _channel(db_session, bid)
    await _switch_off(db_session, bid, quiet, "replies_enabled")
    await _switch_off(db_session, bid, paused, "sending_enabled")
    t_bot = await _thread(db_session, bid, quiet, "bot")
    t_human = await _thread(db_session, bid, quiet, "human")
    t_paused = await _thread(db_session, bid, paused, "paused")
    db_session.add(Outbox(branch_id=bid, thread_id=t_bot, text="a", source="crm_followthrough"))
    db_session.add(Outbox(branch_id=bid, thread_id=t_human, text="b", source="manager"))
    db_session.add(Outbox(branch_id=bid, thread_id=t_paused, text="c", source="agent"))
    await db_session.flush()

    assert await wiring.sweep_undeliverable(db_session, bid) == 1

    rows = {r.thread_id: r.status for r in (await db_session.exec(select(Outbox))).all()}
    assert rows[t_bot] == "skipped"
    assert rows[t_human] == "pending", "сообщение человека не стираем"
    assert rows[t_paused] == "pending", "пауза временная — очередь ждёт"
