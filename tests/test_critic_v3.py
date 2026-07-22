"""The v3 critic — judges whether the reply sells, and fails OPEN.

v2's critic returned ok=False on any broker hiccup or malformed JSON, and a second failure
switched the lead's bot off permanently: broker instability converted directly into lost
conversations. Every failure path here ships the draft instead.
"""
from __future__ import annotations

import json

from app.modules.conversation.critic_v3 import CRITIC_CORRECTION, Verdict, review


class _LLM:
    def __init__(self, answer: str | None = None, raises: Exception | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        self.messages = messages
        if self._raises is not None:
            raise self._raises
        return self._answer, {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _review(llm: _LLM) -> Verdict:
    return await review(llm, reply="halo kak", context="KB", last_inbound="berapa harganya",
                        lang="id", branch_id=1, thread_id=1)


async def test_a_passing_verdict_is_reported_as_such() -> None:
    assert await _review(_LLM(json.dumps({"sells": True}))) == Verdict(sells=True)


async def test_a_rejection_carries_the_reason_and_the_fix() -> None:
    verdict = await _review(_LLM(json.dumps(
        {"sells": False, "why": "tidak menjawab pertanyaan harga", "fix": "sebutkan angkanya"})))
    assert verdict.sells is False
    assert verdict.why == "tidak menjawab pertanyaan harga"
    assert verdict.fix == "sebutkan angkanya"


async def test_a_broker_failure_ships_the_draft() -> None:
    """The v2 inversion: an unreachable reviewer used to cost the lead their answer."""
    verdict = await _review(_LLM(raises=TimeoutError("chat:smart still pending after budget")))
    assert verdict.sells is True
    assert verdict.errored is True


async def test_an_unparseable_verdict_ships_the_draft() -> None:
    verdict = await _review(_LLM("the draft looks fine to me"))
    assert verdict.sells is True and verdict.errored is True


async def test_a_verdict_missing_its_field_ships_the_draft() -> None:
    verdict = await _review(_LLM(json.dumps({"why": "meh"})))
    assert verdict.sells is True and verdict.errored is True


async def test_fenced_json_is_tolerated() -> None:
    verdict = await _review(_LLM('```json\n{"sells": false, "why": "generic"}\n```'))
    assert verdict.sells is False and verdict.why == "generic"


async def test_the_draft_and_the_lead_message_both_reach_the_reviewer() -> None:
    llm = _LLM(json.dumps({"sells": True}))
    await _review(llm)
    user = llm.messages[1]["content"]
    assert "halo kak" in user and "berapa harganya" in user


def test_the_rubric_puts_answering_the_question_first() -> None:
    from app.modules.conversation.critic_v3 import _SYSTEM
    assert "FIRST line actually answer it" in _SYSTEM
    assert "more than the other two combined" in _SYSTEM


def test_the_rubric_biases_towards_passing() -> None:
    """A plain honest reply reaching the lead beats a perfect one that never arrives."""
    from app.modules.conversation.critic_v3 import _SYSTEM
    assert "Be reluctant to fail" in _SYSTEM


def test_the_correction_forbids_a_handoff_or_a_generic_retreat() -> None:
    text = CRITIC_CORRECTION.format(why="w", fix="f")
    assert "Do not hand off" in text and "generic line" in text
