"""ReplyService + OutboxSender against fake LLM/channel ports — no broker, no transport.

Proves the seam: decide() parses a Decision from a fake LLMPort, enqueue writes a
branch-scoped pending Outbox (invisible to another branch), and send_next drains it via
a fake ChannelPort — flipping status and recording an outgoing Message, or marking failed."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    KnowledgeDoc,
    Lead,
    Message,
    Outbox,
    Product,
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
        # `.seen` is overwritten by every chat() call — a first-reply turn is always SMART
        # (all replies ride the sales chain now), which also runs the critic's own
        # separate review call
        # on the same LLM, so `.seen` alone can't tell "the main decide() call" apart from
        # "the critic's own prompt". Keep every call so assertions can target the first one.
        self.calls_seen: list[list[dict[str, Any]]] = []
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
        self.calls_seen.append(messages)
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
    # No price, no product pitch — this fixture is about decide()'s plumbing (routing,
    # dialog assembly, JSON parsing), not the money/pitch gates, so it must not trip them
    # (an uninvited price fires the pitch gate regardless of discovery — see money_gate.py).
    "reply": "Oh siap Kak, boleh tau dulu mau belajar buat apa?",
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


async def _thread_with_inbound(
    s, branch_id: int, *, text: str = "halo", dossier: str | None = None,
) -> int:
    channel = Channel(branch_id=branch_id, kind=ChannelKind.INSTAGRAM)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch_id, dossier=dossier)
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


async def test_first_reply_to_ad_tap_is_written_by_the_model(db_session):
    """The tap used to ship a fixed template that opened with the DP figure. Measured over 30
    live days it was answered 14.3% of the time against 36.3% for a written first reply — and
    quoting money before the lead says a word contradicts the contract everywhere else. The
    model writes it now; the note tells it nothing was actually asked and holds the price."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(
        s, branch_id, text="Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?")
    llm = FakeLLM(_DECISION)

    decision = await _reply_service(s, branch_id, llm).decide(thread_id)

    assert isinstance(decision, Decision)
    assert decision.stage is Stage.QUALIFYING
    assert llm.calls_seen, "the tap must reach the model, not a template"
    note = "\n".join(
        m["content"] for m in llm.calls_seen[0] if m["role"] == "system")
    # The note states what the prefill is — and that this one message carries no figure.
    # Leaving that to the model's judgement was measured on 2026-07-26 over 819 pure-prefill
    # threads: 16.1% answered when the opener carried a number against 36.3% when it did not.
    # The 24%-of-453-never-got-a-figure finding still stands, and the note answers it in the
    # same breath: money the moment they write one thing of their own, just not before.
    assert "tapped an ad" in note
    assert "prefill" in note
    assert "no price" in note and "money is fair game" in note


async def test_ad_tap_note_names_the_mapped_product(db_session):
    """The ad→product mapping is the one thing the tap does tell us — the note carries it so
    discovery anchors on that skill instead of a generic "what are you looking for"."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id, text="📷 itstep_jakarta")
    thread = (await s.exec(select(ChannelThread).where(ChannelThread.id == thread_id))).first()
    s.add(Message(branch_id=branch_id, thread_id=thread_id, channel_id=thread.channel_id,
                  external_id="in-2", direction="in", sent_by="lead",
                  text="Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?"))
    s.add(Product(branch_id=branch_id, slug="vibe", title="Vibe Coding", is_active=True))
    await s.flush()
    llm = FakeLLM(_DECISION)

    await _reply_service(s, branch_id, llm).decide(thread_id)

    note = "\n".join(m["content"] for m in llm.calls_seen[0] if m["role"] == "system")
    assert "for Vibe Coding" in note


async def test_bare_ack_first_message_from_ad_reaches_the_model(db_session):
    """thread 5097: an ad-click lead cleared the prefill and sent just 'iyaaaa' — as
    uninformative as a tap, and classified the same way. It goes to the model with the tap
    note rather than to a template."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id, text="iyaaaa")
    thread = (await s.exec(select(ChannelThread).where(ChannelThread.id == thread_id))).first()
    thread.ad_id = "AD123"
    s.add(thread)
    await s.flush()
    llm = FakeLLM(_DECISION)

    decision = await _reply_service(s, branch_id, llm).decide(thread_id)

    assert decision is not None
    assert llm.calls_seen, "an ad-context ack must reach the model"


async def test_bare_ack_first_message_without_ad_gets_the_clarify_template(db_session):
    """'iyaaaa' with no ad context says nothing a generation could build on — the neutral
    clarify template answers it deterministically, at zero broker cost (opener.Entry.JUNK)."""
    from app.modules.conversation.opener import JUNK_OPENER

    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id, text="iyaaaa")
    llm = FakeLLM(_DECISION)

    decision = await _reply_service(s, branch_id, llm).decide(thread_id)

    assert decision is not None and decision.reply == JUNK_OPENER
    assert llm.calls_seen == []  # deterministic — no broker call


