"""Needs discovery (Value Proposition Canvas + SPIN): the profile is parsed/merged/rendered,
the decision carries jobs/pains/gains, presentation is gated until a need is captured, and
the captured profile is persisted on the lead and fed back."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message, StageEvent
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import Decision, parse_decision
from app.modules.conversation.needs import (
    NeedsProfile,
    merge_needs,
    needs_summary,
    parse_needs,
)
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import _parse

_NOW = datetime.now(UTC).replace(tzinfo=None)


# ─── profile: parse / merge / render ──────────────────────────────────────────

def test_parse_and_merge_union() -> None:
    stored = parse_needs('{"jobs":["switch career"],"pains":["too pricey"],"gains":[],'
                         '"discovery_complete":false}')
    assert stored.jobs == ["switch career"] and stored.pains == ["too pricey"]
    merged = merge_needs(stored, jobs=["switch career"], pains=["no time"],
                         gains=["get a dev job"], discovery_complete=True)
    assert merged.jobs == ["switch career"]              # deduped
    assert merged.pains == ["too pricey", "no time"]      # unioned
    assert merged.gains == ["get a dev job"]
    assert merged.discovery_complete is True


def test_needs_summary_and_roundtrip() -> None:
    p = NeedsProfile(jobs=["become a developer"], pains=["failed self-study"], gains=[])
    s = needs_summary(p)
    assert "KNOWN LEAD NEEDS" in s and "become a developer" in s and "failed self-study" in s
    assert needs_summary(NeedsProfile()) == ""           # nothing captured → no block
    assert parse_needs(p.to_json()).pains == ["failed self-study"]  # json roundtrip


def test_parse_decision_reads_profile() -> None:
    d = parse_decision(json.dumps({
        "reply": "ok", "stage": "qualifying",
        "jobs": ["career change"], "pains": ["scared to waste money"],
        "gains": ["stable income"], "discovery_complete": True}))
    assert d.jobs == ["career change"] and d.pains == ["scared to waste money"]
    assert d.gains == ["stable income"] and d.discovery_complete is True
    assert d.has_needs() is True
    assert parse_decision(json.dumps({"reply": "hi"})).has_needs() is False  # nothing → False


# ─── the discovery gate ───────────────────────────────────────────────────────

def _svc(s, bid: int) -> ReplyService:
    return ReplyService(s, bid, _FakeLLM(), KnowledgeService(s, bid),
                        branch_settings=_parse({}), notifier=None)


class _FakeLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return "{}", {"cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_gate_blocks_presenting_without_needs(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)  # no needs captured yet
    db_session.add(lead)
    await db_session.flush()
    svc = _svc(db_session, b.id)

    bare = Decision(reply="here's the price...", stage=Stage.PRESENTING, product_slug="vibe",
                    ready=False, needs_manager=False)
    assert svc._stage_for(bare, lead) == Stage.QUALIFYING  # forced back to discovery

    pain_only = Decision(reply="...", stage=Stage.PRESENTING, product_slug="vibe",
                         ready=False, needs_manager=False, pains=["scared to fail"])
    assert svc._stage_for(pain_only, lead) == Stage.QUALIFYING  # a lone pain is still shallow

    with_need = Decision(reply="...", stage=Stage.PRESENTING, product_slug="vibe",
                         ready=False, needs_manager=False,
                         pains=["scared to fail"], gains=["a stable job"])
    assert svc._stage_for(with_need, lead) == Stage.PRESENTING  # pain + gain → allowed


async def test_gate_allows_presenting_when_lead_already_has_needs(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING,
                needs='{"jobs":["job"],"pains":["cost"],"gains":[],"discovery_complete":true}')
    db_session.add(lead)
    await db_session.flush()
    svc = _svc(db_session, b.id)
    bare = Decision(reply="...", stage=Stage.PRESENTING, product_slug=None,
                    ready=False, needs_manager=False)
    assert svc._stage_for(bare, lead) == Stage.PRESENTING  # stored need satisfies the gate


# ─── persistence through decide() ─────────────────────────────────────────────

class _ProfileLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return (json.dumps({"reply": "what's the hardest part for you?", "stage": "qualifying",
                            "pains": ["tried before, gave up"], "gains": ["a real job"]}),
                {"cost_usd": 0.0})

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_decide_persists_captured_needs(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(th)
    await db_session.flush()
    db_session.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id="m1",
                           direction="in", sent_by="lead", text="how much is vibe coding?",
                           occurred_at=_NOW))
    await db_session.flush()

    svc = ReplyService(db_session, b.id, _ProfileLLM(),
                       KnowledgeService(db_session, b.id, _ProfileLLM()),
                       branch_settings=_parse({}), notifier=None)
    await svc.decide(th.id)
    got = parse_needs((await db_session.exec(select(Lead).where(Lead.id == lead.id))).first().needs)
    assert "tried before, gave up" in got.pains and "a real job" in got.gains


# ─── discovery metric ─────────────────────────────────────────────────────────

async def test_discovery_metrics(db_session) -> None:
    from datetime import timedelta

    from app.api._query import fetch_discovery_metrics
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    good = Lead(branch_id=b.id)   # discovered before presenting
    bad = Lead(branch_id=b.id)    # jumped straight to presenting
    db_session.add_all([ch, good, bad])
    await db_session.flush()
    tg = ChannelThread(lead_id=good.id, channel_id=ch.id, external_thread_id="g")
    db_session.add(tg)
    await db_session.flush()
    t0 = _NOW - timedelta(minutes=10)
    # good lead: qualifying then presenting, with 2 inbound messages before presenting
    db_session.add(StageEvent(branch_id=b.id, lead_id=good.id, from_stage="new",
                              to_stage="qualifying", created_at=t0))
    db_session.add(StageEvent(branch_id=b.id, lead_id=good.id, from_stage="qualifying",
                              to_stage="presenting", created_at=t0 + timedelta(minutes=5)))
    for i in range(2):
        db_session.add(Message(branch_id=b.id, thread_id=tg.id, channel_id=ch.id,
                               external_id=f"g{i}", direction="in", sent_by="lead",
                               text="q", occurred_at=t0 + timedelta(minutes=1 + i)))
    # bad lead: straight to presenting, no qualifying
    db_session.add(StageEvent(branch_id=b.id, lead_id=bad.id, from_stage="new",
                              to_stage="presenting", created_at=t0))
    await db_session.flush()

    m = await fetch_discovery_metrics(db_session, [b.id])
    assert m["reached"] == 2 and m["discovered"] == 1
    assert m["pct"] == 50 and m["avg_msgs"] == 2.0  # only the good lead had inbound msgs
