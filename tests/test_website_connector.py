"""The website connector, and the machinery that must stop at its declaration.

The registry contract (tests/test_connector_registry.py) already holds this spec to the same
rules as every other. What is new here is the one thing a website chat has that no DM
connector has: an anonymous correspondent who cannot be written to once the request is over.
Every test below sets up a thread that is due for a proactive touch in every OTHER respect,
so the only reason it is skipped is ConnectorSpec.proactive_outreach.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.adapters.channels.website import NO_SEND_API, WebsiteAdapter  # noqa: E402
from app.adapters.db.models import (  # noqa: E402
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Message,
    Outbox,
    StageEvent,
)
from app.connectors.registry import REGISTRY, does_proactive_outreach  # noqa: E402
from app.domain.enums import ChannelKind, Stage  # noqa: E402
from app.modules.conversation.followup import FollowupService  # noqa: E402
from app.modules.conversation.outbox import OutboxSender  # noqa: E402
from app.modules.conversation.reactivation import ReactivationService  # noqa: E402
from app.modules.knowledge.service import KnowledgeService  # noqa: E402
from app.modules.settings.service import _parse, invalidate  # noqa: E402
from app.ports.channel import SendResult  # noqa: E402

_NOW = datetime.now(UTC).replace(tzinfo=None)
_SPEC = REGISTRY[ChannelKind.WEBSITE]


# ─── the declaration itself ───────────────────────────────────────────────────

def test_the_spec_declares_only_what_a_web_page_actually_has() -> None:
    """Inbound arrives synchronously and that is all. No private-API powers, no reply window,
    no unanswered queue, and — the S6 decision — no writing first."""
    assert _SPEC.capabilities == frozenset()
    assert _SPEC.send_window is None
    assert _SPEC.counts_as_awaiting is False
    assert _SPEC.proactive_outreach is False
    assert _SPEC.operator_addable is False
    # every DM connector keeps the old behaviour; only the site opts out
    assert [s.kind.value for s in REGISTRY.values() if not s.proactive_outreach] == ["website"]
    assert [s.kind.value for s in REGISTRY.values() if not s.operator_addable] == ["website"]


# ─── who may create the channel ───────────────────────────────────────────────
#
# This row is not an account somebody connects. Its existence is what website_branch_id reads
# to decide which branch the PUBLIC landing page sells from, so one operator adding one to
# their own branch would repoint that page at their tenant's persona, prices and catalogue —
# a branch-scoped write turning into a global, cross-tenant, publicly visible change.


class _Req:
    """The three things channel_create reads off a request."""

    def __init__(self, writable: list[int] | None) -> None:
        self.cookies: dict[str, str] = {}
        self.state = type("S", (), {"writable_branch_ids": writable,
                                    "allowed_branch_ids": writable})()


@pytest.mark.asyncio
async def test_the_create_route_refuses_a_website_channel_from_any_branch(
    db_session, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """Branch 1 is the live Indonesian tenant and has the lowest id there is, so it is the
    worst case: its operator picking "website" would take the landing page over."""
    import contextlib

    from app.api import _routes_channels as routes

    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        yield db_session

    monkeypatch.setattr(routes, "session_scope", _scope)
    # The channel table renders through a Postgres-only ANY() query; it is not what is under
    # test here, and the insert it would render happens before it.
    async def _rendered(session: Any, branch_id: int) -> str:
        return "<table></table>"

    monkeypatch.setattr(routes, "_ch_list_html", _rendered)
    branch = Branch(name="Indonesia", lang="id")
    db_session.add(branch)
    await db_session.flush()
    bid = int(branch.id or 0)
    request = _Req([bid])  # a WRITE-role operator on their OWN branch

    refused = await routes.channel_create(bid, request, kind="website", handle="site",
                                          account_id="", is_active="on")

    assert refused.status_code == 403
    assert (await db_session.execute(
        Channel.__table__.select().where(Channel.kind == ChannelKind.WEBSITE))).all() == []
    # the same operator's ordinary connector still goes in — this is a kind gate, not a
    # branch gate that happens to also block website
    ok = await routes.channel_create(bid, request, kind="instagram", handle="@x",
                                     account_id="", is_active="on")
    assert ok.status_code == 200
    kinds = (await db_session.execute(
        Channel.__table__.select().where(Channel.branch_id == bid))).all()
    assert [r.kind for r in kinds] == ["instagram"]


@pytest.mark.asyncio
async def test_the_schema_refuses_a_second_website_channel(db_session) -> None:  # noqa: ANN001
    """The process lock in ensure_website_branch covers one uvicorn worker and is released
    before the caller's transaction commits. This index is what actually makes "the branch the
    site sells from" a single answer, whoever writes second."""
    from sqlalchemy.exc import IntegrityError

    for name in ("Website", "Impostor"):
        branch = Branch(name=name, lang="en")
        db_session.add(branch)
        await db_session.flush()
        db_session.add(Channel(branch_id=branch.id, kind=ChannelKind.WEBSITE, handle=name))
        if name == "Website":
            await db_session.flush()
            continue
        with pytest.raises(IntegrityError):
            await db_session.flush()
    await db_session.rollback()


def test_an_unregistered_kind_still_gets_its_follow_ups() -> None:
    """Opposite default from `supports`, on purpose: a channel row this build does not know
    (a kind from a newer deploy, a hand-written row) must keep behaving as it always did
    rather than have its outreach silently switched off."""
    assert does_proactive_outreach("tiktok") is True
    assert does_proactive_outreach(None) is True
    assert does_proactive_outreach(ChannelKind.WEBSITE) is False


