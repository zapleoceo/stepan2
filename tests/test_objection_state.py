"""Objection-state — the memory that stops Stepan pitching over a live objection.

Unlike jobs/pains/gains (unioned and kept), open objections REPLACE each turn: the model
re-reports the still-unresolved set, so a handled objection drops out. Pins: parse/merge
replace-semantics, the summary block renders, the Decision carries the field, and the whole
thing round-trips — an objection stored on turn 1 is injected into turn 2's prompt."""
from __future__ import annotations

import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import AppSetting, Branch, KnowledgeDoc  # noqa: E402
from app.modules.conversation.decision import parse_decision  # noqa: E402
from app.modules.conversation.needs import (  # noqa: E402
    NeedsProfile,
    merge_needs,
    needs_summary,
    parse_needs,
)
from app.modules.conversation.sim import SimService  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402


def test_objections_parse_and_roundtrip() -> None:
    p = parse_needs('{"jobs":[],"pains":[],"gains":[],"objections":["mahal","ga ada waktu"]}')
    assert p.objections == ["mahal", "ga ada waktu"]
    assert parse_needs(p.to_json()).objections == ["mahal", "ga ada waktu"]


def test_objections_replace_not_union() -> None:
    stored = NeedsProfile(objections=["mahal banget"])
    # a NEW open set replaces the old one (the budget objection got handled, only time is left)
    merged = merge_needs(stored, jobs=[], pains=[], gains=[], discovery_complete=False,
                         objections=["ga ada waktu"])
    assert merged.objections == ["ga ada waktu"]


def test_objections_none_keeps_stored() -> None:
    stored = NeedsProfile(objections=["ragu legit"])
    merged = merge_needs(stored, jobs=[], pains=[], gains=[], discovery_complete=False)
    assert merged.objections == ["ragu legit"]  # None → untouched


def test_summary_renders_open_objections() -> None:
    block = needs_summary(NeedsProfile(objections=["takut ga dapat kerja"]))
    assert "OPEN OBJECTIONS" in block and "takut ga dapat kerja" in block


def test_decision_parses_open_objections() -> None:
    d = parse_decision(json.dumps({"reply": "ok kak", "stage": "objection",
                                   "open_objections": ["mahal", "kejauhan"]}))
    assert d.open_objections == ["mahal", "kejauhan"]


class _RecordingLLM:
    """Returns scripted decisions and records the system prompt of each generation call so a
    test can assert what the second turn was told."""

    def __init__(self, decisions: list[dict]) -> None:
        self._decisions = list(decisions)
        self.systems: list[str] = []

    async def chat(self, messages, **_kw):  # noqa: ANN001, ANN003, ANN201
        self.systems.append(messages[0]["content"])
        d = self._decisions.pop(0) if self._decisions else {"reply": "ok", "stage": "qualifying"}
        return json.dumps(d), {"model": "gen", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy", content="Vibe Coding 4 bulan."))
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="urls"))
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_ungrounded_objection_is_dropped(db_session) -> None:
    bid = await _branch(db_session)
    # the lead said nothing about price, but the model invents an objection — grounding drops it
    llm = _RecordingLLM([
        {"reply": "Halo Kak!", "stage": "qualifying", "open_objections": ["mahal"]},
        {"reply": "Siap.", "stage": "qualifying"},
    ])
    sim = SimService(db_session, llm)
    await sim.say(bid, "obj2", "halo kak mau tanya")
    await sim.say(bid, "obj2", "ok")
    # the invented "mahal" was not grounded in the lead's words → never reaches the next prompt
    assert "OPEN OBJECTIONS" not in llm.systems[-1]


async def test_objection_roundtrips_into_next_prompt(db_session) -> None:
    bid = await _branch(db_session)
    # Turn 1: the lead voices a budget objection; the model records it as open.
    llm = _RecordingLLM([
        {"reply": "Aku ngerti Kak soal biayanya.", "stage": "objection",
         "open_objections": ["mahal"]},
        {"reply": "Siap Kak.", "stage": "objection", "open_objections": ["mahal"]},
    ])
    sim = SimService(db_session, llm)
    await sim.say(bid, "obj1", "waduh mahal banget kak")
    # Turn 2: the stored objection must be injected into the new turn's system prompt.
    await sim.say(bid, "obj1", "hmm")
    assert "OPEN OBJECTIONS" in llm.systems[-1]
    assert "mahal" in llm.systems[-1]
