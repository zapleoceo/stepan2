"""Persona auto-dialogue: LLM lead-actor talks to the real reply engine, ends naturally."""
from __future__ import annotations

import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import Branch, KnowledgeDoc  # noqa: E402
from app.modules.conversation.sim_persona import PERSONAS, run_persona  # noqa: E402


class _DualLLM:
    """Plays both sides: JSON decision for Stepan (require_json_schema), a lead line for
    the actor. The actor ends the chat on its 2nd turn."""

    def __init__(self) -> None:
        self.actor_calls = 0

    async def chat(self, messages, *, require_json_schema=False, **kw):  # noqa: ANN001, ANN003, ANN201
        if require_json_schema:  # Stepan's decision
            # move=answer_question because the lead TYPED a price question ("berapa harga") —
            # the pitch gate (premature_pitch) never applies to a directly-asked price, only
            # to a volunteered one, so quoting it here is correct on turn one with an empty
            # dossier.
            payload = {"reply": "Halo Kak! Vibe Coding 13 juta ya.", "move": "answer_question",
                       "stage": "qualifying"}
            return json.dumps(payload), {"model": "x", "cost_usd": 0.0}
        self.actor_calls += 1  # lead actor
        # A real, typed price question — so answer-first (not the pitch gate) governs this
        # turn, and quoting a price on an empty dossier is legitimately the right move.
        return ("halo kak, berapa ya harga vibe coding?" if self.actor_calls == 1 else "[END]",
                {"model": "x", "cost_usd": 0.0})

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_persona_runs_a_dialogue_and_ends(db_session) -> None:
    b = Branch(name="SIM", lang="id")
    db_session.add(b)
    await db_session.flush()
    db_session.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000. Vibe Coding 13 juta."))

    out = await run_persona(db_session, b.id, "hot_ready", "p1", _DualLLM(), max_turns=4)
    assert out["ok"] and out["ended"] and out["reason"] == "lead_ended"
    assert out["turns_total"] == 1                       # one real exchange before [END]
    assert [m["who"] for m in out["transcript"]] == ["lead", "stepan"]
    assert "13 juta" in out["transcript"][1]["text"]     # Stepan's reply captured


async def test_unknown_persona_is_rejected(db_session) -> None:
    b = Branch(name="SIM", lang="id")
    db_session.add(b)
    await db_session.flush()
    out = await run_persona(db_session, b.id, "nope", "p2", _DualLLM())
    assert out["ok"] is False


def test_personas_defined() -> None:
    assert len(PERSONAS) >= 10  # segmentation/funnel matrix + hard-lead set
    assert "hard_skeptic" in PERSONAS  # difficult, winnable leads for the close test
