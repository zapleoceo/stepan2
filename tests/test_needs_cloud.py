"""Needs-cloud: incremental classification onto a stable taxonomy, cheap range aggregation
with visual weights, daily history snapshot."""
from __future__ import annotations

import json

from sqlmodel import select

from app.adapters.db.models import Branch, Lead, NeedAggSnapshot, NeedEntity
from app.modules.needs_cloud import (
    classify_branch,
    cloud_for,
    translate_labels,
    write_snapshot,
)


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
        # phrases is index→phrase; return index→label (mirrors the real prompt contract)
        out = {idx: self.rules.get(p, p) for idx, p in payload["phrases"].items()}
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


async def test_garbage_script_label_is_rejected(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal", "aneh"])))
    await db_session.flush()
    # the model drifts to Arabic for one phrase — that label must be dropped, not made a category
    rules = {"mahal": "Цена", "aneh": "برمجة"}
    await classify_branch(db_session, bid, _FakeLLM(rules))
    labels = [e.label for e in (await db_session.execute(
        select(NeedEntity).where(NeedEntity.branch_id == bid))).scalars()]
    assert labels == ["Цена"]  # only the clean Russian label survived


class _I18nLLM:
    """Returns index→{en,id} for label translation (or an Arabic drift to test the guard)."""

    def __init__(self, mapping: dict[str, dict], drift: bool = False) -> None:
        self._mapping = mapping
        self._drift = drift

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        payload = json.loads(messages[-1]["content"])
        out = {}
        for idx, ru in payload["labels"].items():
            if self._drift:
                out[idx] = {"en": "تعلم", "id": "برمجة"}  # Arabic → must be rejected
            else:
                out[idx] = self._mapping.get(ru, {"en": ru, "id": ru})
        return json.dumps(out, ensure_ascii=False), {"cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_translate_labels_caches_en_id_and_localizes(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal"])))
    await db_session.flush()
    await classify_branch(db_session, bid, _FakeLLM(_RULES))  # → entity "Цена"
    n = await translate_labels(db_session, bid,
                               _I18nLLM({"Цена": {"en": "Price", "id": "Harga"}}))
    await db_session.flush()  # persist label_i18n before the raw-SQL cloud_for reads it
    assert n == 1
    ru = await cloud_for(db_session, [bid], "pains", None, None, lang="ru")
    en = await cloud_for(db_session, [bid], "pains", None, None, lang="en")
    idn = await cloud_for(db_session, [bid], "pains", None, None, lang="id")
    assert ru[0].label == "Цена"      # canonical Russian untouched
    assert en[0].label == "Price"     # localized from the cache, no LLM at render
    assert idn[0].label == "Harga"


async def test_translate_labels_rejects_wrong_script_drift(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, needs=_needs(pains=["mahal"])))
    await db_session.flush()
    await classify_branch(db_session, bid, _FakeLLM(_RULES))
    assert await translate_labels(db_session, bid, _I18nLLM({}, drift=True)) == 0
    ent = (await db_session.execute(
        select(NeedEntity).where(NeedEntity.branch_id == bid))).scalars().first()
    assert ent.label_i18n is None  # Arabic drift not cached — retried next run
    # render falls back to the canonical Russian for a non-ru viewer
    en = await cloud_for(db_session, [bid], "pains", None, None, lang="en")
    assert en[0].label == "Цена"


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


async def test_the_cloud_classifies_the_column_the_bot_actually_writes(db_session) -> None:
    """Every other test in this file seeds the v2 `needs` column, which is why they all passed
    while the live panel showed three empty columns for weeks: the extraction has written
    `dossier` since the v3 cutover, classify_branch read `needs`, so no lead's hash ever
    changed and the nightly pass returned 0 leads processed, forever."""
    bid = await _branch(db_session)
    db_session.add(Lead(branch_id=bid, dossier=json.dumps({
        "job_to_be_done": "pengen jago coding", "pains": ["mahal"],
        "desired_state": [], "objections": []})))
    await db_session.flush()

    llm = _FakeLLM(_RULES)
    assert await classify_branch(db_session, bid, llm) == 1
    await db_session.commit()

    pains = await cloud_for(db_session, [bid], "pains", None, None)
    jobs = await cloud_for(db_session, [bid], "jobs", None, None)
    assert [e.label for e in pains] == ["Цена"]
    assert [e.label for e in jobs] == ["Освоить кодинг"]


async def test_a_dossier_edit_reclassifies_that_lead(db_session) -> None:
    """The hash must track the column being read — otherwise a lead classified once is frozen
    and new pains never reach the cloud."""
    bid = await _branch(db_session)
    lead = Lead(branch_id=bid, dossier=json.dumps({"pains": ["mahal"]}))
    db_session.add(lead)
    await db_session.flush()

    llm = _FakeLLM(_RULES)
    await classify_branch(db_session, bid, llm)
    await db_session.commit()

    lead.dossier = json.dumps({"pains": ["mahal", "gak ada waktu"]})
    db_session.add(lead)
    await db_session.flush()
    assert await classify_branch(db_session, bid, llm) == 1
    await db_session.commit()

    assert {e.label for e in await cloud_for(db_session, [bid], "pains", None, None)} == {
        "Цена", "Время"}