async def test_a_templated_opener_says_so_on_the_bubble(db_session):
    """No broker call means no broker line — but a BLANK chip is what a lost meta looks like
    too (the 2026-07-22 regression). The templated turn labels itself instead."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id, text="iyaaaa")
    svc = _reply_service(s, branch_id, FakeLLM(_DECISION))

    decision = await svc.decide(thread_id)
    assert decision is not None
    await svc.enqueue_reply(thread_id, decision)

    row = (await s.exec(select(Outbox).where(Outbox.thread_id == thread_id))).first()
    assert row is not None and row.llm_info == "templated | free"


async def test_second_reply_to_ad_tap_text_is_not_templated(db_session):
    """The prefill only marks the FIRST message after a tap (signals.py) — if this exact
    text somehow reappears once the bot has already replied once, it's no longer special."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(
        s, branch_id, text="Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?")
    thread = (await s.exec(select(ChannelThread).where(ChannelThread.id == thread_id))).first()
    s.add(Message(branch_id=branch_id, thread_id=thread_id, channel_id=thread.channel_id,
                  external_id="out-1", direction="out", sent_by="agent", text="Halo Kak!"))
    await s.flush()
    llm = FakeLLM(_DECISION)

    await _reply_service(s, branch_id, llm).decide(thread_id)

    assert llm.calls_seen != []  # a real turn — the broker WAS called


async def test_decide_returns_decision_from_fake_llm(db_session):
    from app.modules.conversation.dossier import LeadDossier

    s = db_session
    branch_id = await _branch(s)
    # Discovery already complete so the discovery-extraction backstop pass (a separate
    # chat:fast call, see discovery.py) doesn't fire and overwrite llm.seen with its own
    # smaller prompt — this test is about the main decide() call, not that backstop.
    thread_id = await _thread_with_inbound(
        s, branch_id,
        dossier=LeadDossier(pains=["takut telat"], desired_state=["kerja remote"]).to_json())
    # A prior bot turn — the opener module owns every genuine FIRST turn deterministically,
    # so the full-pipeline plumbing under test needs history (like production turn 2+).
    from app.modules.conversation.opener import AD_TAP_OPENER as _OPENER
    thread = (await s.exec(select(ChannelThread).where(ChannelThread.id == thread_id))).first()
    s.add(Message(branch_id=branch_id, thread_id=thread_id, channel_id=thread.channel_id,
                  external_id="out-0", direction="out", sent_by="agent", text=_OPENER))
    await s.flush()
    llm = FakeLLM(_DECISION)

    decision = await _reply_service(s, branch_id, llm).decide(thread_id)

    assert isinstance(decision, Decision)
    assert decision.reply == _DECISION["reply"]
    # The stage follows the DOSSIER, not the model's word for it: this lead has a pain and a
    # desired state, so the conversation is presenting whatever the reply's JSON claims. The
    # fixture still says "qualifying" on purpose — a stale field must not win (2026-07-26).
    assert decision.stage is Stage.PRESENTING
    assert decision.product_slug == "vibe"
    assert llm.json_required is True  # require_json_schema flowed through
    first_call = llm.calls_seen[0]  # the main decide() call — a critic review may follow it
    assert first_call[0]["role"] == "system"
    # dialog turn included — not necessarily last: a situational/format nudge is appended
    # after it on purpose, so the model reads the instruction closest to its own turn
    assert any(m["content"] == "halo" for m in first_call)


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


async def test_the_broker_line_survives_the_hand_off_to_message(db_session):
    """The chat reads `message.llm_info`, never `outbox.llm_info` — stamping the queue row
    is only half the chain. If _outgoing() drops the field the chip disappears with every
    other assertion still green, so this is the link that needs its own guard."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    s.add(Outbox(branch_id=branch_id, thread_id=thread_id, text="hai kak",
                 llm_info="1.2s | #abc123 | free | 900↑40↓ | gpt-oss-120b"))
    await s.flush()

    await OutboxSender(s, branch_id, FakeChannel(ok=True)).send_next(thread_id)

    out = [m for m in await ReplyService(
        s, branch_id, FakeLLM(_DECISION), KnowledgeService(s, branch_id),
    ).messages.dialog(thread_id) if m.direction == "out"]
    assert len(out) == 1
    assert out[0].llm_info == "1.2s | #abc123 | free | 900↑40↓ | gpt-oss-120b"


async def test_a_managers_manual_message_carries_no_broker_line(db_session):
    """A human wrote it — a blank chip is the correct, permanent answer here (it is why ~12%
    of outgoing prod rows have no llm_info and always will). Guarded so a future 'fill in the
    blanks' fix can't start attributing manager text to a model."""
    s = db_session
    branch_id = await _branch(s)
    thread_id = await _thread_with_inbound(s, branch_id)
    s.add(Outbox(branch_id=branch_id, thread_id=thread_id, text="halo, saya Rina",
                 source="manager", sent_by_name="Rina"))
    await s.flush()

    await OutboxSender(s, branch_id, FakeChannel(ok=True)).send_next(thread_id)

    out = [m for m in await ReplyService(
        s, branch_id, FakeLLM(_DECISION), KnowledgeService(s, branch_id),
    ).messages.dialog(thread_id) if m.direction == "out"]
    assert out[0].sent_by == "manager" and out[0].sent_by_name == "Rina"
    assert out[0].llm_info is None


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
