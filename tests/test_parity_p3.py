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
from app.modules.conversation.decision import Decision
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

def test_ready_subtype_is_derived_from_the_dossier() -> None:
    """It used to be parsed off the model's own JSON. Since 2026-07-26 the selling model is not
    asked whether the lead is ready — discovery reports readiness from the lead's own words and
    to_legacy stamps the subtype from that."""
    from app.modules.conversation.decision import TurnDecision
    from app.modules.conversation.dossier import LeadDossier

    ready = TurnDecision(reply="ok").to_legacy(LeadDossier(readiness="ready"))
    assert ready.ready is True and ready.ready_subtype == "deal"
    exploring = TurnDecision(reply="ok").to_legacy(LeadDossier(readiness="exploring"))
    assert exploring.ready is False and exploring.ready_subtype is None


def test_source_hint_only_for_known_entry_points() -> None:
    from app.modules.conversation.prompt import source_hint
    assert "paid ads" in (source_hint("ad_clicktomsg") or "")
    assert "stories" in (source_hint("story") or "")
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


def test_manager_note_block_wraps_with_header_or_none() -> None:
    from app.modules.conversation.prompt import manager_note_block
    block = manager_note_block("checked, not ready yet — needs budget confirmed")
    assert block is not None
    assert "MANAGER NOTE" in block
    assert "checked, not ready yet" in block
    assert manager_note_block(None) is None
    assert manager_note_block("   ") is None  # blank note → nothing injected


def test_fmt_llm_meta_free_time_and_id() -> None:
    from app.modules.conversation.delivery import _fmt_llm_meta
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


def test_the_stage_comes_from_the_dossier_not_from_the_model() -> None:
    """The old test pinned a fallback for an off-contract stage label. There is no label any
    more: the schema stopped asking, the field came off TurnDecision on 2026-07-28, and the
    stage is read from what the lead actually revealed."""
    import json

    from app.domain.enums import Stage
    from app.modules.conversation.decision import TurnDecision, parse_turn_decision
    from app.modules.conversation.dossier import LeadDossier, Objection

    # A volunteered stage is ignored rather than trusted or rejected.
    d = parse_turn_decision(json.dumps({"reply": "hi", "stage": "greeting"}))
    assert not hasattr(d, "stage")

    assert TurnDecision(reply="hi").to_legacy(LeadDossier()).stage == Stage.QUALIFYING
    assert TurnDecision(reply="hi").to_legacy(
        LeadDossier(pains=["takut telat"], desired_state=["kerja remote"])).stage \
        == Stage.PRESENTING
    assert TurnDecision(reply="hi").to_legacy(
        LeadDossier(objections=[Objection("mahal")])).stage == Stage.OBJECTION


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


async def test_dialog_char_budget_trims_oldest_of_a_wordy_thread(db_session) -> None:
    """Thread 452's newest 30 messages carried 12.8k chars — the count cap alone doesn't
    bound a wordy thread. The char budget drops the OLDEST tail; the newest messages stay
    verbatim (dedup/don't-repeat compare against them), and the single newest message is
    always kept even if it alone exceeds the budget."""
    from app.modules.conversation.repository import _DIALOG_CHAR_BUDGET

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add_all([ch, lead])
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-2")
    db_session.add(thread)
    await db_session.flush()
    per_msg = _DIALOG_CHAR_BUDGET // 4 - 20  # 4 fit within the budget, the 5th overflows
    for i in range(5):
        db_session.add(Message(
            branch_id=b.id, thread_id=thread.id, channel_id=ch.id, external_id=f"w{i}",
            direction="in", sent_by="lead", text=f"m{i}:" + "x" * per_msg,
            occurred_at=_NOW - timedelta(minutes=(5 - i)),
        ))
    await db_session.flush()
    dialog = await MessageRepo(db_session, b.id).dialog(thread.id)
    assert len(dialog) == 4                      # oldest of the 5 trimmed by the char budget
    assert dialog[-1].text.startswith("m4:")     # newest kept
    assert dialog[0].text.startswith("m1:")      # m0 dropped

    # a single oversized newest message is still returned (never an empty dialog)
    thread2 = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-3")
    db_session.add(thread2)
    await db_session.flush()
    db_session.add(Message(
        branch_id=b.id, thread_id=thread2.id, channel_id=ch.id, external_id="big",
        direction="in", sent_by="lead", text="y" * (_DIALOG_CHAR_BUDGET + 100),
        occurred_at=_NOW))
    await db_session.flush()
    assert len(await MessageRepo(db_session, b.id).dialog(thread2.id)) == 1


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
            if self.seen_messages is None:  # only the FIRST call is what this test examines
                self.seen_messages = messages
            # A discovery move so the pitch gate (which fires on an unlabelled "give_value"
            # default against an empty dossier) doesn't spend a second call and overwrite
            # seen_messages with the correction turn instead.
            return ('{"reply":"ok","stage":"presenting","move":"discover_situation"}',
                    {"model": "fake", "cost_usd": 0.0})

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
    from app.modules.conversation.delivery import _split_bubbles
    assert _split_bubbles("hello") == ["hello"]
    assert _split_bubbles("a ||| b ||| c") == ["a", "b", "c"]
    assert _split_bubbles("a|||b|||c|||d") == ["a", "b", "c d"]  # overflow merged into last
    assert _split_bubbles("   |||   ") == []


def test_two_pipes_split_too_because_the_model_writes_them() -> None:
    """Контракт просит три пайпа, модель иногда пишет два — и тогда «||» уезжало лиду прямо
    в текст, а весь ход шёл одним куском. Тред 4422, 11.08.2026: «Halo Kak muhammadfaqihh,
    masih ingat MinStep? 👋 || Gimana nih…». С 27.07 так утекло пять сообщений."""
    from app.modules.conversation.delivery import _split_bubbles
    assert _split_bubbles("Halo Kak! 👋 || Gimana nih?") == ["Halo Kak! 👋", "Gimana nih?"]
    assert _split_bubbles("a |||| b") == ["a", "b"]


def test_a_single_pipe_is_not_a_separator() -> None:
    """Одиночный пайп встречается в обычном тексте, и разрывать по нему значило бы рвать
    сообщения на ровном месте."""
    from app.modules.conversation.delivery import _split_bubbles
    assert _split_bubbles("Rp 1.000.000 | tanpa potongan") == ["Rp 1.000.000 | tanpa potongan"]


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
