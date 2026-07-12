"""Fixes for the live bot-quality failures found in the 2026-07-12 long-thread review:
- the canned "be more specific" clarify repeated verbatim in a loop (thread 2262/2789);
- offering a class date that's already in the past (no today-awareness) (thread 2262);
- a stray markdown `---` shipped into a DM bubble (thread 2778).
"""
from __future__ import annotations

import json
from datetime import datetime

from app.adapters.db.models import AppSetting, Branch
from app.modules.conversation import guard
from app.modules.conversation.prompt import build_messages, now_hint
from app.modules.conversation.reply import _clean_bubble, _split_bubbles
from app.modules.conversation.sim import SimService
from app.modules.settings.service import invalidate

# ─── fix 1: today-awareness (no past dates) ──────────────────────────────────────

def test_now_hint_states_the_date_and_forbids_past_sessions() -> None:
    hint = now_hint(datetime(2026, 7, 12, 10, 38))
    assert "12 July 2026" in hint and "10:38" in hint
    assert "ALREADY passed" in hint          # the model is told not to offer a past slot
    assert "past" in hint.lower()


def test_build_messages_injects_the_now_block() -> None:
    msgs = build_messages("PERSONA", [], "id", now_block="CURRENT DATE & TIME (branch-local): X")
    assert "CURRENT DATE & TIME" in msgs[0]["content"]   # rides in the system prompt


# ─── fix 3: strip stray markdown artifacts ───────────────────────────────────────

def test_clean_bubble_strips_horizontal_rules_and_headings() -> None:
    assert _clean_bubble("Halo Kak 😊\n---") == "Halo Kak 😊"        # trailing rule gone
    assert _clean_bubble("---\n\nInfo penting") == "Info penting"
    assert _clean_bubble("***\nisi\n___") == "isi"                  # both rule styles
    # real content is NOT touched: a dash inside a line, or a heading that carries text
    assert _clean_bubble("Harga Rp 500.000 - 600.000") == "Harga Rp 500.000 - 600.000"
    assert _clean_bubble("### Judul penting") == "### Judul penting"


def test_split_bubbles_drops_artifact_only_bubbles() -> None:
    assert _split_bubbles("Halo Kak|||---") == ["Halo Kak"]         # the '---' bubble vanishes
    assert _split_bubbles("Satu 😊\n---|||Dua") == ["Satu 😊", "Dua"]


# ─── fix 2: the clarify loop breaks into a hand-off (integration via SimService) ──

class _EchoLLM:
    """Always returns the SAME reply, forcing the near-duplicate dead-end the clarify path
    exists for — so we can prove a SECOND consecutive dead-end no longer repeats the canned
    clarify verbatim but escalates instead."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        payload = {"reply": self._reply, "stage": "qualifying",
                   "jobs": [], "pains": [], "gains": []}
        return json.dumps(payload), {"model": "deepseek/deepseek-chat", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="urls"))  # deterministic path
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_clarify_loop_escalates_instead_of_repeating_verbatim(db_session) -> None:
    bid = await _branch(db_session)
    sim = SimService(db_session, _EchoLLM("Boleh tahu tujuan utama Kakak belajar ini?"))
    # turn 1: first answer, sent as-is (nothing to duplicate yet)
    await sim.say(bid, "loop", "halo")
    # turn 2: the model can only echo → dead-end → the canned clarify is sent ONCE
    t2 = await sim.say(bid, "loop", "terus gimana")
    assert t2["reply"] == guard.CLARIFY_FALLBACK
    # turn 3: STILL a dead-end AND we already clarified last turn → must NOT repeat the
    # identical clarify; it breaks the loop by escalating (no phone → ask for contact first)
    t3 = await sim.say(bid, "loop", "jelasin dong")
    assert t3["reply"] != guard.CLARIFY_FALLBACK           # loop broken, not repeated
    assert t3["reply"] == guard.ASK_PHONE_BEFORE_HANDOFF   # escalation path (contact-less lead)
