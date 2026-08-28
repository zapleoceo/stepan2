"""Dormant reactivation harvest respects the opt-in flag, cooldown window, gap and cap."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, StageEvent
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.reactivation import (
    MAX_DORMANT_DAYS,
    REACTIVATION_CAP,
    ReactivationService,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _setup(s):
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="ig", account_id="ig",
                 is_active=True)
    s.add(ch)
    await s.flush()
    return b.id, ch.id


async def _dormant_lead(s, bid, chid, *, days_ago: float, reacts=0, last_react_days=None):
    lead = Lead(branch_id=bid, stage=Stage.DORMANT, agent_enabled=False)
    s.add(lead)
    await s.flush()
    now = _now()
    t = ChannelThread(lead_id=lead.id, channel_id=chid, external_thread_id=f"t{lead.id}",
                      last_in_at=now - timedelta(days=days_ago),
                      last_out_at=now - timedelta(days=days_ago + 0.1))
    s.add(t)
    for _ in range(reacts):
        when = now - timedelta(days=last_react_days if last_react_days is not None else 30)
        s.add(StageEvent(branch_id=bid, lead_id=lead.id, thread_id=None,
                         from_stage="dormant", to_stage="nurturing", actor="system",
                         reason="reactivation", created_at=when))
    await s.flush()
    return t.id, lead.id


def _svc(s, bid, *, enabled=True):
    settings = SimpleNamespace(agent_enabled=True, reactivation_enabled=enabled)
    return ReactivationService(s, bid, llm=None, knowledge=None, settings=settings)


async def test_disabled_returns_nothing(db_session) -> None:
    bid, chid = await _setup(db_session)
    await _dormant_lead(db_session, bid, chid, days_ago=5)
    assert await _svc(db_session, bid, enabled=False).due(_now()) == []


async def test_cooldown_window(db_session) -> None:
    bid, chid = await _setup(db_session)
    good, _ = await _dormant_lead(db_session, bid, chid, days_ago=5)      # in [3,21] → due
    await _dormant_lead(db_session, bid, chid, days_ago=1)                # too soon → skip
    await _dormant_lead(db_session, bid, chid, days_ago=MAX_DORMANT_DAYS + 5)  # too old → skip
    due = await _svc(db_session, bid).due(_now())
    assert [t for t, _s, _l in due] == [good], due


async def test_gap_and_cap(db_session) -> None:
    bid, chid = await _setup(db_session)
    fresh, _ = await _dormant_lead(db_session, bid, chid, days_ago=6)
    # already reactivated 3 days ago (< 14-day gap) → skip
    await _dormant_lead(db_session, bid, chid, days_ago=6, reacts=1, last_react_days=3)
    # already reactivated CAP times (long ago) → skip
    await _dormant_lead(db_session, bid, chid, days_ago=6, reacts=REACTIVATION_CAP,
                        last_react_days=40)
    due = await _svc(db_session, bid).due(_now())
    assert [t for t, _s, _l in due] == [fresh], due


# ── the touch must know which ad the lead came from ───────────────────────────

async def test_entry_block_names_the_product_the_lead_came_for(db_session) -> None:
    """Reactivation shipped for weeks without passing an entry block, and the reply and
    follow-up paths both pass one. Without it the ad's prefill is the only thing in the prompt
    that looks like a stated interest — and four of the six touches sent on 26-27.07 quoted it
    back: "Kakak tanya soal jadwal, durasi, sama biaya kursus" is Meta's button text, put in
    the mouth of someone who never typed it."""
    from app.adapters.db.models import Product

    bid, _chid = await _setup(db_session)
    db_session.add(Product(branch_id=bid, slug="vibe_coding", title="Vibe Coding",
                           content="x", is_active=True))
    await db_session.flush()

    block = await _svc(db_session, bid)._entry_block("vibe_coding")
    assert block is not None
    assert "Vibe Coding" in block
    assert "WHAT THEY CAME FOR" in block


async def test_no_product_means_no_entry_block(db_session) -> None:
    """A lead with no mapped product gets nothing rather than an empty claim about what they
    wanted — inventing an interest is worse than admitting there is none on file."""
    bid, _chid = await _setup(db_session)
    assert await _svc(db_session, bid)._entry_block(None) is None
    assert await _svc(db_session, bid)._entry_block("no_such_course") is None


def test_the_framing_bans_the_two_wasted_shapes() -> None:
    """'Remind me who you are' and a bare 'still interested?' are the two ways this touch is
    spent for nothing — both were live on 27.07."""
    from app.modules.conversation.reactivation import _REACTIVATION_FRAMING

    assert "masih tertarik" in _REACTIVATION_FRAMING
    assert "remind you who they are" in _REACTIVATION_FRAMING


# ── the queue drains best-first, not newest-first ─────────────────────────────

async def _lead_with(s, bid, chid, *, days_ago: float, phone=None, dossier=None):
    lead = Lead(branch_id=bid, stage=Stage.DORMANT, agent_enabled=False,
                phone_e164=phone, dossier=dossier)
    s.add(lead)
    await s.flush()
    now = _now()
    t = ChannelThread(lead_id=lead.id, channel_id=chid, external_thread_id=f"t{lead.id}",
                      last_in_at=now - timedelta(days=days_ago),
                      last_out_at=now - timedelta(days=days_ago + 0.1))
    s.add(t)
    await s.flush()
    return t.id


async def test_a_lead_who_named_a_goal_outranks_a_newer_silent_one(db_session) -> None:
    """The backlog is ~2700 threads draining slowly, and 2747 of 4052 leads never wrote two
    sentences of their own. Ordering by recency alone parked the people who described a real
    project — an app for migrant workers, a donor database — behind hundreds of one-word ad
    taps that merely happened later."""
    bid, chid = await _setup(db_session)
    silent_new = await _lead_with(db_session, bid, chid, days_ago=4)
    with_goal_old = await _lead_with(
        db_session, bid, chid, days_ago=40,
        dossier='{"job_to_be_done": "bikin aplikasi buat toko", "pains": []}')
    due = await _svc(db_session, bid).due(_now())
    order = [tid for tid, _slug, _lid in due]
    assert order.index(with_goal_old) < order.index(silent_new)


async def test_a_phone_on_file_outranks_a_goal(db_session) -> None:
    """A number means a human can act on the answer, which is worth more than a good opening."""
    bid, chid = await _setup(db_session)
    goal_only = await _lead_with(db_session, bid, chid, days_ago=4,
                                 dossier='{"job_to_be_done": "belajar coding"}')
    with_phone = await _lead_with(db_session, bid, chid, days_ago=40, phone="+628123456789")
    order = [tid for tid, _s, _l in await _svc(db_session, bid).due(_now())]
    assert order.index(with_phone) < order.index(goal_only)


async def test_an_empty_goal_string_does_not_count(db_session) -> None:
    """Discovery writes job_to_be_done="" when it learned nothing — that must not score."""
    bid, chid = await _setup(db_session)
    empty = await _lead_with(db_session, bid, chid, days_ago=4,
                             dossier='{"job_to_be_done": "", "pains": []}')
    real = await _lead_with(db_session, bid, chid, days_ago=40,
                            dossier='{"job_to_be_done": "bikin dashboard", "pains": []}')
    order = [tid for tid, _s, _l in await _svc(db_session, bid).due(_now())]
    assert order.index(real) < order.index(empty)


def test_the_daily_ceiling_is_a_hundred() -> None:
    """34 per pass × 3 passes. The old 40 was set the week of the soft-block and never
    re-derived; the branch sends 334-960 a day and averages about one block signature."""
    from app.modules.conversation.reactivation import BATCH_PER_RUN

    assert BATCH_PER_RUN == 34


# ── лид, который нас не читает ────────────────────────────────────────────────


async def _sent(s, bid, chid, tid, *, n: int, after_seen: bool, seen_days_ago: float) -> None:
    """n исходящих; after_seen=True кладёт их ПОСЛЕ квитанции о прочтении."""
    from app.adapters.db.models import ChannelThread as _CT
    from app.adapters.db.models import Message
    now = _now()
    seen = now - timedelta(days=seen_days_ago)
    thread = await s.get(_CT, tid)
    thread.lead_seen_at = seen
    s.add(thread)
    for i in range(n):
        shift = timedelta(hours=i + 1)
        s.add(Message(branch_id=bid, thread_id=tid, channel_id=chid, external_id=f"o{tid}-{i}",
                      direction="out", sent_by="bot", text="halo",
                      occurred_at=(seen + shift) if after_seen else (seen - shift)))
    await s.flush()


async def test_two_unread_messages_stop_the_touch(db_session) -> None:
    """Тред 4422: лид последний раз открывал переписку 20 июля, мы написали после этого
    четырежды и ни разу не получили ответа. Следующее сообщение — уже не касание, а спам.
    На филиале 1 таких тредов 646 из 2679, подходящих по остальным условиям."""
    bid, chid = await _setup(db_session)
    tid, _lid = await _dormant_lead(db_session, bid, chid, days_ago=5)
    await _sent(db_session, bid, chid, tid, n=2, after_seen=True, seen_days_ago=6)

    assert await _svc(db_session, bid).due(_now()) == []


async def test_one_unread_message_still_allows_a_touch(db_session) -> None:
    """Одно непрочитанное — человек мог просто не открыть приложение. Правило про ДВА."""
    bid, chid = await _setup(db_session)
    tid, lid = await _dormant_lead(db_session, bid, chid, days_ago=5)
    await _sent(db_session, bid, chid, tid, n=1, after_seen=True, seen_days_ago=6)

    assert [r[0] for r in await _svc(db_session, bid).due(_now())] == [tid]


async def test_messages_the_lead_did_read_do_not_count(db_session) -> None:
    """Два сообщения ДО квитанции — прочитанные, и молчание после них это другой разговор."""
    bid, chid = await _setup(db_session)
    tid, lid = await _dormant_lead(db_session, bid, chid, days_ago=5)
    await _sent(db_session, bid, chid, tid, n=2, after_seen=False, seen_days_ago=6)

    assert [r[0] for r in await _svc(db_session, bid).due(_now())] == [tid]


async def test_no_receipt_at_all_does_not_block(db_session) -> None:
    """lead_seen_at заполняет только Instagram, и внутри него он пуст у 1860 тредов из 4220.
    Трактовать «не знаем» как «не читает» значило бы выключить реактивацию у воцапа и Meta
    целиком, а у инстаграма на 44%."""
    bid, chid = await _setup(db_session)
    tid, lid = await _dormant_lead(db_session, bid, chid, days_ago=5)
    from app.adapters.db.models import Message
    now = _now()
    for i in range(3):  # три исходящих и НИ ОДНОЙ квитанции
        db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=chid,
                               external_id=f"n{i}", direction="out", sent_by="bot",
                               text="halo", occurred_at=now - timedelta(days=4, hours=i)))
    await db_session.flush()

    assert [r[0] for r in await _svc(db_session, bid).due(_now())] == [tid]


async def test_a_read_only_channel_gets_no_touch(db_session) -> None:
    """Канал в режиме чтения не должен порождать реактивации.

    Гейт стоял только на основном ответе, а этот сборщик о нём не знал: 26.08.2026 в очереди
    филиала 1 лежало 68 реактиваций на CRM Jakarta, замолчавшем каналом в тот же день.
    Отправка их не пропустила бы, но текст к тому моменту уже сочинён и оплачен брокеру.

    Отсекать надо ДО LIMIT: у этой выборки он есть, и тред, выброшенный после, уже съел слот,
    которого ждал достижимый лид.
    """
    from app.adapters.db.models import AppSetting  # noqa: PLC0415

    bid, chid = await _setup(db_session)
    await _dormant_lead(db_session, bid, chid, days_ago=5)
    assert await _svc(db_session, bid).due(_now()), "контроль: без запрета касание есть"

    db_session.add(AppSetting(branch_id=bid, key="replies_enabled",
                              value="false", channel_id=chid))
    await db_session.flush()

    assert await _svc(db_session, bid).due(_now()) == []
