"""Critic-gate — the positive quality check on every reply (conversation.critic + apply_critic).

Isolated from the fabrication guard by seeding reply_guard='urls' (deterministic only), so the
ONLY LLM judgment in the path is the critic. The fake LLM splits calls by the workflow kwarg:
workflow='critic' is the critic verdict (scripted, or raise for the error test); anything else
is a reply-generation call. Pins: pass ships as-is; one rejection regens and re-judges; two
rejections FAIL CLOSED to a human; a raising critic ALSO fails closed (opposite of the
grounding verify's fail-open); shadow logs without altering; off skips entirely."""
from __future__ import annotations

import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import AppSetting, Branch, KnowledgeDoc  # noqa: E402
from app.modules.conversation import guard  # noqa: E402
from app.modules.conversation.sim import SimService  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

_DRAFT_A = "Vibe Coding itu 4 bulan ya Kak, DP 500rb buat amankan seat 😊"
_DRAFT_B = "Betul Kak, biar aku bantu cariin skema yang pas. Sekarang lagi kerja atau kuliah?"


def _ok() -> str:
    return json.dumps({"ok": True, "failures": [], "fix": ""})


def _bad(reason: str) -> str:
    return json.dumps({"ok": False, "failures": [f"responsive: {reason}"], "fix": "answer them"})


class _CriticLLM:
    """workflow='critic' → the next scripted critic verdict (or raise); else → generate the
    next draft decision JSON."""

    def __init__(self, drafts: list[str], verdicts: list) -> None:
        self._drafts = list(drafts)
        self._verdicts = list(verdicts)
        self.critic_calls = 0
        self.gen_calls = 0
        self._last = ""

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        if kw.get("workflow") == "critic":
            self.critic_calls += 1
            v = self._verdicts.pop(0) if self._verdicts else _ok()
            if isinstance(v, Exception):
                raise v
            return v, {"model": "critic", "cost_usd": 0.0}
        self.gen_calls += 1
        self._last = self._drafts.pop(0) if self._drafts else self._last
        payload = {"reply": self._last, "stage": "qualifying", "jobs": [], "pains": [],
                   "gains": []}
        return json.dumps(payload), {"model": "gen", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s, critic_gate: str) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Vibe Coding 4 bulan. Pembayaran: DP Rp 500.000 via BCA atau QRIS."))
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="urls"))  # isolate the critic
    s.add(AppSetting(branch_id=b.id, key="critic_gate", value=critic_gate))
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_passing_draft_ships_unchanged(db_session) -> None:
    bid = await _branch(db_session, "on")
    llm = _CriticLLM([_DRAFT_A], verdicts=[_ok()])
    out = await SimService(db_session, llm).say(bid, "c1", "vibe coding berapa lama kak?")
    assert out["reply"] == _DRAFT_A
    assert llm.critic_calls == 1 and llm.gen_calls == 1  # judged once, no regen


async def test_rejected_then_regen_passes(db_session) -> None:
    bid = await _branch(db_session, "on")
    # critic rejects the first draft, the regen (DRAFT_B) passes on re-judge.
    llm = _CriticLLM([_DRAFT_A, _DRAFT_B], verdicts=[_bad("ignored their question"), _ok()])
    out = await SimService(db_session, llm).say(bid, "c2", "vibe coding berapa lama kak?")
    assert out["reply"] == _DRAFT_B
    assert llm.gen_calls == 2 and llm.critic_calls == 2  # draft+regen, judged twice
    assert not out["needs_manager"]


async def test_two_rejections_fail_closed_to_human(db_session) -> None:
    bid = await _branch(db_session, "on")
    llm = _CriticLLM([_DRAFT_A, _DRAFT_A], verdicts=[_bad("off-topic"), _bad("still off-topic")])
    out = await SimService(db_session, llm).say(bid, "c3", "vibe coding berapa lama kak?")
    assert out["reply"] == guard.SAFE_FALLBACK
    assert out["needs_manager"] is True


async def test_erroring_critic_fails_closed(db_session) -> None:
    bid = await _branch(db_session, "on")
    llm = _CriticLLM([_DRAFT_A, _DRAFT_A],
                     verdicts=[RuntimeError("broker 502"), RuntimeError("broker 502")])
    out = await SimService(db_session, llm).say(bid, "c4", "vibe coding berapa lama kak?")
    assert out["reply"] == guard.SAFE_FALLBACK   # opposite of verify_grounding's fail-open
    assert out["needs_manager"] is True


async def test_shadow_logs_but_does_not_alter(db_session) -> None:
    bid = await _branch(db_session, "shadow")
    llm = _CriticLLM([_DRAFT_A], verdicts=[_bad("would normally block")])
    out = await SimService(db_session, llm).say(bid, "c5", "vibe coding berapa lama kak?")
    assert out["reply"] == _DRAFT_A              # shadow never changes the reply
    assert llm.critic_calls == 1 and llm.gen_calls == 1  # judged, but no regen


async def test_suggest_workflow_skips_the_critic() -> None:
    """A manager 'suggest' draft must NOT go through the critic's fail-closed hand-off — the
    manager is the human reviewer. apply_critic returns the draft untouched for workflow=
    'suggest', before it would even touch the engine (so a None engine here proves the skip)."""
    from app.modules.conversation.decision import Decision
    from app.modules.conversation.reply import apply_critic
    from app.domain.enums import Stage

    settings_on = type("S", (), {"critic_gate": "on"})()
    draft = Decision(reply="Vibe Coding 13jt, DP 500rb ya Kak", stage=Stage.PRESENTING,
                     product_slug="vibe_coding", ready=False, needs_manager=False)
    out, meta = await apply_critic(
        settings_on, None, None, 1, lang="id", workflow="suggest", bill=False,
        decision=draft, meta={}, situational=None, last_inbound="berapa harganya?",
        open_objections=[])
    assert out is draft  # unchanged — critic skipped (a None engine would crash if it ran)


async def test_off_skips_the_critic(db_session) -> None:
    bid = await _branch(db_session, "off")
    llm = _CriticLLM([_DRAFT_A], verdicts=[_bad("never consulted")])
    out = await SimService(db_session, llm).say(bid, "c6", "vibe coding berapa lama kak?")
    assert out["reply"] == _DRAFT_A
    assert llm.critic_calls == 0                 # never consulted when off
