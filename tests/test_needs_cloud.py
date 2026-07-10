"""Needs-cloud: incremental classification onto a stable taxonomy, cheap range aggregation
with visual weights, daily history snapshot."""
from __future__ import annotations

import json

from sqlmodel import select

from app.adapters.db.models import Branch, Lead, NeedAggSnapshot, NeedEntity
from app.modules.needs_cloud import classify_branch, cloud_for, write_snapshot


class _FakeLLM:
    """Maps each input phrase to a canonical label via `rules` (default: identity). Parses the
    real user payload so we exercise the actual prompt shape; counts calls for the incremental
    test."""

    def __init__(self, rules: dict[str, str]) -> None:
        self.rules = rules
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        payload = json.loads(messages[-1]["content"])
        out = {p: self.rules.get(p, p) for p in payload["phrases"]}
        return json.dumps(out, ensure_ascii=False), {"cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


def _needs(pains=(), jobs=(), gains=()) -> str:  # noqa: ANN001
    return json.dumps({"pains": list(pains), "jobs": list(jobs), "gains": list(gains)})


async def _branch(s) -> int:  # noqa: ANN001
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    return b.id


_RULES = {"mahal": "Цена", "terlalu mahal": "Цена", "gak ada budget": "Цена",
          "gak ada waktu": "Время", "pengen jago coding": "Освоить кодинг"}


async def test_classify_groups_synonyms_and_counts_with_weights(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal", "gak ada waktu"],
                                                    gains=["pengen jago coding"])))
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["terlalu mahal"])))
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["gak ada budget"])))
    await db_session.flush()

    n = await classify_branch(db_session, bid, _FakeLLM(_RULES))
    assert n == 3

    pains = await cloud_for(db_session, [bid], "pains", None, None)
    assert [(e.label, e.count) for e in pains] == [("Цена", 3), ("Время", 1)]
    assert pains[0].weight == 1.0 and pains[1].weight == 1 / 3  # bar scales to the top entity

    gains = await cloud_for(db_session, [bid], "gains", None, None)
    assert [(e.label, e.count) for e in gains] == [("Освоить кодинг", 1)]


async def test_taxonomy_is_reused_not_duplicated(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal"])))
    await db_session.flush()
    await classify_branch(db_session, bid, _FakeLLM(_RULES))
    # a second lead voicing a synonym maps onto the SAME entity, not a new row
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["terlalu mahal"])))
    await db_session.flush()
    await classify_branch(db_session, bid, _FakeLLM(_RULES))
    entities = (await db_session.execute(
        select(NeedEntity).where(NeedEntity.branch_id == bid, NeedEntity.kind == "pains"))
    ).scalars().all()
    assert [e.label for e in entities] == ["Цена"]  # one canonical entity, reused


async def test_unchanged_leads_are_not_reclassified(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal"])))
    await db_session.flush()
    llm = _FakeLLM(_RULES)
    assert await classify_branch(db_session, bid, llm) == 1
    calls_after_first = llm.calls
    # nothing changed → no leads processed and no new LLM calls
    assert await classify_branch(db_session, bid, llm) == 0
    assert llm.calls == calls_after_first


async def test_snapshot_freezes_current_counts(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal"])))
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["terlalu mahal"])))
    await db_session.flush()
    await classify_branch(db_session, bid, _FakeLLM(_RULES))
    written = await write_snapshot(db_session, bid)
    assert written == 1  # one entity (Цена)
    snap = (await db_session.execute(
        select(NeedAggSnapshot).where(NeedAggSnapshot.branch_id == bid))).scalars().all()
    assert len(snap) == 1 and snap[0].lead_count == 2
