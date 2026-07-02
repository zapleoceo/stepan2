"""S1 parity (P3): markdown stripping, context cap, outbox source priority,
ready_subtype parsed end-to-end (deal vs openhouse)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    ManagerAlert,
    Message,
    Outbox,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import Decision, parse_decision
from app.modules.conversation.repository import _MAX_CONTEXT_MSGS, MessageRepo, OutboxRepo
from app.modules.conversation.sanitize import clean_reply
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import _parse

_NOW = datetime.now(UTC).replace(tzinfo=None)


# ─── markdown stripping ───────────────────────────────────────────────────────

def test_clean_reply_strips_markdown() -> None:
    out = clean_reply("**Halo** kak __penting__\n## Judul\n- item satu\n* item dua")
    assert "**" not in out and "__" not in out
    assert "Halo" in out and "penting" in out and "Judul" in out
    assert "##" not in out
    assert "• item satu" in out and "• item dua" in out


def test_clean_reply_keeps_prices_and_single_star() -> None:
    out = clean_reply("Harga 1.2jt, diskon 5*2 minggu")
    assert "1.2jt" in out and "5*2" in out  # single * (not bold) left intact


# ─── ready_subtype parsing ────────────────────────────────────────────────────

def test_parse_decision_ready_subtype() -> None:
    import json
    base = {"reply": "ok", "stage": "ready", "ready": True}
    assert parse_decision(json.dumps({**base, "ready_subtype": "openhouse"})).ready_subtype \
        == "openhouse"
    assert parse_decision(json.dumps({**base, "ready_subtype": "DEAL"})).ready_subtype == "deal"
    assert parse_decision(json.dumps({**base, "ready_subtype": "bogus"})).ready_subtype is None
    assert parse_decision(json.dumps(base)).ready_subtype is None


# ─── context cap ──────────────────────────────────────────────────────────────

async def test_dialog_capped_to_recent(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add(ch)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(thread)
    await db_session.flush()
    for i in range(_MAX_CONTEXT_MSGS + 15):
        db_session.add(Message(
            branch_id=b.id, thread_id=thread.id, channel_id=ch.id, external_id=f"m{i}",
            direction="in", sent_by="lead", text=f"msg{i}",
            occurred_at=_NOW - timedelta(minutes=(_MAX_CONTEXT_MSGS + 15 - i)),
        ))
    await db_session.flush()
    dialog = await MessageRepo(db_session, b.id).dialog(thread.id)
    assert len(dialog) == _MAX_CONTEXT_MSGS
    assert dialog[-1].text == f"msg{_MAX_CONTEXT_MSGS + 14}"  # newest kept, oldest-first
    assert dialog[0].text == "msg15"  # oldest 15 dropped


# ─── outbox source priority ───────────────────────────────────────────────────

async def test_oldest_pending_prioritizes_manager_then_agent_then_followup(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add(ch)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(thread)
    await db_session.flush()
    sched = _NOW - timedelta(minutes=1)
    # insert followup first (older), then agent, then manager — priority must beat time
    for src in ("followup", "agent", "manager"):
        db_session.add(Outbox(branch_id=b.id, thread_id=thread.id, text=src,
                              source=src, status="pending", scheduled_at=sched))
    await db_session.flush()
    row = await OutboxRepo(db_session, b.id).oldest_pending(thread.id)
    assert row is not None and row.source == "manager"


# ─── openhouse hand-off end-to-end ────────────────────────────────────────────

async def test_openhouse_handoff_kind_and_subtype(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.PRESENTING, phone_e164="+6281234567890")
    db_session.add(ch)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(thread)
    await db_session.flush()
    db_session.add(Message(branch_id=b.id, thread_id=thread.id, channel_id=ch.id,
                           external_id="m1", direction="in", sent_by="lead", text="daftar",
                           occurred_at=_NOW))
    await db_session.flush()

    svc = ReplyService(db_session, b.id, _FakeLLM(), KnowledgeService(db_session, b.id),
                       branch_settings=_parse({}), notifier=None)
    decision = Decision(reply="ok", stage=Stage.PRESENTING, product_slug=None,
                        ready=True, needs_manager=False, ready_subtype="openhouse")
    await svc.enqueue_reply(thread.id, decision)
    assert lead.stage == Stage.READY and lead.ready_subtype == "openhouse"
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_openhouse"


class _FakeLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return '{"reply":"ok","stage":"presenting"}', {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


# ─── ||| multi-bubble split ───────────────────────────────────────────────────

def test_split_bubbles() -> None:
    from app.modules.conversation.reply import _split_bubbles
    assert _split_bubbles("hello") == ["hello"]
    assert _split_bubbles("a ||| b ||| c") == ["a", "b", "c"]
    assert _split_bubbles("a|||b|||c|||d") == ["a", "b", "c d"]  # overflow merged into last
    assert _split_bubbles("   |||   ") == []


async def test_enqueue_splits_into_staggered_bubbles(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    db_session.add(ch)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(thread)
    await db_session.flush()
    db_session.add(Message(branch_id=b.id, thread_id=thread.id, channel_id=ch.id,
                           external_id="m1", direction="in", sent_by="lead", text="halo",
                           occurred_at=_NOW))
    await db_session.flush()

    svc = ReplyService(db_session, b.id, _FakeLLM(), KnowledgeService(db_session, b.id),
                       branch_settings=_parse({}), notifier=None)
    svc._last_llm_meta = {"model": "m", "cost_usd": 0.01}  # normally set by decide()
    decision = Decision(reply="Halo kak|||Ada info menarik", stage=Stage.QUALIFYING,
                        product_slug=None, ready=False, needs_manager=False)
    await svc.enqueue_reply(thread.id, decision)
    rows = list((await db_session.exec(
        select(Outbox).where(Outbox.thread_id == thread.id).order_by(Outbox.scheduled_at)
    )).all())
    assert [r.text for r in rows] == ["Halo kak", "Ada info menarik"]
    assert rows[1].scheduled_at > rows[0].scheduled_at  # staggered
    assert rows[0].llm_info is not None and rows[1].llm_info is None  # cost on first only
