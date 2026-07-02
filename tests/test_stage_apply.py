"""Stage application in the reply pipeline (S1 semantics): decision → lead.stage +
stage_event journal, hand-off (agent off, alert, CAPI), budget gate in decide()."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.adapters.db.models import (
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    Lead,
    ManagerAlert,
    Message,
    StageEvent,
)
from app.adapters.meta_capi import MetaCapi
from app.domain.enums import BOT_SILENT_STAGES, ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import Decision
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import _parse, invalidate

_NOW = datetime.now(UTC).replace(tzinfo=None)


class FakeLLM:
    def __init__(self, decision: dict[str, Any] | None = None) -> None:
        self._payload = json.dumps(decision or {"reply": "ok", "stage": "qualifying"})

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._payload, {"model": "fake", "cost_usd": 0.02}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


class FakeNotifier:
    def __init__(self) -> None:
        self.pings: list[str] = []

    async def notify_manager(self, **kw) -> None:  # noqa: ANN003
        self.pings.append(kw["kind"])


async def _world(s, *, phone: str | None = None, stage: Stage = Stage.NEW,
                 settings: dict[str, str] | None = None) -> tuple[int, int, Lead]:
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    for k, v in (settings or {}).items():
        s.add(AppSetting(branch_id=branch.id, key=k, value=v))
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=stage, phone_e164=phone)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                  external_id="m1", direction="in", sent_by="lead", text="halo",
                  occurred_at=_NOW))
    await s.flush()
    invalidate(branch.id)
    return branch.id, thread.id, lead


def _decision(**over: Any) -> Decision:
    base: dict[str, Any] = {
        "reply": "ok", "stage": Stage.QUALIFYING, "product_slug": None,
        "ready": False, "needs_manager": False,
    }
    base.update(over)
    return Decision(**base)


def _svc(s, bid: int, notifier=None, llm=None) -> ReplyService:  # noqa: ANN001
    return ReplyService(s, bid, llm or FakeLLM(), KnowledgeService(s, bid),
                        branch_settings=_parse({}), notifier=notifier)


async def test_stage_applied_with_journal(db_session) -> None:
    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision())
    assert lead.stage == Stage.QUALIFYING
    ev = (await db_session.exec(select(StageEvent))).first()
    assert ev is not None and ev.from_stage == "new" and ev.to_stage == "qualifying"
    assert ev.actor == "bot"


async def test_same_stage_writes_no_event(db_session) -> None:
    bid, tid, _ = await _world(db_session, stage=Stage.QUALIFYING)
    await _svc(db_session, bid).enqueue_reply(tid, _decision())
    assert (await db_session.exec(select(StageEvent))).first() is None


async def test_ready_with_phone_hands_off(db_session, monkeypatch) -> None:
    capi_calls: list[str] = []

    async def fake_post(self, pixel_id, token, payload):  # noqa: ANN001, ANN201
        capi_calls.append(payload["data"][0]["event_id"])
        return True

    monkeypatch.setattr(MetaCapi, "_post", fake_post)
    notifier = FakeNotifier()
    bid, tid, lead = await _world(
        db_session, phone="+6281234567890",
        settings={"meta_pixel_id": "pix", "meta_capi_token": "tok"},
    )
    svc = ReplyService(db_session, bid, FakeLLM(), KnowledgeService(db_session, bid),
                       branch_settings=_parse({"meta_pixel_id": "pix",
                                               "meta_capi_token": "tok"}),
                       notifier=notifier)
    await svc.enqueue_reply(tid, _decision(ready=True, stage=Stage.PRESENTING))
    assert lead.stage == Stage.READY
    assert lead.agent_enabled is False
    assert lead.handed_off_at is not None
    assert lead.ready_subtype == "deal"
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_deal"
    assert alert.lead_phone == "+6281234567890"
    assert notifier.pings == ["ready_deal"]
    assert capi_calls == [f"handoff-{bid}-{lead.id}"]


async def test_ready_without_phone_keeps_selling(db_session) -> None:
    bid, tid, lead = await _world(db_session, phone=None)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(ready=True))
    assert lead.stage == Stage.PRESENTING
    assert lead.agent_enabled is True


async def test_needs_manager_moves_to_manager_and_mutes(db_session) -> None:
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone="+6281234567890")
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(needs_manager=True, manager_question="Промокод?"),
    )
    assert lead.stage == Stage.MANAGER
    assert lead.agent_enabled is False
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "needs_manager"
    assert alert.lead_phone == "+6281234567890"


async def test_manager_stage_not_in_bot_silent() -> None:
    assert Stage.MANAGER not in BOT_SILENT_STAGES  # silence is per-lead agent_enabled


async def test_product_slug_attributed_once(db_session) -> None:
    bid, tid, _ = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(product_slug="vibe"))
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert thread.product_slug == "vibe"


async def test_budget_gate_blocks_decide(db_session) -> None:
    bid, tid, _ = await _world(db_session, settings={"daily_budget_usd": "0.01"})
    svc = _svc(db_session, bid)
    first = await svc.decide(tid)  # records 0.02 → over budget after this call
    assert first is not None
    assert await svc.decide(tid) is None  # gated now
