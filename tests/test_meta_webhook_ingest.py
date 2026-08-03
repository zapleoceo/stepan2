"""Webhook ingest writes into the SAME threads the poll writes into.

The webhook and the poll describe a conversation differently — the poll stores the Graph
conversation id as external_thread_id, the webhook only knows the sender's PSID. Keyed
naively, every lead who writes while both paths run would end up with two threads: two
histories, two 24h windows, and a bot answering the half it can see. These tests pin the
reconciliation and the dedup that stop that.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    MediaAsset,
    Message,
)
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


def _event(
    mid: str = "m_aaa", text: str = "halo", sender: str = "PSID1",
    *, object_type: str = "page", entry_id: str = "PAGE1", message: dict | None = None,
) -> dict:
    return {
        "object": object_type,
        "entry": [{
            "id": entry_id,
            "messaging": [{
                "sender": {"id": sender},
                "recipient": {"id": entry_id},
                "timestamp": _TS_MS,
                "message": message or {"mid": mid, "text": text},
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


async def test_an_unmatched_entry_id_is_logged_loudly_not_at_info(
    db_session, monkeypatch,
) -> None:
    """The one failure mode this whole step exists to remove is SILENT loss. The HTTP layer has
    already answered 200, Meta will not retry, and the poll keeps delivering leads — so if this
    drop is not greppable in the log, nobody ever learns the webhook ingests nothing.

    Spy the logger METHOD, like test_config_validate and test_channels do: a handler sees
    nothing once tests/test_infra.py has run alembic, whose migrations/env.py calls
    fileConfig() — which disables every logger imported before it."""
    branch_id, _ = await _world(db_session, page_id="OTHER_PAGE")
    _wire(monkeypatch, db_session, _FakePort())
    warnings: list[str] = []
    monkeypatch.setattr(webhook_ingest._log, "warning",
                        lambda msg, *a, **k: warnings.append(msg % a if a else msg))

    assert await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event())) == 0

    assert any("META WEBHOOK DROPPED" in w for w in warnings)


async def test_an_instagram_dm_reaches_the_channel_despite_a_foreign_entry_id(
    db_session, monkeypatch,
) -> None:
    """For object='instagram' Meta sends the INSTAGRAM professional-account id in entry.id, not
    the Page id we store on Channel.account_id — so the direct match cannot hit and every
    Instagram DM (Stepan's entire revenue path) was dropped. One active Meta channel on the
    branch is an unambiguous join; anything else refuses to guess."""
    branch_id, channel_id = await _world(db_session, page_id="PAGE1")
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())

    stored = await webhook_ingest.ingest_webhook_messages(
        branch_id, _events(_event(object_type="instagram", entry_id="IG_ACCOUNT_17841")))

    assert stored == 1
    [msg] = await _messages(db_session, branch_id)
    assert msg.channel_id == channel_id


async def test_two_meta_channels_make_an_unmatched_instagram_entry_a_refusal(
    db_session, monkeypatch,
) -> None:
    """The fallback is a join, not a shrug. With two candidates, routing a client's DM into the
    wrong inbox is worse than the poll-cycle delay of dropping it."""
    branch_id, channel_id = await _world(db_session, page_id="PAGE1")
    second = Channel(branch_id=branch_id, kind=ChannelKind.META_BUSINESS,
                     account_id="PAGE2", is_active=True)
    db_session.add(second)
    await db_session.flush()
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())

    assert await webhook_ingest.ingest_webhook_messages(
        branch_id, _events(_event(object_type="instagram", entry_id="IG_ACCOUNT_17841"))) == 0


async def test_a_photo_the_webhook_stored_is_not_stored_again_by_the_poll(
    db_session, monkeypatch,
) -> None:
    """The regression this step shipped: the webhook describes a photo as '🖼 media' + a
    MediaAsset, Graph's /conversations returns the same message with an EMPTY message and no
    attachment, so the text compare found no match and wrote a second, blank inbound. That
    blank row re-opened the 24h window, reset the follow-up cycle and reached the model as
    silence — on every media DM."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())
    photo = {"mid": "m_pic", "attachments": [
        {"type": "image", "payload": {"url": "https://cdn/i.jpg"}}]}
    await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event(message=photo)))

    polled = InboundMessage(  # exactly what MetaBusinessAdapter._to_inbound builds for it
        external_thread_id=_CONVO, sender_id="PSID1", text="", occurred_at=_OCCURRED,
    )
    await IngestService(db_session, branch_id).ingest(channel_id, [polled])

    rows = await _messages(db_session, branch_id)
    assert [r.text for r in rows] == ["🖼 media"]


