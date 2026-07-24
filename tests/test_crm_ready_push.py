"""Ready+phone hand-off pushes the lead into the CRM funnel too (thread 452: the Telegram
alert fired but nothing landed where a manager actually works from)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import Decision
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import _parse

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _FakeLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return json.dumps({"reply": "ok", "stage": "qualifying"}), \
            {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


class _FakePusher:
    """Stands in for CrmMcpPusher — records every call, never touches the network."""

    def __init__(self, url: str, city_alias: str) -> None:
        self.url = url
        self.city_alias = city_alias
        self.calls: list[dict[str, Any]] = []

    async def add_lead_event(
        self, phone: str, event_type: str, *, comment: str, name: str | None,
    ) -> tuple[bool, str]:
        self.calls.append(
            {"phone": phone, "event_type": event_type, "comment": comment, "name": name})
        return True, "ok"


def _decision(**over: Any) -> Decision:
    base: dict[str, Any] = {
        "reply": "ok", "stage": Stage.READY, "product_slug": "smm_intensive",
        "ready": True, "needs_manager": False,
    }
    base.update(over)
    return Decision(**base)


async def _world(s, *, phone: str | None, display_name: str | None = None) -> tuple[int, int]:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=Stage.PRESENTING, phone_e164=phone,
               display_name=display_name)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                  external_id="m1", direction="in", sent_by="lead", text="halo",
                  occurred_at=_NOW))
    await s.flush()
    return branch.id, thread.id


def _svc(s, bid: int, *, crm_on: bool = True) -> ReplyService:  # noqa: ANN001
    cfg = _parse({
        "crm_writeback_enabled": "true" if crm_on else "false",
        "crm_mcp_url": "https://mcp.example/crm",
        "crm_mcp_city_alias": "jakarta",
    })
    return ReplyService(s, bid, _FakeLLM(), KnowledgeService(s, bid),
                        branch_settings=cfg, notifier=None)


async def test_ready_with_phone_queues_crm_push(db_session) -> None:
    """The push no longer runs inline (it held the thread's advisory lock through an LLM
    summary + two MCP round-trips, and a rollback after a successful push duplicated the CRM
    event) — enqueue_reply only QUEUES it; the worker runs push_crm_after_commit once the
    reply transaction has committed."""
    bid, tid = await _world(db_session, phone="+6281234567890", display_name="Amril")
    svc = _svc(db_session, bid)
    await svc.enqueue_reply(tid, _decision())

    assert svc.pending_crm_push is not None
    lead_id, thread_id, reason = svc.pending_crm_push
    assert thread_id == tid
    assert "ready to enrol" in reason


async def test_ready_without_phone_does_not_queue_crm_push(db_session) -> None:
    bid, tid = await _world(db_session, phone=None)
    svc = _svc(db_session, bid)
    await svc.enqueue_reply(tid, _decision())
    # no phone yet — _stage_for keeps it in PRESENTING, no handoff at all
    assert svc.pending_crm_push is None


async def test_crm_writeback_disabled_does_not_queue(db_session) -> None:
    bid, tid = await _world(db_session, phone="+6281234567890")
    svc = _svc(db_session, bid, crm_on=False)
    await svc.enqueue_reply(tid, _decision())
    assert svc.pending_crm_push is None


class _SessionScopeStub:
    """Stands in for session_scope() inside push_crm_after_commit — hands back the test's own
    in-memory session (the global engine can't see StaticPool's single connection)."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self._session = session

    async def __aenter__(self):  # noqa: ANN204
        return self._session

    async def __aexit__(self, *exc) -> None:  # noqa: ANN002
        pass


async def test_push_after_commit_sends_and_marks(db_session, monkeypatch) -> None:
    from app.adapters.db.models import StageEvent
    from app.modules.crm.push_mcp import PUSHED_HANDOFF_REASON

    pushers: list[_FakePusher] = []

    def _spawn(url: str, city_alias: str, timeout_s: float = 30.0) -> _FakePusher:
        p = _FakePusher(url, city_alias)
        pushers.append(p)
        return p

    monkeypatch.setattr("app.modules.crm.push_mcp.CrmMcpPusher", _spawn)
    monkeypatch.setattr(
        "app.adapters.db.session.session_scope", lambda: _SessionScopeStub(db_session))
    bid, tid = await _world(db_session, phone="+6281234567890", display_name=None)
    svc = _svc(db_session, bid)
    await svc.enqueue_reply(tid, _decision())
    assert svc.pending_crm_push is not None

    await svc.push_crm_after_commit()

    assert len(pushers) == 1
    call = pushers[0].calls[0]
    assert call["phone"] == "+6281234567890"
    assert call["name"] == "Stepan"  # no display_name → CRM contact still gets a name
    assert call["comment"]  # never blank — falls back to the reason line
    assert svc.pending_crm_push is None  # consumed
    from sqlmodel import select
    marks = (await db_session.exec(select(StageEvent).where(
        StageEvent.reason == PUSHED_HANDOFF_REASON))).all()
    assert len(marks) == 1  # success marker — drain_handoffs won't re-announce this hand-off
