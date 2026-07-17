"""ReplyService + OutboxSender against fake LLM/channel ports — no broker, no transport.

Proves the seam: decide() parses a Decision from a fake LLMPort, enqueue writes a
branch-scoped pending Outbox (invisible to another branch), and send_next drains it via
a fake ChannelPort — flipping status and recording an outgoing Message, or marking failed."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    KnowledgeDoc,
    Lead,
    Message,
    Outbox,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation import Decision, OutboxSender, ReplyService
from app.modules.conversation.repository import OutboxRepo
from app.modules.knowledge import KnowledgeService
from app.ports.channel import SendResult


class FakeLLM:
    """Returns a fixed JSON decision; records the messages it was handed."""

    def __init__(self, decision: dict[str, Any]) -> None:
        self._payload = json.dumps(decision)
        self.seen: list[dict[str, Any]] | None = None
        self.json_required = False

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        capability: str = "chat:fast",
        require_json_schema: bool = False,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **_kw: Any,
    ) -> tuple[str, dict[str, Any]]:
        self.seen = messages
        self.json_required = require_json_schema
        return self._payload, {"cost_usd": 0.0, "model": "fake"}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class FakeChannel:
    """ChannelPort double: succeeds (or fails) on send_text and records the call."""

    kind = ChannelKind.INSTAGRAM

    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok
        self.sent: list[tuple[str, str]] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append((external_thread_id, text))
        if self._ok:
            return SendResult(ok=True, external_message_id="ext-1")
        return SendResult(ok=False, error="channel down")

    async def session_status(self) -> Any:
        return None


_DECISION = {
    "reply": "Halo! Vibe Coding mulai 1.2jt.",
    "stage": "qualifying",
    "product_slug": "vibe",
    "ready": False,
    "needs_manager": False,
}


async def _branch(s, name: str = "Jakarta", lang: str = "id") -> int:
    b = Branch(name=name, lang=lang)
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000 via transfer BCA atau QRIS."))
    return b.id


async def _thread_with_inbound(s, branch_id: int, *, text: str = "halo") -> int:
    channel = Channel(branch_id=branch_id, kind=ChannelKind.INSTAGRAM)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch_id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=channel.id, external_thread_id="ig-100",
        product_slug="vibe",
    )
    s.add(thread)
    await s.flush()
    s.add(Message(
        branch_id=branch_id, thread_id=thread.id, channel_id=channel.id,
        external_id="in-1", direction="in", sent_by="lead", text=text,
    ))
    await s.flush()
    return thread.id


def _reply_service(s, branch_id: int, llm: FakeLLM) -> ReplyService:
    return ReplyService(s, branch_id, llm, KnowledgeService(s, branch_id))


async def test_decide_returns_decision_from_fake_llm(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    llm = FakeLLM(_DECISION)

    decision = await _reply_service(s, branch_id, llm).decide(thread_id)

    assert isinstance(decision, Decision)
    assert decision.reply == _DECISION["reply"]
    assert decision.stage is Stage.QUALIFYING
    assert decision.product_slug == "vibe"
    assert llm.json_required is True  # require_json_schema flowed through
    assert llm.seen[0]["role"] == "system"
    # dialog turn included — not necessarily last: a situational/format nudge is appended
    # after it on purpose, so the model reads the instruction closest to its own turn
    assert any(m["content"] == "halo" for m in llm.seen)


async def test_decide_none_without_dialog(db_session):
    s = db_session
    branch_id = await _branch(s)
    channel = Channel(branch_id=branch_id, kind=ChannelKind.INSTAGRAM)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch_id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=channel.id, external_thread_id="ig-x")
    s.add(thread)
    await s.flush()

    assert await _reply_service(s, branch_id, FakeLLM(_DECISION)).decide(thread.id) is None


async def test_enqueue_writes_pending_outbox_isolated_per_branch(db_session):
    s = db_session
    branch_a = await _branch(s, "Jakarta")
    branch_b = await _branch(s, "Hanoi", lang="vi")
    thread_id = await _thread_with_inbound(s, branch_a)
    decision = Decision(
        reply="queued line", stage=Stage.QUALIFYING, product_slug="vibe",
        ready=False, needs_manager=False,
    )

    row = await _reply_service(s, branch_a, FakeLLM(_DECISION)).enqueue_reply(thread_id, decision)

    assert row is not None
    assert row.status == "pending"
    assert row.branch_id == branch_a
    assert row.text == "queued line"

    queued_a = await OutboxRepo(s, branch_a).oldest_pending(thread_id)
    assert queued_a is not None and queued_a.id == row.id
    assert await OutboxRepo(s, branch_b).list() == []  # branch B sees no outbox of A


async def test_send_next_sends_flips_sent_and_records_outgoing(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    s.add(Outbox(branch_id=branch_id, thread_id=thread_id, text="hello out"))
    await s.flush()
    channel = FakeChannel(ok=True)

    sent = await OutboxSender(s, branch_id, channel).send_next(thread_id)

    assert sent is not None
    assert sent.status == "sent"
    assert sent.sent_at is not None
    assert channel.sent == [("ig-100", "hello out")]  # routed to thread's external id

    out_msgs = [
        m for m in await ReplyService(
            s, branch_id, FakeLLM(_DECISION), KnowledgeService(s, branch_id)
        ).messages.dialog(thread_id)
        if m.direction == "out"
    ]
    assert len(out_msgs) == 1
    assert out_msgs[0].text == "hello out"
    assert out_msgs[0].sent_by == "agent"
    assert out_msgs[0].external_id == "ext-1"


async def test_send_next_failure_marks_failed_and_records_nothing(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    s.add(Outbox(branch_id=branch_id, thread_id=thread_id, text="will fail"))
    await s.flush()
    channel = FakeChannel(ok=False)

    sent = await OutboxSender(s, branch_id, channel).send_next(thread_id)

    assert sent is not None
    assert sent.status == "failed"
    assert sent.error == "channel down"
    assert sent.sent_at is None

    dialog = await ReplyService(
        s, branch_id, FakeLLM(_DECISION), KnowledgeService(s, branch_id)
    ).messages.dialog(thread_id)
    assert all(m.direction == "in" for m in dialog)  # no outgoing message recorded


async def test_send_next_soft_block_retries_then_gives_up(db_session):
    """A soft block (challenge/rate) used to retry forever. Cap it — once attempts are
    exhausted the row gives up as 'failed' instead of requeuing every _RETRY_AFTER forever."""
    from app.modules.conversation.outbox import _MAX_SOFT_BLOCK_ATTEMPTS

    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    row = Outbox(branch_id=branch_id, thread_id=thread_id, text="stuck",
                attempts=_MAX_SOFT_BLOCK_ATTEMPTS - 1)
    s.add(row)
    await s.flush()

    class _ChallengeChannel(FakeChannel):
        async def send_text(self, external_thread_id, text):  # noqa: ANN001, ANN201
            return SendResult(ok=False, error="challenge_required")

    sent = await OutboxSender(s, branch_id, _ChallengeChannel()).send_next(thread_id)
    assert sent is not None
    assert sent.attempts == _MAX_SOFT_BLOCK_ATTEMPTS  # last allowed retry
    assert sent.status == "pending"
    sent.scheduled_at = datetime.now(UTC).replace(tzinfo=None)  # force it due again
    s.add(sent)
    await s.flush()

    sent2 = await OutboxSender(s, branch_id, _ChallengeChannel()).send_next(thread_id)
    assert sent2 is not None
    assert sent2.status == "failed"  # attempts exhausted — gives up instead of retrying again
    assert sent2.error == "challenge_required"


async def test_send_next_none_when_nothing_pending(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)

    assert await OutboxSender(s, branch_id, FakeChannel()).send_next(thread_id) is None


# ── sanitize ──────────────────────────────────────────────────────────────────

def test_clean_reply_strips_zero_width():
    from app.modules.conversation.sanitize import clean_reply
    assert clean_reply("Halo​ Kakak") == "Halo Kakak"


def test_clean_reply_removes_fake_phone_line():
    from app.modules.conversation.sanitize import clean_reply
    text = "Silakan hubungi kami!\n📱 Telepon: +62 812 3456 7890\nTerima kasih"
    result = clean_reply(text)
    assert "+62 812" not in result
    assert "Silakan" in result and "Terima kasih" in result


def test_clean_reply_keeps_official_number():
    from app.modules.conversation.sanitize import clean_reply
    line = "📱 Telepon: +62 811 1314 400"
    assert clean_reply(line) == line


def test_clean_reply_replaces_em_dash():
    from app.modules.conversation.sanitize import clean_reply
    assert clean_reply("Vibe Coding—kursus") == "Vibe Coding - kursus"


# ── decision: manager_question + kb_gap ──────────────────────────────────────

def test_parse_decision_extracts_manager_question():
    from app.modules.conversation.decision import parse_decision
    raw = json.dumps({
        "reply": "Aku sambungkan ke tim.",
        "stage": "manager",
        "product_slug": None,
        "ready": False,
        "needs_manager": True,
        "manager_question": "Lead minta cicilan khusus untuk bulan Juli.",
        "kb_gap": "Promo July tidak ada di KB.",
    })
    d = parse_decision(raw)
    assert d.needs_manager is True
    assert d.manager_question == "Lead minta cicilan khusus untuk bulan Juli."
    assert d.kb_gap == "Promo July tidak ada di KB."


def test_parse_decision_manager_question_defaults_to_none():
    from app.modules.conversation.decision import parse_decision
    d = parse_decision(json.dumps({
        "reply": "ok", "stage": "qualifying",
        "product_slug": None, "ready": False, "needs_manager": False,
    }))
    assert d.manager_question is None
    assert d.kb_gap is None


def test_parse_decision_hard_stop():
    from app.modules.conversation.decision import parse_decision
    on = parse_decision(json.dumps({"reply": "Maaf, aku berhenti.", "stage": "dormant",
                                    "hard_stop": True}))
    assert on.hard_stop is True
    off = parse_decision(json.dumps({"reply": "ok", "stage": "qualifying"}))
    assert off.hard_stop is False  # absent → not a hard stop


# ── manager alert ─────────────────────────────────────────────────────────────

class FakeNotifier:
    """Records send() calls (into the lead's topic)."""

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []

    async def create_topic(self, *, name: str, icon_emoji=None) -> int:  # noqa: ANN001, ARG002
        return 1

    async def send(self, *, text: str, topic_id: Any = None) -> str:  # noqa: ARG002
        self.sends.append({"text": text, "topic_id": topic_id})
        return "ok"


async def test_enqueue_reply_triggers_alert_when_needs_manager(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    notifier = FakeNotifier()
    decision = Decision(
        reply="Sambungkan ke tim.",
        stage=Stage.MANAGER,
        product_slug=None,
        ready=False,
        needs_manager=True,
        manager_question="Lead tanya diskon khusus.",
    )
    svc = ReplyService(s, branch_id, FakeLLM(_DECISION), KnowledgeService(s, branch_id),
                       notifier=notifier)

    row = await svc.enqueue_reply(thread_id, decision)

    assert row is not None
    assert len(notifier.sends) == 1
    assert "Lead tanya diskon khusus." in notifier.sends[0]["text"]


async def test_enqueue_reply_no_alert_without_needs_manager(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    notifier = FakeNotifier()
    decision = Decision(
        reply="Oke!",
        stage=Stage.QUALIFYING,
        product_slug=None,
        ready=False,
        needs_manager=False,
    )
    svc = ReplyService(s, branch_id, FakeLLM(_DECISION), KnowledgeService(s, branch_id),
                       notifier=notifier)

    await svc.enqueue_reply(thread_id, decision)

    assert len(notifier.sends) == 0
