"""The phone gate must not swallow a real answer under the WhatsApp stub.

Live losses, 2026-07 audit: thread 4224 — pain+gain captured, "berapa biayanya?" got the
contact-ask stub instead of the drafted price; thread 4199 — the very FIRST bot message to
an ad click was the stub. The gate now keeps a substantive reply (dropping only the
escalation) for an answerable own-words question and for a lead who hasn't typed a word;
canned fallbacks and hand-off promises still funnel into the stub.
"""
from __future__ import annotations

import json

from app.adapters.db.models import AppSetting, Branch, KnowledgeDoc
from app.modules.conversation import guard
from app.modules.conversation.sim import SimService
from app.modules.settings.service import invalidate


class _EscalatingLLM:
    """Always escalates but drafts a real answer — the shape of the live price-stub loss."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        payload = {"reply": self._reply, "stage": "qualifying",
                   "jobs": [], "pains": [], "gains": [],
                   "needs_manager": True, "manager_question": "berapa biayanya",
                   "kb_gap": "лид спросил цену"}
        return json.dumps(payload), {"model": "deepseek/deepseek-chat", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000. SMM Intensive total Rp 1.882.955."))
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="urls"))
    await s.flush()
    invalidate(b.id)
    return b.id


PRICE_ANSWER = ("Investasinya DP Rp 500.000 buat amankan kursi, sisanya bisa dicicil ya Kak 😊 "
                "Totalnya Rp 1.882.955. Kira-kira mau mulai kapan?")


async def test_price_question_keeps_the_answer_not_the_stub(db_session) -> None:
    bid = await _branch(db_session)
    sim = SimService(db_session, _EscalatingLLM(PRICE_ANSWER))
    await sim.say(bid, "price", "aku punya olshop, followers stuck")
    t = await sim.say(bid, "price", "berapa biayanya?")
    assert t["reply"] != guard.ASK_PHONE_BEFORE_HANDOFF
    assert "500.000" in t["reply"]
    assert not t["needs_manager"]          # no phone → escalation deferred, bot stays on


async def test_silent_clicker_never_gets_the_stub_first(db_session) -> None:
    bid = await _branch(db_session)
    greeting = "Halo Kak! Aku MinStep 😊 Boleh tahu tujuan Kakak ambil kursus ini?"
    sim = SimService(db_session, _EscalatingLLM(greeting))
    t = await sim.say(
        bid, "clicker", "Halo, saya ingin tahu detail program SMM dan biaya kursusnya")
    assert t["reply"] != guard.ASK_PHONE_BEFORE_HANDOFF
    assert t["reply"] == greeting


async def test_handoff_promise_still_funnels_into_the_stub(db_session) -> None:
    bid = await _branch(db_session)
    promise = "Datanya sudah aku teruskan, tim kami akan menghubungi Kakak ya 🙏"
    sim = SimService(db_session, _EscalatingLLM(promise))
    await sim.say(bid, "promise", "halo, mau tanya")
    t = await sim.say(bid, "promise", "berapa biayanya?")
    # a promised call needs a number to call — the contact-ask is correct here
    assert t["reply"] == guard.ASK_PHONE_BEFORE_HANDOFF
