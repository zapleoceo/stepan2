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
from app.modules.conversation.reply import _DISCOVERY_TURN_CAP
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


def test_near_duplicate_phrasings_collapse() -> None:
    # thread 1081: the same job/gain reworded each turn must NOT pile up
    stored = parse_needs(json.dumps({
        "jobs": ["pengen buat aplikasi", "buat aplikasi bantu UMKM", "membuat aplikasi"],
        "gains": ["aplikasi dibantu AI", "bisa bikin aplikasi sendiri dengan AI",
                  "bisa bikin aplikasi UMKM dengan AI"],
        "pains": [], "discovery_complete": True}))
    assert stored.jobs == ["buat aplikasi bantu UMKM"]          # collapsed to the most specific
    assert stored.gains == ["bisa bikin aplikasi UMKM dengan AI"]
    merged = merge_needs(stored, jobs=["membuat aplikasi bantu UMKM"], pains=[],
                         gains=["bisa bikin aplikasi UMKM pakai AI"], discovery_complete=True)
    assert len(merged.jobs) == 1 and len(merged.gains) == 1     # reworded restatement adds nothing


def test_distinct_needs_not_collapsed() -> None:
    stored = parse_needs(json.dumps({
        "jobs": ["dapat kerja jadi developer", "bikin startup sendiri"],
        "pains": ["takut buang waktu", "ga punya laptop"],
        "gains": ["gaji lebih tinggi"], "discovery_complete": False}))
    assert len(stored.jobs) == 2 and len(stored.pains) == 2     # genuinely different → kept


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


async def test_gate_stops_forcing_discovery_after_turn_cap(db_session) -> None:
    """A non-yielding lead must not be interrogated forever: once they've taken enough turns
    the gate stops blocking, so Stepan presents on what he has instead of a third question."""
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)  # still no captured need
    db_session.add(lead)
    await db_session.flush()
    svc = _svc(db_session, b.id)
    bare = Decision(reply="here's the fit...", stage=Stage.PRESENTING, product_slug="vibe",
                    ready=False, needs_manager=False)
    assert svc._stage_for(bare, lead, inbound_count=_DISCOVERY_TURN_CAP - 1) == Stage.QUALIFYING
    assert svc._stage_for(bare, lead, inbound_count=_DISCOVERY_TURN_CAP) == Stage.PRESENTING


async def test_gate_blocks_premature_discovery_complete_without_pain(db_session) -> None:
    """The model sets discovery_complete=true with pains=[] (thread 1081), which used to skip
    warm-up. A pain-less 'complete' must NOT satisfy the gate — keep discovering."""
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    db_session.add(lead)
    await db_session.flush()
    svc = _svc(db_session, b.id)
    flag_no_pain = Decision(reply="here's the program...", stage=Stage.PRESENTING,
                            product_slug="vibe", ready=False, needs_manager=False,
                            gains=["build an app"], discovery_complete=True)  # pains=[]
    assert svc._stage_for(flag_no_pain, lead) == Stage.QUALIFYING  # no pain → keep warming up
    flag_with_pain = Decision(reply="...", stage=Stage.PRESENTING, product_slug="vibe",
                              ready=False, needs_manager=False, pains=["takut gagal"],
                              discovery_complete=True)
    assert svc._stage_for(flag_with_pain, lead) == Stage.PRESENTING  # pain-backed → allowed


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


async def test_ad_template_click_records_no_needs(db_session) -> None:
    """Thread 2912: the lead's ONLY message was the ad's prefilled opener ('👨‍💻 Ceritakan
    lebih detail tentang program kursus Cybersecurity') — a button click, not their words —
    yet the model invented a job+gain from the course name and the needs cloud showed them.
    Until the lead types something of their own, model-claimed needs are NOT persisted."""
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
    db_session.add(Message(
        branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id="m1",
        direction="in", sent_by="lead",
        text="👨‍💻 Ceritakan lebih detail tentang program kursus Cybersecurity.",
        occurred_at=_NOW))
    await db_session.flush()

    svc = ReplyService(db_session, b.id, _ProfileLLM(),
                       KnowledgeService(db_session, b.id, _ProfileLLM()),
                       branch_settings=_parse({}), notifier=None)
    await svc.decide(th.id)
    got = parse_needs((await db_session.exec(
        select(Lead).where(Lead.id == lead.id))).first().needs)
    assert not got.jobs and not got.pains and not got.gains  # nothing invented from a click


def test_lead_spoke_own_words_helper() -> None:
    from types import SimpleNamespace

    from app.modules.conversation.reply import _lead_spoke_own_words

    def _in(text: str):  # noqa: ANN202
        return SimpleNamespace(direction="in", text=text)

    template = "💻 Ceritakan lebih detail tentang program kursusnya"
    # the second (largest) prefill family — was slipping through as the lead's own words (2983/3005)
    template2 = "Halo, saya ingin tahu detail program SMM dan biaya kursusnya 😊"
    assert _lead_spoke_own_words([_in(template)]) is False
    assert _lead_spoke_own_words([_in(template2)]) is False
    assert _lead_spoke_own_words([_in("🎤 voice"), _in(template)]) is False
    assert _lead_spoke_own_words([_in(template), _in("berapa biayanya kak?")]) is True
    assert _lead_spoke_own_words([_in(template2), _in("buat karier freelance ka")]) is True
    assert _lead_spoke_own_words([_in("🎤 berapa harga kursusnya")]) is True  # transcribed voice


async def test_decide_holds_reply_on_untranscribed_voice(db_session) -> None:
    """A voice note the broker hasn't transcribed yet (text is the raw '🎤 voice'
    placeholder) must NOT get a reply — decide() returns None so Stepan waits for the
    transcript instead of answering the placeholder (the chat-1756 bug)."""
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
    db_session.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id="v1",
                           direction="in", sent_by="lead", text="🎤 voice", occurred_at=_NOW))
    await db_session.flush()
    svc = ReplyService(db_session, b.id, _ProfileLLM(),
                       KnowledgeService(db_session, b.id, _ProfileLLM()),
                       branch_settings=_parse({}), notifier=None)
    assert await svc.decide(th.id) is None  # held — waiting for the transcript


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


async def test_discovery_metrics_scoped_by_since_until(db_session) -> None:
    """The reports date-range/quick-range filter must also scope this KPI, not just the
    lead-stage counts — leads whose conversation started outside [since, until) are
    excluded even if their stage_event rows fall inside the window."""
    from datetime import datetime, timedelta

    from app.api._query import fetch_discovery_metrics
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    old_lead = Lead(branch_id=b.id, created_at=datetime(2026, 1, 1))
    new_lead = Lead(branch_id=b.id, created_at=datetime(2026, 6, 1))
    db_session.add_all([ch, old_lead, new_lead])
    await db_session.flush()
    for lead in (old_lead, new_lead):
        t0 = _NOW - timedelta(minutes=10)
        db_session.add(StageEvent(branch_id=b.id, lead_id=lead.id, from_stage="new",
                                  to_stage="qualifying", created_at=t0))
        db_session.add(StageEvent(branch_id=b.id, lead_id=lead.id, from_stage="qualifying",
                                  to_stage="presenting", created_at=t0 + timedelta(minutes=5)))
    await db_session.flush()

    m = await fetch_discovery_metrics(
        db_session, [b.id], since=datetime(2026, 3, 1), until=datetime(2026, 12, 1))
    assert m["reached"] == 1  # only new_lead's conversation-start falls in the window
