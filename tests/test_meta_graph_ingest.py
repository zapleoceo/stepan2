"""What the official Graph connector (branch 7) loses on the way from Graph into a Message row.

Three separate live gaps, all on the poll path:

1. A photo, video or voice DM arrives from Graph with an EMPTY `message` string and its real
   content sitting in `attachments` — which the poll never asked for. The message ingested as
   a blank row: nothing for the model to read, nothing for the media backfill to fetch.
2. The adapter never set `direction`, so every consumer downstream had to assume "in" and take
   it on faith that the transport had filtered our own Page replies out.
3. Nothing carried Graph's native message id (mid), so the row's external_id was a synthetic
   thread+time+text hash. A webhook delivery and a poll of the SAME message hash differently
   and would land as two rows.

Branch 1 (Indonesia, 37k messages) polls instagrapi and is not on this path at all — the last
two tests pin that none of this leaked into it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import select

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.adapters.channels.meta_business import MetaBusinessAdapter
from app.adapters.channels.transports import GraphTransportHTTP, download_bounded
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
from app.ports.channel import InboundMessage

_PAGE = "207513496325789"
_OWN_IG = "17841406968997652"
_LEAD = "1690990202132445"
_WHEN = "2026-08-03T10:00:01+0000"


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _Client:
    """Serves one page of Instagram conversations and records the requested fields."""

    def __init__(self, convs: list[dict], *, both_platforms: bool = False) -> None:
        self.convs = convs
        self.both = both_platforms
        self.fields: list[str] = []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def get(self, _url: str, params: dict) -> _Resp:
        if params.get("fields") == "instagram_business_account":
            return _Resp({"id": _PAGE, "instagram_business_account": {"id": _OWN_IG}})
        self.fields.append(params.get("fields", ""))
        if self.both or params.get("platform") == "instagram":
            return _Resp({"data": self.convs})
        return _Resp({"data": []})


class _FieldPickyClient(_Client):
    """A Graph that 400s any request naming `attachments` — an unknown or unpermitted
    subfield rejects the WHOLE call, both platforms, every tick."""

    def __init__(self, convs: list[dict]) -> None:
        super().__init__(convs, both_platforms=True)
        self.attempted: list[str] = []

    async def get(self, url: str, params: dict) -> _Resp:
        import httpx

        fields = params.get("fields", "")
        if fields != "instagram_business_account":
            self.attempted.append(fields)
        if "attachments" in fields:
            req = httpx.Request("GET", "https://graph.facebook.com/v21.0/x")
            raise httpx.HTTPStatusError(
                "(#100) Tried accessing nonexisting field (attachments)",
                request=req, response=httpx.Response(400, request=req))
        return await super().get(url, params)


def _msg(text: str = "", *, mid: str = "m1", attachments: list[dict] | None = None,
         when: str = _WHEN, who: str = _LEAD) -> dict:
    out: dict = {"id": mid, "from": {"id": who}, "message": text, "created_time": when}
    if attachments is not None:
        out["attachments"] = {"data": attachments}
    return out


def _conv(msgs: list[dict], cid: str = "ig_1") -> dict:
    return {"id": cid,
            "participants": {"data": [{"id": _OWN_IG, "username": "zapleosoft"},
                                      {"id": _LEAD, "username": "client"}]},
            "messages": {"data": msgs}}


def _transport(client: _Client) -> GraphTransportHTTP:
    t = GraphTransportHTTP(base_url="https://graph.facebook.com/v21.0",
                           account_id=_PAGE, token="T")  # noqa: S106
    t._client = lambda: client  # type: ignore[method-assign]
    return t


class _FakeGraph:
    """A GraphTransport serving already-shaped rows, so the adapter can be tested alone."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch_conversations(self) -> list[dict]:
        return self._rows

    async def send_message(self, recipient_id: str, text: str) -> dict:
        return {"message_id": "x"}

    async def token_debug(self) -> dict:
        return {"is_valid": True}

    async def download_media(self, url: str) -> bytes:
        return b"bytes"


