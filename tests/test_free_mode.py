"""Free reply mode — the model sells, the code guards only the money.

Pins the mode's four promises: the prompt prefix is byte-stable (the broker's cache anchor),
decisive turns ride chat:sales with a chat:smart fallback, the scripted gates and notes stay
out of the way, and the money gate keeps exactly its scripted severity.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.dossier import LeadDossier
from app.modules.conversation.free_mode import build_messages_free
from app.modules.conversation.reply import ESCALATION_HOLD_REPLY, ReplyService
from app.modules.settings.repository import SettingRepo

_NOW = datetime.now(UTC).replace(tzinfo=None)
_KB = "KB FACTS: Vibe Coding — DP Rp 500.000, total Rp 9.000.000"

# A dossier that routes SMART (open money talk) and already has_discovery(), so the
# discovery-extraction backstop never fires and call counts stay exact.
_HOT = LeadDossier(pains=["takut telat"], desired_state=["kerja remote"],
                   readiness="considering").to_json()
# A dossier that routes FAST: no objections, no money talk, not weighing it up.
_COLD = LeadDossier(pains=["takut telat"], desired_state=["kerja remote"],
                    readiness="exploring").to_json()


class _LLM:
    def __init__(self, *answers: str, fail_caps: frozenset[str] = frozenset()) -> None:
        self._answers = list(answers) or [_answer()]
        self._fail_caps = fail_caps
        self.capabilities: list[str] = []
        self.messages: list[list[dict]] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        if kw.get("workflow") == "discovery":
            return '{"job_to_be_done":"","pains":[],"desired_state":[],"objections":[]}', \
                {"model": "fake", "cost_usd": 0.0}
        self.capabilities.append(kw.get("capability", ""))
        self.messages.append(messages)
        if kw.get("capability") in self._fail_caps:
            raise TimeoutError("chain down")
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return answer, {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


class _Knowledge:
    def __init__(self, context: str = _KB) -> None:
        self._context = context

    async def knowledge_context(self, product_slug, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._context

    async def full_knowledge_context(self, lang=None):  # noqa: ANN001, ANN201
        return self._context

    async def objection_snippets(self, categories):  # noqa: ANN001, ANN201
        return ""

    async def market_snippets(self, categories):  # noqa: ANN001, ANN201
        return ""


def _answer(**over) -> str:  # noqa: ANN003
    payload = {"reply": "halo kak", "move": "build_rapport", "stage": "qualifying"}
    payload.update(over)
    return json.dumps(payload)


async def _thread(s, *, dossier: str | None = None,  # noqa: ANN001
                  texts=(("out", "hai kak, aku dari IT STEP"), ("in", "mau tanya dong")),  # noqa: ANN001
                  ) -> tuple[int, int, int]:
    b = Branch(name="F", lang="id")
    s.add(b)
    await s.flush()
    await SettingRepo(s).upsert("reply_mode", "free", branch_id=b.id)
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, dossier=dossier)
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-f1")
    s.add(th)
    await s.flush()
    for i, (direction, text) in enumerate(texts):
        s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id=f"f{i}",
                      direction=direction, sent_by="lead" if direction == "in" else "bot",
                      text=text, occurred_at=_NOW))
    await s.flush()
    return b.id, th.id, lead.id


def _service(session, branch_id: int, llm: _LLM) -> ReplyService:  # noqa: ANN001
    return ReplyService(session, branch_id, llm, _Knowledge())


# ── routing ───────────────────────────────────────────────────────────────────

async def test_decisive_turn_rides_sales_with_one_call(db_session) -> None:  # noqa: ANN001
    """One generation, no critic, no gates — and it goes to the Sonnet-first chain."""
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    llm = _LLM()
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "halo kak"
    assert llm.capabilities == ["chat:sales"]


async def test_routine_turn_stays_on_fast(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session, dossier=_COLD)
    llm = _LLM()
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert llm.capabilities == ["chat:fast"]


async def test_sales_chain_down_falls_back_to_smart(db_session) -> None:  # noqa: ANN001
    """A capped/down chat:sales degrades to today's quality, never to silence."""
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    llm = _LLM(fail_caps=frozenset({"chat:sales"}))
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "halo kak"
    assert llm.capabilities == ["chat:sales", "chat:smart"]