async def test_a_photo_the_poll_delivered_blank_first_is_filled_in_not_duplicated(
    db_session, monkeypatch,
) -> None:
    """The same collapse in the other order, which is the ORDINARY one: the Graph poll fires
    every two minutes while the webhook job waits behind whatever the arq worker is already
    doing, so any worker lag puts the blank copy first. Guarding only the webhook-wins direction
    left every media DM in that window duplicated exactly as before — the blank row re-opening
    the 24h window and reaching the model as silence."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())

    polled = InboundMessage(
        external_thread_id=_CONVO, sender_id="PSID1", text="", occurred_at=_OCCURRED,
    )
    await IngestService(db_session, branch_id).ingest(channel_id, [polled])
    photo = {"mid": "m_pic", "attachments": [
        {"type": "image", "payload": {"url": "https://cdn/i.jpg"}}]}
    await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event(message=photo)))

    [row] = await _messages(db_session, branch_id)
    assert row.text == "🖼 media"
    assert row.media_pending is True
    assert row.external_id == "m_pic"  # a Meta redelivery is now dedupable by id alone
    assets = (await db_session.exec(select(MediaAsset))).all()
    assert [a.message_id for a in assets] == [row.id]


async def test_a_blank_media_copy_survives_unrelated_text_a_second_earlier(
    db_session, monkeypatch,
) -> None:
    """The guard matches on the instant, so its reach has to be bounded by something else or a
    plain text row eats a DIFFERENT, real photo the poll delivers a second later — a silent
    inbound drop on the shared write path. Only a neighbour that CARRIES an attachment can be
    another description of the same message."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())
    await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event(text="halo")))

    polled = InboundMessage(  # a real attachment, one second later, blank as Graph returns it
        external_thread_id=_CONVO, sender_id="PSID1", text="",
        occurred_at=_OCCURRED + timedelta(seconds=1),
    )
    await IngestService(db_session, branch_id).ingest(channel_id, [polled])

    assert len(await _messages(db_session, branch_id)) == 2


async def test_two_blank_attachments_seconds_apart_are_two_messages(db_session) -> None:
    """A lead sending two photos in a burst reaches the poll as two empty Graph messages. An
    empty text is the absence of content, not content two rows can share, so matching them by
    text made the second one vanish — the loss class 8d45063 exists to prevent."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    svc = IngestService(db_session, branch_id)

    for delta in (0, 1):
        await svc.ingest(channel_id, [InboundMessage(
            external_thread_id=_CONVO, sender_id="PSID1", text="",
            occurred_at=_OCCURRED + timedelta(seconds=delta),
        )])

    assert len(await _messages(db_session, branch_id)) == 2


async def test_text_with_a_trailing_newline_is_not_stored_twice(
    db_session, monkeypatch,
) -> None:
    """A one-character .strip() in the parser disarmed the only dedup that exists until S3:
    the webhook stored 'halo', the poll stored 'halo\\n', and the exact-text compare missed."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")
    _wire(monkeypatch, db_session, _FakePort())
    await webhook_ingest.ingest_webhook_messages(branch_id, _events(_event(text="halo\n")))

    polled = InboundMessage(
        external_thread_id=_CONVO, sender_id="PSID1", text="halo\n", occurred_at=_OCCURRED,
    )
    await IngestService(db_session, branch_id).ingest(channel_id, [polled])

    assert len(await _messages(db_session, branch_id)) == 1


async def test_a_blank_polled_message_is_still_stored_when_the_webhook_never_ran(
    db_session, monkeypatch,
) -> None:
    """The contentless guard must not eat the poll's only copy. With no webhook row at that
    instant there is nothing to be a duplicate OF, and losing it would be the silent drop the
    step is meant to remove — webhooks are not live on every channel yet."""
    branch_id, channel_id = await _world(db_session)
    await _existing_thread(db_session, branch_id, channel_id, "PSID1")

    polled = InboundMessage(
        external_thread_id=_CONVO, sender_id="PSID1", text="", occurred_at=_OCCURRED,
    )
    created = await IngestService(db_session, branch_id).ingest(channel_id, [polled])

    assert len(created) == 1


def _events(payload: dict) -> list[dict]:
    from app.modules.meta.webhook_parse import parse_meta_messages  # noqa: PLC0415

    return [m.as_dict() for m in parse_meta_messages(payload)]
