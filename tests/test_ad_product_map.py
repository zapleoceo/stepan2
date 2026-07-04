"""Ad→product mapping: operator map roundtrip, history suggestion, ingest auto-bind at
first inbound, and the provenance rules that let Stepan re-qualify an ad-derived product
while a manager's manual pick stays locked."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Message,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.ads import AdMappingService
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import Decision
from app.modules.knowledge.service import KnowledgeService
from app.modules.leads.ingest import IngestService
from app.modules.settings.service import _parse
from app.ports.channel import InboundMessage

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _branch_channel(s) -> tuple[int, int]:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    return branch.id, ch.id


def _inbound(ext: str = "ig-1", *, ad_id: str | None = None) -> InboundMessage:
    return InboundMessage(
        external_thread_id=ext, sender_id="u1", text="halo", occurred_at=_NOW,
        ad_id=ad_id, external_id=f"m-{ext}",
    )


# ─── mapping service ──────────────────────────────────────────────────────────

async def test_upsert_and_lookup_roundtrip(db_session) -> None:
    bid, _ = await _branch_channel(db_session)
    svc = AdMappingService(db_session, bid)
    assert await svc.product_for_ad("AD1") is None
    await svc.upsert("AD1", "vibe_coding", actor="owner")
    assert await svc.product_for_ad("AD1") == "vibe_coding"
    await svc.upsert("AD1", "smm_intensive", actor="owner")  # update, not duplicate
    assert await svc.product_for_ad("AD1") == "smm_intensive"
    assert await svc.all_mappings() == {"AD1": "smm_intensive"}


async def test_lookup_is_branch_scoped(db_session) -> None:
    bid1, _ = await _branch_channel(db_session)
    bid2, _ = await _branch_channel(db_session)
    await AdMappingService(db_session, bid1).upsert("AD1", "vibe_coding", actor=None)
    assert await AdMappingService(db_session, bid2).product_for_ad("AD1") is None


async def test_clear_removes_mapping(db_session) -> None:
    bid, _ = await _branch_channel(db_session)
    svc = AdMappingService(db_session, bid)
    await svc.upsert("AD1", "vibe_coding", actor=None)
    await svc.clear("AD1")
    assert await svc.product_for_ad("AD1") is None


async def test_suggest_from_history_takes_majority(db_session) -> None:
    bid, ch = await _branch_channel(db_session)
    lead = Lead(branch_id=bid, stage=Stage.QUALIFYING)
    db_session.add(lead)
    await db_session.flush()
    # AD1 → mostly smm_intensive (2 vs 1 vibe), AD2 → vibe only, AD3 → all empty (ignored)
    slugs = ["smm_intensive", "smm_intensive", "vibe_coding", None]
    for i, slug in enumerate(slugs):
        db_session.add(ChannelThread(
            lead_id=lead.id, channel_id=ch, external_thread_id=f"a1-{i}",
            ad_id="AD1", product_slug=slug))
    db_session.add(ChannelThread(
        lead_id=lead.id, channel_id=ch, external_thread_id="a2",
        ad_id="AD2", product_slug="vibe_coding"))
    db_session.add(ChannelThread(
        lead_id=lead.id, channel_id=ch, external_thread_id="a3",
        ad_id="AD3", product_slug=None))
    await db_session.flush()
    suggestion = await AdMappingService(db_session, bid).suggest_from_history()
    assert suggestion == {"AD1": "smm_intensive", "AD2": "vibe_coding"}


# ─── ingest auto-bind ─────────────────────────────────────────────────────────

async def test_ingest_binds_product_from_ad_map(db_session) -> None:
    bid, ch = await _branch_channel(db_session)
    await AdMappingService(db_session, bid).upsert("AD1", "smm_intensive", actor="owner")
    await IngestService(db_session, bid).ingest(ch, [_inbound(ad_id="AD1")])
    thread = (await db_session.exec(select(ChannelThread))).first()
    assert thread.product_slug == "smm_intensive"
    assert thread.product_source == "ad"
    assert thread.ad_id == "AD1"


async def test_ingest_leaves_product_none_when_ad_unmapped(db_session) -> None:
    bid, ch = await _branch_channel(db_session)
    await IngestService(db_session, bid).ingest(ch, [_inbound(ad_id="AD_UNKNOWN")])
    thread = (await db_session.exec(select(ChannelThread))).first()
    assert thread.product_slug is None
    assert thread.product_source is None


async def test_ingest_no_ad_no_binding(db_session) -> None:
    bid, ch = await _branch_channel(db_session)
    await IngestService(db_session, bid).ingest(ch, [_inbound(ad_id=None)])
    thread = (await db_session.exec(select(ChannelThread))).first()
    assert thread.product_slug is None


# ─── model override provenance ────────────────────────────────────────────────

def _decision(**over: Any) -> Decision:
    base: dict[str, Any] = {
        "reply": "ok", "stage": Stage.QUALIFYING, "product_slug": None,
        "ready": False, "needs_manager": False,
    }
    base.update(over)
    return Decision(**base)


async def _thread_with_source(s, source: str | None, slug: str | None):  # noqa: ANN001
    bid, ch = await _branch_channel(s)
    lead = Lead(branch_id=bid, stage=Stage.QUALIFYING)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch, external_thread_id="ig-1",
                           product_slug=slug, product_source=source)
    s.add(thread)
    await s.flush()
    s.add(Message(branch_id=bid, thread_id=thread.id, channel_id=ch, external_id="m1",
                  direction="in", sent_by="lead", text="halo", occurred_at=_NOW))
    await s.flush()
    return bid, thread.id


def _svc(s, bid: int):  # noqa: ANN001, ANN201
    from tests.test_stage_apply import FakeLLM  # reuse the module's stub
    return ReplyService(s, bid, FakeLLM(), KnowledgeService(s, bid),
                        branch_settings=_parse({}), notifier=None)


async def test_model_overrides_ad_sourced_product(db_session) -> None:
    bid, tid = await _thread_with_source(db_session, "ad", "smm_intensive")
    await _svc(db_session, bid).enqueue_reply(tid, _decision(product_slug="vibe_coding"))
    thread = (await db_session.exec(select(ChannelThread))).first()
    assert thread.product_slug == "vibe_coding"
    assert thread.product_source == "model"


async def test_model_does_not_override_manager_product(db_session) -> None:
    bid, tid = await _thread_with_source(db_session, "manager", "smm_intensive")
    await _svc(db_session, bid).enqueue_reply(tid, _decision(product_slug="vibe_coding"))
    thread = (await db_session.exec(select(ChannelThread))).first()
    assert thread.product_slug == "smm_intensive"  # manager pick locked
    assert thread.product_source == "manager"


async def test_model_binds_when_unset(db_session) -> None:
    bid, tid = await _thread_with_source(db_session, None, None)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(product_slug="data_analyst"))
    thread = (await db_session.exec(select(ChannelThread))).first()
    assert thread.product_slug == "data_analyst"
    assert thread.product_source == "model"