async def test_unparseable_sales_body_retries_on_smart(db_session) -> None:  # noqa: ANN001
    """A garbage chat:sales body costs a retry, not the turn."""
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    llm = _LLM("sonnet had a bad day", _answer())
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "halo kak"
    assert llm.capabilities == ["chat:sales", "chat:smart"]


async def test_tool_envelope_wrapped_decision_is_unwrapped(db_session) -> None:  # noqa: ANN001
    """Anthropic via the broker's forced-tool JSON mode intermittently wraps the decision in
    {"parameters": {...}} (measured live, ~half of chat:sales turns) — unwrap, don't skip."""
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    wrapped = json.dumps({"parameters": json.loads(_answer(reply="dari envelope kak"))})
    llm = _LLM(wrapped)
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "dari envelope kak"
    assert llm.capabilities == ["chat:sales"]


# ── freedom ───────────────────────────────────────────────────────────────────

async def test_model_keeps_its_own_move_label(db_session) -> None:  # noqa: ANN001
    """Free mode never coerces the move to the scripted enum — it's telemetry there."""
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    llm = _LLM(_answer(move="Comfort Then Close!"))
    svc = _service(db_session, bid, llm)
    assert await svc.decide(tid) is not None
    assert svc.last_decision.move == "comfort_then_close"


async def test_pitch_with_empty_dossier_ships(db_session) -> None:  # noqa: ANN001
    """The scripted pitch gate escalated a close on an empty dossier; free mode trusts the
    model — only money stays gated."""
    bid, tid, _ = await _thread(db_session, dossier=None)
    llm = _LLM(_answer(reply="Gas daftar Vibe Coding sekarang?", move="close"))
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "Gas daftar Vibe Coding sekarang?"
    assert not decision.needs_manager


# ── the money gate keeps its severity ─────────────────────────────────────────

async def test_invented_price_still_escalates(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    bad = _answer(reply="Biayanya Rp 5.750.000 aja kak")
    llm = _LLM(bad, bad)  # the rewrite repeats the invented figure
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == ESCALATION_HOLD_REPLY
    assert decision.needs_manager


async def test_grounded_price_passes(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session, dossier=_HOT)
    llm = _LLM(_answer(reply="DP-nya Rp 500.000 kak, total Rp 9.000.000"))
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply.startswith("DP-nya Rp 500.000")
    assert not decision.needs_manager


# ── the cache anchor ──────────────────────────────────────────────────────────

def test_prompt_prefix_is_byte_stable_across_leads_and_turns() -> None:
    """messages[0] is the broker's prompt-cache anchor: nothing per-lead or per-turn may
    leak into it. Everything variable lives in the second system message."""
    kb = "KB FACTS: harga Rp 9.000.000"
    a = build_messages_free(
        kb, [], "id", LeadDossier(pains=["a"], desired_state=["b"]),
        coaching_notes=["selalu sopan"], source_block="[ad entry]",
        name_block="[lead name: Budi]", manager_note="vip", now_block="[today: Rabu]",
        is_first_reply=True)
    b = build_messages_free(
        kb, [], "id", LeadDossier(),
        coaching_notes=None, source_block=None,
        name_block="[lead name: Sari]", manager_note=None, now_block="[today: Kamis]",
        is_first_reply=False)
    assert a[0] == b[0]
    assert a[0]["role"] == "system"
    assert "Budi" not in a[0]["content"] and "Rabu" not in a[0]["content"]
    assert "Budi" in a[1]["content"] and "Sari" in b[1]["content"]


def test_default_mode_is_scripted() -> None:
    from app.modules.settings.schema import field_for
    assert field_for("reply_mode").default == "scripted"