@pytest.mark.asyncio
async def test_the_adapter_refuses_to_send_and_has_nothing_to_poll() -> None:
    port = WebsiteAdapter()
    assert await port.fetch_inbound() == []
    result = await port.send_text("anyone", "hello?")
    assert result.ok is False
    assert result.error == NO_SEND_API


# ─── the harvests ─────────────────────────────────────────────────────────────

def _cfg(**over: str) -> Any:
    return _parse({"followup_enabled": "true", "agent_enabled_global": "true",
                   "reactivation_enabled": "true", "quiet_start": "0", "quiet_end": "0",
                   "hourly_cap": "0", "daily_cap": "0", **over})


async def _due_thread(s, kind: ChannelKind) -> tuple[int, int, int]:  # noqa: ANN001
    """A branch + a thread that satisfies every condition of BOTH harvests except the kind."""
    branch = Branch(name="B", lang="en")
    s.add(branch)
    await s.flush()
    for key, value in (("followup_enabled", "true"), ("agent_enabled_global", "true"),
                       ("hourly_cap", "0"), ("daily_cap", "0")):
        s.add(AppSetting(branch_id=branch.id, key=key, value=value))
    ch = Channel(branch_id=branch.id, kind=kind)
    s.add(ch)
    lead = Lead(branch_id=branch.id, stage=Stage.QUALIFYING, agent_enabled=True)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=ch.id, external_thread_id="t-1",
        last_out_at=_NOW - timedelta(days=10), last_in_at=_NOW - timedelta(days=11),
        next_followup_at=_NOW - timedelta(minutes=1))
    s.add(thread)
    await s.flush()
    s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                  external_id="m1", direction="in", sent_by="lead", text="hi",
                  occurred_at=_NOW - timedelta(days=11)))
    await s.flush()
    invalidate(branch.id)
    return int(branch.id or 0), int(thread.id or 0), int(ch.id or 0)


def _followups(s, bid: int):  # noqa: ANN001, ANN201
    return FollowupService(s, bid, None, KnowledgeService(s, bid), _cfg())


@pytest.mark.asyncio
async def test_followup_harvest_takes_a_dm_thread_and_leaves_the_website_one(
    db_session,  # noqa: ANN001
) -> None:
    """Two threads, identical in every field the follow-up query reads; only the connector
    differs. The Instagram one is due, so the query and the fixture are both proven right —
    without it a website thread skipped for the wrong reason would look like a pass."""
    ig_bid, ig_tid, _ = await _due_thread(db_session, ChannelKind.INSTAGRAM)
    assert await _followups(db_session, ig_bid).due_threads(_NOW) == [(ig_tid, None, 0)]

    web_bid, _web_tid, _ = await _due_thread(db_session, ChannelKind.WEBSITE)
    assert await _followups(db_session, web_bid).due_threads(_NOW) == []


@pytest.mark.asyncio
async def test_reactivation_harvest_takes_a_dm_thread_and_leaves_the_website_one(
    db_session,  # noqa: ANN001
) -> None:
    async def _dormant(kind: ChannelKind) -> tuple[int, int]:
        bid, tid, _ = await _due_thread(db_session, kind)
        thread = await db_session.get(ChannelThread, tid)
        thread.next_followup_at = None  # a live follow-up timer excludes a thread from `due`
        db_session.add(thread)
        await db_session.flush()
        return bid, tid

    ig_bid, ig_tid = await _dormant(ChannelKind.INSTAGRAM)
    svc = ReactivationService(db_session, ig_bid, None, KnowledgeService(db_session, ig_bid),
                              _cfg())
    assert [t for t, _s, _l in await svc.due(_NOW)] == [ig_tid]

    web_bid, _web_tid = await _dormant(ChannelKind.WEBSITE)
    web = ReactivationService(db_session, web_bid, None,
                              KnowledgeService(db_session, web_bid), _cfg())
    assert await web.due(_NOW) == []


class _Port:
    """Records what it was asked to send. Not a stand-in that swallows everything: the point
    of the test below is that the send SUCCEEDS and the timer still is not armed."""

    def __init__(self, kind: ChannelKind) -> None:
        self.kind = kind
        self.sent: list[str] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append(text)
        return SendResult(ok=True, external_message_id="x")

    async def session_status(self) -> Any:
        return None


@pytest.mark.asyncio
async def test_a_delivered_line_arms_no_timer_and_never_winds_down_to_dormant(
    db_session,  # noqa: ANN001
) -> None:
    """The wind-down to DORMANT only ever fires when the follow-up ladder runs out, so a
    connector that arms no ladder can never reach it. Arming one anyway would park the
    visitor as dormant days later for a reason that never happened."""
    for kind, expect_timer in ((ChannelKind.INSTAGRAM, True), (ChannelKind.WEBSITE, False)):
        bid, tid, _ = await _due_thread(db_session, kind)
        thread = await db_session.get(ChannelThread, tid)
        thread.next_followup_at = None
        db_session.add(thread)
        db_session.add(Outbox(branch_id=bid, thread_id=tid, text="hi", source="agent",
                              status="pending", scheduled_at=_NOW - timedelta(seconds=5)))
        await db_session.flush()

        port = _Port(kind)
        row = await OutboxSender(db_session, bid, port).send_next(tid)

        assert row is not None and row.status == "sent" and port.sent == ["hi"]
        assert (thread.next_followup_at is not None) is expect_timer, kind
        parked = (await db_session.execute(
            StageEvent.__table__.select().where(StageEvent.thread_id == tid))).all()
        assert parked == []
