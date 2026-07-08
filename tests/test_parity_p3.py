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


def test_build_messages_merges_consecutive_same_role() -> None:
    from types import SimpleNamespace

    from app.modules.conversation.prompt import build_messages
    dialog = [
        SimpleNamespace(direction="in", text="hi"),
        SimpleNamespace(direction="in", text="are you there?"),    # consecutive user
        SimpleNamespace(direction="out", text="yes"),
        SimpleNamespace(direction="out", text="how can I help?"),  # consecutive assistant
        SimpleNamespace(direction="in", text="  "),                # empty → dropped
        SimpleNamespace(direction="in", text="price?"),
    ]
    msgs = build_messages("persona", dialog, "en")
    # strict user/assistant alternation after the system message (Anthropic requirement)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "hi\nare you there?" in msgs[1]["content"]
    assert "yes\nhow can I help?" in msgs[2]["content"]
    assert msgs[3]["content"] == "price?"  # blank turn dropped


def test_source_hint_only_for_known_entry_points() -> None:
    from app.modules.conversation.prompt import source_hint
    assert "paid ad" in (source_hint("ad_clicktomsg") or "")
    assert "story" in (source_hint("story") or "")
    assert source_hint(None) is None
    assert source_hint("organic") is None  # unknown source → no assumption


def test_lead_name_hint_rejects_handles_keeps_real_names() -> None:
    from app.modules.conversation.prompt import lead_name_hint
    assert "Ade" in (lead_name_hint("Ade Putra") or "")  # real name → first name used
    assert "Budi" in (lead_name_hint("Budi") or "")
    assert lead_name_hint("user8842") is None            # digits → handle
    assert lead_name_hint("vibecoding.id") is None        # dot → handle
    assert lead_name_hint("cool_guy") is None             # underscore → handle
    assert lead_name_hint("@someone") is None             # at → handle
    assert lead_name_hint(None) is None
    assert lead_name_hint("   ") is None
    assert lead_name_hint("A") is None                    # too short to be a name


def test_build_messages_injects_entry_hint() -> None:
    from types import SimpleNamespace

    from app.modules.conversation.prompt import build_messages
    dialog = [SimpleNamespace(direction="in", text="halo")]
    msgs = build_messages("persona", dialog, "en", source_block="ENTRY: paid ad, be warm.")
    assert "ENTRY: paid ad" in msgs[0]["content"]
    plain = build_messages("persona", dialog, "en")
    assert "ENTRY:" not in plain[0]["content"]  # no block → nothing injected


def test_manager_note_block_wraps_with_header_or_none() -> None:
    from app.modules.conversation.prompt import manager_note_block
    block = manager_note_block("checked, not ready yet — needs budget confirmed")
    assert block is not None
    assert "MANAGER NOTE" in block
    assert "checked, not ready yet" in block
    assert manager_note_block(None) is None
    assert manager_note_block("   ") is None  # blank note → nothing injected


def test_build_messages_injects_manager_note() -> None:
    """Per-lead override (2026-07-08): a manager who demotes a wrongly-ready lead can leave
    Stepan a reason so it doesn't just mark ready=true again next turn — distinct from
    CoachingNote, which is branch-wide, not per-lead."""
    from types import SimpleNamespace

    from app.modules.conversation.prompt import build_messages
    dialog = [SimpleNamespace(direction="in", text="halo")]
    msgs = build_messages("persona", dialog, "en", manager_note="not actually ready yet")
    assert "MANAGER NOTE" in msgs[0]["content"]
    assert "not actually ready yet" in msgs[0]["content"]
    plain = build_messages("persona", dialog, "en")
    assert "MANAGER NOTE" not in plain[0]["content"]  # no note → nothing injected


def test_fmt_llm_meta_free_time_and_id() -> None:
    from app.modules.conversation.reply import _fmt_llm_meta
    free = _fmt_llm_meta({"model": "x/mistral-large-latest", "tokens_in": 537,
                          "tokens_out": 131, "cost_usd": 0.0, "elapsed_ms": 8231,
                          "request_id": "abc123def456"})
    assert "mistral-large-latest" in free and "537↑131↓" in free
    assert "free" in free and "$" not in free   # zero cost → free
    assert "8.2s" in free                        # seconds when >= 1s
    assert "#abc123de" in free                   # short broker request id
    assert " | " in free                         # pipe-separated
    # order: time | id | cost | tokens | model — all on one line
    assert free.index("8.2s") < free.index("#abc123de") < free.index("free") \
        < free.index("537↑131↓") < free.index("mistral-large-latest")

    paid = _fmt_llm_meta({"model": "gpt", "cost_usd": 0.0021, "elapsed_ms": 450})
    assert "$0.0021" in paid and "450ms" in paid  # ms when < 1s


def test_parse_decision_tolerates_off_contract_stage() -> None:
    import json

    from app.domain.enums import Stage
    # an LLM stage the enum doesn't know must NOT crash the reply — fall back, keep talking
    assert parse_decision(json.dumps(
        {"reply": "hi", "stage": "greeting"})).stage == Stage.QUALIFYING
    assert parse_decision(json.dumps(
        {"reply": "hi"})).stage == Stage.QUALIFYING  # missing stage too
    assert parse_decision(json.dumps(
        {"reply": "hi", "stage": "PRESENTING"})).stage == Stage.PRESENTING  # case-insensitive


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


async def test_decide_appends_user_turn_when_dialog_ends_on_assistant(db_session) -> None:
    """REGRESSION: a re-triggered reply_pending tick can call decide() with a dialog
    whose newest message is the bot's OWN previous reply (threads_awaiting_reply is
    meant to prevent this, but wiring.try_lock_thread's own docstring documents the
    race it closes only on Postgres). Mistral hard-rejects an assistant-trailing
    messages array ("Expected last role User or Tool ... but got assistant", code
    3230) — 285 such errors/24h were observed live, all from this path. decide()
    must append a synthetic user turn so every provider gets a valid shape."""
    class _SpyLLM:
        def __init__(self) -> None:
            self.seen_messages: list[dict] | None = None

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
            self.seen_messages = messages
            return '{"reply":"ok","stage":"presenting"}', {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001, ANN201
            return [[0.0] for _ in texts]

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
    db_session.add(Message(branch_id=b.id, thread_id=thread.id, channel_id=ch.id,
                           external_id="m1", direction="in", sent_by="lead", text="hi",
                           occurred_at=_NOW - timedelta(minutes=2)))
    db_session.add(Message(branch_id=b.id, thread_id=thread.id, channel_id=ch.id,
                           external_id="m2", direction="out", sent_by="bot", text="hello!",
                           occurred_at=_NOW - timedelta(minutes=1)))
    await db_session.flush()

    llm = _SpyLLM()
    svc = ReplyService(db_session, b.id, llm, KnowledgeService(db_session, b.id),
                       branch_settings=_parse({}), notifier=None)
    await svc.decide(thread.id)
    assert llm.seen_messages is not None
    assert llm.seen_messages[-2]["role"] == "assistant"   # the bot's real last reply
    assert llm.seen_messages[-1]["role"] == "user"         # synthetic nudge appended


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
    # Openhouse is a notify-only side channel, not a hand-off — the bot must keep talking
    # (see reply.py's _stage_for/_handoff_openhouse), so stage stays put and the bot stays on.
    assert lead.stage == Stage.PRESENTING and lead.ready_subtype == "openhouse"
    assert lead.agent_enabled is True
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
    # every bubble of one reply now shows the broker line (same LLM call → same meta)
    assert rows[0].llm_info is not None and rows[1].llm_info is not None
    assert rows[0].llm_info == rows[1].llm_info