# ---------------------------------------------------------------- attachments (finding b)

@pytest.mark.asyncio
async def test_a_photo_dm_no_longer_arrives_as_an_empty_message() -> None:
    """The reported shape: message="" plus an image attachment. It used to ingest blank."""
    client = _Client([_conv([_msg(attachments=[
        {"mime_type": "image/jpeg",
         "image_data": {"url": "https://lookaside.fbsbx.com/a.jpg"}}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["message"] == IMAGE_PENDING_PH
    assert row["media_kind"] == "image"
    assert row["media_url"] == "https://lookaside.fbsbx.com/a.jpg"


@pytest.mark.asyncio
async def test_a_voice_dm_gets_the_same_placeholder_the_instagrapi_path_writes() -> None:
    """One convention, not two: the transcriber swaps '🎤 voice' for the words, and the reply
    hold recognises that exact string. A Graph-only placeholder would be invisible to both."""
    client = _Client([_conv([_msg(attachments=[
        {"mime_type": "audio/mp4", "file_url": "https://lookaside.fbsbx.com/v.mp4"}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["message"] == VOICE_PENDING_PH
    assert row["media_kind"] == "audio"
    assert row["media_url"] == "https://lookaside.fbsbx.com/v.mp4"


@pytest.mark.asyncio
async def test_a_video_dm_keeps_the_video_kind_and_the_image_placeholder() -> None:
    """Mirrors IG item_type 'media', which covers photo AND video and yields '🖼 media'."""
    client = _Client([_conv([_msg(attachments=[
        {"mime_type": "video/mp4",
         "video_data": {"url": "https://lookaside.fbsbx.com/c.mp4"}}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["media_kind"] == "video"
    assert row["message"] == IMAGE_PENDING_PH


@pytest.mark.asyncio
async def test_a_caption_sent_with_the_photo_is_kept_not_replaced() -> None:
    """The placeholder only stands in for absent text — a lead who captions the photo asked a
    question, and overwriting it with '🖼 media' would delete the question."""
    client = _Client([_conv([_msg("ini brosurnya kak, harganya berapa?", attachments=[
        {"mime_type": "image/jpeg", "image_data": {"url": "https://cdn/x.jpg"}}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["message"] == "ini brosurnya kak, harganya berapa?"
    assert row["media_url"] == "https://cdn/x.jpg"  # still backfilled


@pytest.mark.asyncio
async def test_a_plain_text_message_carries_no_media() -> None:
    client = _Client([_conv([_msg("halo kak")])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["media_url"] is None and row["media_kind"] is None


@pytest.mark.asyncio
async def test_attachments_and_the_message_id_are_actually_requested() -> None:
    """Graph returns neither unless asked. This is the whole bug for both findings — the
    mapping below can be perfect and still see nothing."""
    client = _Client([_conv([_msg("hi")])])
    await _transport(client).fetch_conversations()
    assert all("attachments" in f for f in client.fields)
    assert all("{id," in f for f in client.fields)


# ------------------------------------------------------- media must never cost the channel

@pytest.mark.asyncio
async def test_graph_refusing_the_media_fields_costs_the_media_not_the_channel() -> None:
    """`attachments` went into the field string BOTH platform calls share, and Graph 400s the
    whole request over one subfield it dislikes. fetch_conversations logs that and moves on —
    so an unpermitted field would silently take branch 7 from 'photos ingest blank' to
    'nothing ingests at all'. The poll must fall back to the field set that ran before."""
    client = _FieldPickyClient([_conv([_msg("halo kak", mid="m5")])])
    rows = await _transport(client).fetch_conversations()

    assert [r["message"] for r in rows] == ["halo kak"]  # the channel still delivers
    assert rows[0]["media_url"] is None                  # media is what we lost
    assert client.attempted[0].count("attachments") == 1  # asked first
    assert "attachments" not in client.attempted[-1]      # then degraded


@pytest.mark.asyncio
async def test_a_non_400_from_graph_is_not_treated_as_a_bad_field() -> None:
    """A 500 or an auth failure must keep bubbling to fetch_conversations' own handler —
    silently dropping the media fields on every error would hide a real outage as data loss."""
    import httpx

    class _Broken(_Client):
        async def get(self, url: str, params: dict) -> _Resp:
            if params.get("fields") == "instagram_business_account":
                return await super().get(url, params)
            req = httpx.Request("GET", "https://graph.facebook.com/v21.0/x")
            raise httpx.HTTPStatusError(
                "boom", request=req, response=httpx.Response(500, request=req))

    transport = _transport(_Broken([_conv([_msg("hi")])]))
    assert await transport.fetch_conversations() == []
    assert "attachments" in transport._msg_fields


@pytest.mark.asyncio
async def test_the_webhook_shape_of_attachments_does_not_take_the_channel_down() -> None:
    """Messenger sends attachments as a BARE LIST, not {"data": [...]}. `.get("data")` on it
    raised AttributeError, which is not an httpx error — it escapes the poll's handler and
    fetch_inbound (the worker catches only RuntimeError there) and dies at the worker's
    blanket except, so the channel returns zero messages for the tick. Every tick."""
    m = _msg("lihat ini")
    m["attachments"] = [{"type": "image", "payload": {"url": "https://cdn/a.jpg"}}]
    (row,) = await _transport(_Client([_conv([m])])).fetch_conversations()
    assert row["message"] == "lihat ini"


@pytest.mark.parametrize("attachments", [
    [{"type": "image", "payload": {"url": "https://cdn/a.jpg"}}],  # webhook shape
    "nonsense", 7, {"data": "nope"}, {"data": [None, 3]}, None])
def test_media_of_never_raises_on_a_container_shape_it_does_not_know(attachments) -> None:
    """The parser itself, not the guard above it. Returning None here is the contract: whoever
    calls it gets 'no media', never an exception it has no reason to expect."""
    from app.adapters.channels import graph_parse

    assert graph_parse.media_of({"id": "m1", "attachments": attachments}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("attachments", ["nonsense", 7, {"data": "nope"}, {"data": [None, 3]}])
async def test_a_junk_attachments_payload_still_delivers_the_message(attachments) -> None:
    m = _msg("halo")
    m["attachments"] = attachments
    (row,) = await _transport(_Client([_conv([m])])).fetch_conversations()
    assert row["message"] == "halo" and row["media_kind"] is None


@pytest.mark.asyncio
async def test_a_pdf_is_not_dressed_up_as_an_image() -> None:
    """Calling an unknown attachment an image sent a PDF to the VISION describer and gave it
    the '🖼 media' placeholder — and that placeholder holds the reply for the whole 6h media
    window. Branch 7 is a Page inbox, where leads attach documents routinely. No kind we can
    establish means no media: the message goes through and the bot answers."""
    client = _Client([_conv([_msg("ini dokumennya", attachments=[
        {"mime_type": "application/pdf",
         "file_url": "https://lookaside.fbsbx.com/brosur.pdf"}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["media_kind"] is None and row["media_url"] is None
    assert row["message"] == "ini dokumennya"


@pytest.mark.asyncio
async def test_a_file_url_with_no_mime_is_still_read_from_its_extension() -> None:
    """Dropping the guess must not drop real photos: the url still says what it is."""
    client = _Client([_conv([_msg(attachments=[
        {"file_url": "https://lookaside.fbsbx.com/p.jpg?ccb=1&oh=00_AT"}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["media_kind"] == "image"
    assert row["message"] == IMAGE_PENDING_PH


@pytest.mark.asyncio
async def test_a_document_next_to_a_photo_does_not_hide_the_photo() -> None:
    """An attachment we cannot classify is skipped, not returned as the answer."""
    client = _Client([_conv([_msg(attachments=[
        {"mime_type": "application/pdf", "file_url": "https://cdn/a.pdf"},
        {"mime_type": "image/jpeg", "image_data": {"url": "https://cdn/b.jpg"}}])])])
    (row,) = await _transport(client).fetch_conversations()
    assert row["media_kind"] == "image" and row["media_url"] == "https://cdn/b.jpg"


# ------------------------------------------------------------- native mid (finding c)

@pytest.mark.asyncio
async def test_the_native_mid_reaches_the_inbound_message() -> None:
    """external_id is the dedup key. Carrying Graph's own id is what makes a webhook delivery
    and a poll of the same message resolve to ONE row (step S8 rests on this)."""
    client = _Client([_conv([_msg("hi", mid="aWdfZGl0ZW1PMTIz")])])
    rows = await _transport(client).fetch_conversations()
    (inbound,) = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()
    assert inbound.external_id == "aWdfZGl0ZW1PMTIz"


@pytest.mark.asyncio
async def test_a_message_without_an_id_falls_back_to_the_synthetic_one() -> None:
    """Graph omitting the id must not produce external_id="" — that would collide every such
    message onto one row. None hands the decision back to ingest's synthetic id."""
    rows = [{"thread_id": "t1", "from_id": _LEAD, "message": "hi",
             "created_time": _WHEN, "mid": ""}]
    (inbound,) = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()
    assert inbound.external_id is None


@pytest.mark.asyncio
async def test_the_same_message_seen_by_both_platform_calls_is_returned_once() -> None:
    """/conversations is polled twice (Messenger, then platform=instagram). A message counted
    twice would be answered twice; the mid is the identity that collapses it."""
    client = _Client([_conv([_msg("halo", mid="mid-42")])], both_platforms=True)
    out = await _transport(client).fetch_conversations()
    assert [m["mid"] for m in out] == ["mid-42"]


@pytest.mark.asyncio
async def test_two_lines_in_the_same_second_still_both_survive() -> None:
    """The second-resolution loss this dedup must never re-introduce — now guarded by distinct
    mids rather than by the text."""
    client = _Client([_conv([_msg("and the price?", mid="m2"), _msg("hi", mid="m1")])])
    out = await _transport(client).fetch_conversations()
    assert [m["message"] for m in out] == ["hi", "and the price?"]


# ------------------------------------------------------------------ direction (finding a)

@pytest.mark.asyncio
async def test_direction_comes_from_the_payload_not_from_a_default() -> None:
    """The adapter half of the contract. Note what this does NOT claim: the poll filters our
    own ids out before a row is built, so on the poll path direction can only read "in" today.
    The value is derived rather than defaulted so the webhook path (S8), which does deliver
    echoes of our own sends, gets the truth out of the same builder."""
    rows = [{"thread_id": "t1", "from_id": _OWN_IG, "message": "our reply",
             "created_time": _WHEN, "mid": "m9", "direction": "out"}]
    (inbound,) = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()
    assert inbound.direction == "out"


@pytest.mark.asyncio
async def test_the_poll_states_direction_in_on_every_lead_message() -> None:
    client = _Client([_conv([_msg("halo")])])
    out = await _transport(client).fetch_conversations()
    assert [m["direction"] for m in out] == ["in"]


def test_direction_is_computed_from_who_sent_it_not_hardcoded() -> None:
    """The row builder decides by comparing the sender against our own ids — the same test the
    instagrapi transport makes. Hardcoding "in" here would look identical on the poll path (our
    ids are filtered out first) and be a lie the moment a webhook delivers our own echo."""
    from app.adapters.channels import graph_parse

    ours = graph_parse.row_of({"id": "t1"}, {"id": "m1", "from": {"id": _OWN_IG}}, {}, {_OWN_IG})
    theirs = graph_parse.row_of({"id": "t1"}, {"id": "m2", "from": {"id": _LEAD}}, {}, {_OWN_IG})
    assert (ours["direction"], theirs["direction"]) == ("out", "in")


# -------------------------------------------------------------------------- into the DB

async def _world(s) -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.META_BUSINESS)
    s.add(ch)
    await s.flush()
    return b.id, ch.id


async def test_a_graph_photo_becomes_a_stub_the_backfill_can_fetch(db_session) -> None:
    """End to end for finding (b): transport → adapter → Message + MediaAsset, exactly the
    shape the instagrapi path produces, so backfill_media handles both identically."""
    bid, cid = await _world(db_session)
    client = _Client([_conv([_msg(attachments=[
        {"mime_type": "image/jpeg", "image_data": {"url": "https://cdn/photo.jpg"}}])])])
    rows = await _transport(client).fetch_conversations()
    inbound = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()

    (created,) = await IngestService(db_session, bid).ingest(cid, inbound)
    assert created.text == IMAGE_PENDING_PH
    assert created.media_pending is True
    asset = (await db_session.exec(select(MediaAsset))).first()
    assert asset is not None and asset.url == "https://cdn/photo.jpg"
    assert asset.kind == "image" and asset.data is None


async def test_the_row_is_stored_under_the_native_mid(db_session) -> None:
    bid, cid = await _world(db_session)
    rows = [{"thread_id": "t1", "from_id": _LEAD, "message": "halo",
             "created_time": _WHEN, "mid": "mid-77", "direction": "in"}]
    inbound = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()
    (created,) = await IngestService(db_session, bid).ingest(cid, inbound)
    assert created.external_id == "mid-77"


async def test_a_repoll_of_the_same_mid_does_not_duplicate_the_row(db_session) -> None:
    bid, cid = await _world(db_session)
    rows = [{"thread_id": "t1", "from_id": _LEAD, "message": "halo",
             "created_time": _WHEN, "mid": "mid-77", "direction": "in"}]
    inbound = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()
    assert len(await IngestService(db_session, bid).ingest(cid, inbound)) == 1
    assert await IngestService(db_session, bid).ingest(cid, inbound) == []
    assert len((await db_session.exec(select(Message))).all()) == 1


async def test_a_row_stored_before_mids_is_not_duplicated_once_the_mid_arrives(
        db_session) -> None:
    """The legacy-id lookup has to survive this change. A media message is the sharp case: it
    is excluded from content dedup (two different photos share '🖼 media'), so only the id
    lookups stand between the row branch 7 already has and a duplicate on the first poll after
    deploy — and a duplicate re-enters _store as a fresh inbound, resetting the follow-up cycle
    and reviving the bot on a thread nobody wrote to.

    The pre-change row is built here the way PRODUCTION wrote it, not the way this test used to
    reconstruct it: the old poll never asked for attachments, so a photo's text is EMPTY (not
    the placeholder — no pre-change code could produce that), and production's external_id is
    the bare thread:time:sender shape. Reconstructing the placeholder made this pass for the
    wrong reason: it hashed the same text on both sides, which the real deploy never does.

    Not covered, deliberately: a row written by the connector branch itself, whose id carries
    a :sha256("") suffix that no lookup here reproduces. Nothing has ever deployed that shape
    to branch 7, and it stays that way only if the connector stack lands as one deploy."""
    bid, cid = await _world(db_session)
    when = datetime(2026, 8, 3, 10, 0, 1)
    lead = Lead(branch_id=bid, stage=Stage.QUALIFYING)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=cid, external_thread_id="t1")
    db_session.add(thread)
    await db_session.flush()
    db_session.add(Message(
        branch_id=bid, thread_id=thread.id, channel_id=cid,
        external_id=f"t1:{when.isoformat()}:{_LEAD}",
        direction="in", sent_by="lead", text="", occurred_at=when))
    await db_session.flush()

    after = InboundMessage(
        external_thread_id="t1", sender_id=_LEAD, text=IMAGE_PENDING_PH,
        occurred_at=when, media_url="https://cdn/photo.jpg", media_kind="image",
        external_id="mid-88")
    assert await IngestService(db_session, bid).ingest(cid, [after]) == []
    assert len((await db_session.exec(select(Message))).all()) == 1


async def test_a_graph_message_of_ours_is_recorded_as_out_not_as_the_lead(db_session) -> None:
    """Finding (a)'s consequence: with direction carried, an outbound that does reach ingest
    is filed as ours and moves last_out_at, instead of reading as the lead speaking."""
    bid, cid = await _world(db_session)
    lead = Lead(branch_id=bid, stage=Stage.QUALIFYING)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=cid, external_thread_id="t1")
    db_session.add(thread)
    await db_session.flush()
    rows = [{"thread_id": "t1", "from_id": _OWN_IG, "message": "sudah kami kirim ya kak",
             "created_time": _WHEN, "mid": "mid-out-1", "direction": "out"}]
    inbound = await MetaBusinessAdapter(_FakeGraph(rows), account_id=_PAGE).fetch_inbound()
    (created,) = await IngestService(db_session, bid).ingest(cid, inbound)
    assert created.direction == "out" and created.sent_by == "manager"
    assert thread.last_out_at is not None


# ----------------------------------------------------------------- branch 1 stays untouched

def test_the_instagram_path_never_learned_about_graph() -> None:
    """Branch 1 (Indonesia, 37k messages) polls instagrapi; its only active channel is
    kind='instagram'. The Graph mapping must stay entirely on the Graph side — if it ever
    starts feeding the IG modules, this change has reached production behaviour it was never
    meant to touch."""
    from app.adapters.channels import ig_parse, instagram

    for module in (instagram, ig_parse):
        assert "graph_parse" not in Path(module.__file__).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_shared_downloader_still_refuses_redirects_by_default(monkeypatch) -> None:
    """The bounded media downloader is now shared with the Graph transport, which follows
    lookaside redirects. The IG call site branch 1 runs passes no flag, and must keep the exact
    httpx behaviour it had — following a redirect there would store whatever the redirect
    landed on as if it were the lead's photo."""
    import httpx

    seen: dict = {}

    class _Stream:
        async def __aenter__(self) -> _Stream:
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):  # noqa: ANN202
            yield b"jpeg"

    class _FakeAsyncClient:
        def __init__(self, **kwargs) -> None:
            seen.update(kwargs)

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        def stream(self, _method: str, _url: str) -> _Stream:
            return _Stream()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    from app.adapters.channels.transports import InstagrapiTransport

    ig = InstagrapiTransport(username="u", session_settings={})
    assert await ig.download_media("https://cdn/x.jpg") == b"jpeg"
    assert seen["follow_redirects"] is False

    assert await download_bounded("https://lookaside/x.jpg", follow_redirects=True) == b"jpeg"
    assert seen["follow_redirects"] is True


@pytest.mark.asyncio
async def test_the_graph_transport_can_now_download_media() -> None:
    """Without this the media backfill cannot even build a port for a meta_business channel
    (it probes for the method), so every photo/voice would sit pending forever — and a pending
    placeholder holds the reply, turning finding (b)'s fix into silence on branch 7."""
    adapter = MetaBusinessAdapter(_FakeGraph([]), account_id=_PAGE)
    assert hasattr(adapter, "download_media")
    assert await adapter.download_media("https://cdn/x.jpg") == b"bytes"


@pytest.mark.asyncio
async def test_a_media_download_is_still_size_capped(monkeypatch) -> None:
    """The 60 MB ceiling survived the extraction — it is what stops one big DM video from
    OOM-ing the worker for every branch at once."""
    import httpx

    class _Stream:
        async def __aenter__(self) -> _Stream:
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):  # noqa: ANN202
            for _ in range(4):
                yield b"x" * (20 * 1024 * 1024)

    class _FakeAsyncClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        def stream(self, _method: str, _url: str) -> _Stream:
            return _Stream()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(ValueError, match="refusing to buffer"):
        await download_bounded("https://cdn/big.mp4")


def test_the_graph_poll_window_is_unchanged() -> None:
    """Nothing here widens how much history a poll pulls — that would change the volume
    branch 7 ingests, and the constant is shared with the IG thread walk."""
    from app.adapters.channels import transports

    assert transports._MSGS_PER_THREAD == 25
