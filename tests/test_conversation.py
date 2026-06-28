"""ReplyService + OutboxSender against fake LLM/channel ports — no broker, no transport.

Proves the seam: decide() parses a Decision from a fake LLMPort, enqueue writes a
branch-scoped pending Outbox (invisible to another branch), and send_next drains it via
a fake ChannelPort — flipping status and recording an outgoing Message, or marking failed."""
from __future__ import annotations

import json
from typing import Any

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message, Outbox
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
    assert llm.seen[-1]["content"] == "halo"  # dialog turn included


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


async def test_send_next_none_when_nothing_pending(db_session):
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)

    assert await OutboxSender(s, branch_id, FakeChannel()).send_next(thread_id) is None
