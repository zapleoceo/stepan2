"""Webhook ingest writes into the SAME threads the poll writes into.

The webhook and the poll describe a conversation differently — the poll stores the Graph
conversation id as external_thread_id, the webhook only knows the sender's PSID. Keyed
naively, every lead who writes while both paths run would end up with two threads: two
histories, two 24h windows, and a bot answering the half it can see. These tests pin the
reconciliation and the dedup that stop that.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.leads.ingest import IngestService
from app.modules.meta import webhook_ingest
from app.ports.channel import InboundMessage

_TS_MS = 1_754_200_000_000
_OCCURRED = datetime.fromtimestamp(_TS_MS // 1000, tz=UTC).replace(tzinfo=None)
_CONVO = "t_conversation_1"


class _FakePort:
    """Stands in for MetaBusinessAdapter — only the reverse lookup matters here."""

    def __init__(self, convo: str | None = _CONVO) -> None:
        self.convo = convo
        self.lookups: list[str] = []

    async def find_conversation_id(self, user_id: str) -> str | None:
        self.lookups.append(user_id)
        return self.convo


def _event(mid: str = "m_aaa", text: str = "halo", sender: str = "PSID1") -> dict:
    return {
        "object": "page",
        "entry": [{
            "id": "PAGE1",
            "messaging": [{
                "sender": {"id": sender},
                "recipient": {"id": "PAGE1"},
                "timestamp": _TS_MS,
                "message": {"mid": mid, "text": text},
            }],
        }],
    }


async def _world(s, *, active: bool = True, page_id: str = "PAGE1") -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.META_BUSINESS,
                 account_id=page_id, is_active=active)
    s.add(ch)
    await s.flush()
    return b.id, ch.id


async def _existing_thread(s, branch_id: int, channel_id: int, psid: str) -> ChannelThread:
    """A thread as the POLL would have created it: keyed on the conversation id, with the
    lead's sender id stamped onto Lead.ig_user_id."""
    lead = Lead(branch_id=branch_id, stage=Stage.QUALIFYING, ig_user_id=psid)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=channel_id, external_thread_id=_CONVO)
    s.add(thread)
    await s.flush()
    return thread


def _wire(monkeypatch, session, port: _FakePort | None) -> None:
    @asynccontextmanager
    async def _scope():
        yield session

    async def _build(_s, _channel):
        if port is None:
            raise RuntimeError("no active token")
        return port

    monkeypatch.setattr(webhook_ingest, "session_scope", _scope)
    monkeypatch.setattr("app.worker.wiring.build_channel_port", _build)


async def _messages(s, branch_id: int) -> list[Message]:
    rows = await s.exec(select(Message).where(Message.branch_id == branch_id))
    return list(rows.all())


async def test_webhook_message_lands_in_the_thread_the_poll_already_created(
    db_session, monkeypatch,
) -> None:
    """The reconciliation, from the DB side. A lead we already know writes again: their message
    must join the existing conversation, not open a second one keyed on their PSID."""
    branch_id, channel_id = await _world(db_session)
    thread = await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    port = _FakePort()
    _wire(monkeypatch, db_session, port)

    stored = await webhook_ingest.ingest_webhook_messages(
        branch_id, _events(_event()))

    assert stored == 1
    threads = (await db_session.exec(select(ChannelThread))).all()
    assert len(threads) == 1
    [msg] = await _messages(db_session, branch_id)
    assert msg.thread_id == thread.id
    assert msg.external_id == "m_aaa"
    assert port.lookups == []  # a known sender costs zero Graph calls


async def test_unknown_sender_is_translated_into_the_conversation_id(
    db_session, monkeypatch,
) -> None:
    """First contact: nothing in the DB maps the PSID yet, so the conversation id has to come
    from Graph. Storing the raw PSID instead is the duplicate-thread bug."""
    branch_id, channel_id = await _world(db_session)
    port = _FakePort()
    _wire(monkeypatch, db_session, port)

    stored = await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event()))

    assert stored == 1
    [thread] = (await db_session.exec(select(ChannelThread))).all()
    assert thread.external_thread_id == _CONVO
    assert port.lookups == ["PSID1"]


async def test_an_unresolvable_sender_is_left_to_the_poll(db_session, monkeypatch) -> None:
    """Graph could not name the conversation. Inventing a key would create the second thread;
    dropping the message costs one poll cycle of latency and nothing else."""
    branch_id, _ = await _world(db_session)
    _wire(monkeypatch, db_session, _FakePort(convo=None))

    stored = await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event()))

    assert stored == 0
    assert (await db_session.exec(select(ChannelThread))).all() == []


async def test_a_known_sender_still_ingests_when_the_graph_port_is_dead(
    db_session, monkeypatch,
) -> None:
    """A missing/expired token must not stop the half of the reconciliation that is pure DB —
    that is the common case (a lead we already have a thread for) and it needs no network."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, None)

    assert await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event())) == 1


async def test_the_poll_does_not_re_store_what_the_webhook_already_ingested(
    db_session, monkeypatch,
) -> None:
    """The dedup contract with step S3: once the poll carries Meta's native message id, the
    same message arriving on both paths is one row. Without it the lead's question appears
    twice in the thread and in the model's context."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())
    await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event()))

    polled = InboundMessage(
        external_thread_id=_CONVO, sender_id="PSID1", text="halo",
        occurred_at=_OCCURRED, external_id="m_aaa",
    )
    await IngestService(db_session, branch_id).ingest(channel_id, [polled])

    assert len(await _messages(db_session, branch_id)) == 1


async def test_a_page_this_branch_does_not_own_is_dropped(db_session, monkeypatch) -> None:
    """branch_id comes from the webhook URL and the signature proves only that the caller holds
    THAT branch's app secret. Without scoping the channel lookup by branch, one tenant's valid
    signature could write into another tenant's conversation."""
    branch_id, _ = await _world(db_session, page_id="OTHER_PAGE")
    _wire(monkeypatch, db_session, _FakePort())

    assert await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event())) == 0


async def test_a_switched_off_channel_receives_nothing(db_session, monkeypatch) -> None:
    """Meta keeps delivering long after an operator disables a connector; an inactive channel
    must not quietly resume ingesting behind their back."""
    branch_id, _ = await _world(db_session, active=False)
    _wire(monkeypatch, db_session, _FakePort())

    assert await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event())) == 0


def _events(payload: dict) -> list[dict]:
    from app.modules.meta.webhook_parse import parse_meta_messages  # noqa: PLC0415

    return [m.as_dict() for m in parse_meta_messages(payload)]
