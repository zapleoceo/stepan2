"""What the Meta webhook body actually contains, and what we are allowed to do with it.

Before this, POST /webhooks/meta/{branch} counted the entries and threw the payload away, so
every one of these facts was unverified: nothing read a mid, a referral or an attachment, and
nothing decided what to do with an echo.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.modules.meta.webhook_parse import WebhookMessage, parse_meta_messages


def _payload(*messaging: dict, page_id: str = "PAGE1") -> dict:
    return {"object": "page", "entry": [{"id": page_id, "messaging": list(messaging)}]}


def _item(**message: object) -> dict:
    return {
        "sender": {"id": "PSID1"},
        "recipient": {"id": "PAGE1"},
        "timestamp": 1_754_200_000_123,
        "message": {"mid": "m_aaa", **message},
    }


def test_a_plain_text_message_is_parsed_with_its_native_id() -> None:
    """The mid is the dedup key shared with the poll — losing it means duplicate messages."""
    [msg] = parse_meta_messages(_payload(_item(text="halo, berapa harganya?")))
    assert msg.mid == "m_aaa"
    assert msg.sender_id == "PSID1"
    assert msg.page_id == "PAGE1"
    assert msg.text == "halo, berapa harganya?"


def test_timestamp_is_truncated_to_whole_seconds() -> None:
    """Webhook timestamps are milliseconds, Graph's created_time is whole seconds. If the two
    paths disagree on occurred_at, IngestService's ±2s content dedup cannot recognise the
    polled copy of a message the webhook already stored."""
    [msg] = parse_meta_messages(_payload(_item(text="hi")))
    expected = datetime.fromtimestamp(1_754_200_000, tz=UTC).replace(tzinfo=None)
    assert msg.occurred_at == expected
    assert msg.occurred_at.microsecond == 0


def test_our_own_echo_is_never_ingested() -> None:
    """An echo is a message WE sent. OutboxSender already stored it under the send API's id,
    and the echo carries a different mid — ingesting it would show our reply twice in the
    thread and feed it back to the model as if the lead had said it."""
    assert parse_meta_messages(_payload(_item(text="terima kasih", is_echo=True))) == []


def test_delivery_and_read_receipts_produce_nothing() -> None:
    """Meta pushes these on the same subscription; they have no `message` at all. Anything we
    cannot ingest must yield zero rather than raise — a 500 puts Meta into a retry storm."""
    items = [
        {"sender": {"id": "PSID1"}, "delivery": {"mids": ["m_aaa"], "watermark": 1}},
        {"sender": {"id": "PSID1"}, "read": {"watermark": 1}},
        {"sender": {"id": "PSID1"}, "reaction": {"mid": "m_aaa", "action": "react"}},
    ]
    assert parse_meta_messages(_payload(*items)) == []


def test_a_message_with_no_mid_or_no_sender_is_skipped() -> None:
    """Both are load-bearing: the mid is the dedup key, the sender id is the only thing that
    can be turned into a thread."""
    no_mid = {"sender": {"id": "PSID1"}, "timestamp": 1, "message": {"text": "hi"}}
    no_sender = {"timestamp": 1, "message": {"mid": "m_bbb", "text": "hi"}}
    assert parse_meta_messages(_payload(no_mid, no_sender)) == []


def test_standby_events_are_ignored() -> None:
    """`standby` is the handover protocol: another app is the active receiver for that thread.
    Answering there would have two bots talking over each other in the lead's inbox."""
    payload = {"entry": [{"id": "PAGE1", "standby": [_item(text="hi")]}]}
    assert parse_meta_messages(payload) == []


def test_ad_referral_is_carried_through() -> None:
    """thread.lead_source / ad_id drive the ad-aware opener and the AdProductMap lookup. The
    poll never sees this metadata on the first message — the webhook is where it arrives."""
    item = _item(text="mau tanya", referral={
        "source": "ADS", "type": "OPEN_THREAD", "ad_id": "1200",
        "ads_context_data": {"post_id": "9900", "photo_url": "https://cdn/x.jpg"},
    })
    [msg] = parse_meta_messages(_payload(item))
    assert (msg.ad_id, msg.ad_media_id, msg.lead_source) == ("1200", "9900", "ad_clicktomsg")
    assert msg.ad_preview_url == "https://cdn/x.jpg"


def test_top_level_referral_is_read_too() -> None:
    """Meta puts the referral under `message` for a click-to-message ad and at the top level
    for an OPEN_THREAD referral; both mean the same thing to the funnel."""
    item = _item(text="hai")
    item["referral"] = {"source": "IG_STORY", "type": "OPEN_THREAD"}
    [msg] = parse_meta_messages(_payload(item))
    assert msg.lead_source == "story"


def test_an_image_becomes_pending_media_not_an_empty_message() -> None:
    """An attachment-only message has no text. Stored blank it would reach the model as
    silence; the placeholder is the same one the media backfill later replaces."""
    item = _item(attachments=[{"type": "image", "payload": {"url": "https://cdn/i.jpg"}}])
    [msg] = parse_meta_messages(_payload(item))
    assert (msg.media_url, msg.media_kind) == ("https://cdn/i.jpg", "image")
    assert msg.text == "🖼 media"


def test_the_text_is_stored_exactly_as_meta_sent_it() -> None:
    """Not stripped. The poll stores Graph's `message` verbatim, and until S3 carries the mid
    the only dedup between the two paths is an exact text compare — a trailing newline was
    enough to store the lead's message twice, in the thread and in the model's context."""
    [msg] = parse_meta_messages(_payload(_item(text="halo\n")))
    assert msg.text == "halo\n"


def test_a_whitespace_only_message_is_not_a_message() -> None:
    """Stripping is still the emptiness TEST — a body of spaces has nothing to answer, and
    with no attachment behind it there is nothing to store either."""
    assert parse_meta_messages(_payload(_item(text="   \n "))) == []


def test_a_message_without_a_usable_timestamp_is_left_to_the_poll() -> None:
    """as_naive_utc(None) is 1970-01-01. That row drags thread.last_in_at back with it, putting
    the thread permanently outside reply_pending's `last_in_at > cutoff` window — the lead is
    never answered and nothing says why. One poll cycle of latency is the cheaper failure."""
    for bad in (None, "not-a-number", 0, True):
        item = _item(text="halo")
        item["timestamp"] = bad
        assert parse_meta_messages(_payload(item)) == []


def test_a_document_is_not_filed_as_an_image() -> None:
    """type='file' is any document (pdf, docx, zip). Filed as 'image' it was handed to the
    image describer, which billed the broker for describing a PDF."""
    item = _item(attachments=[{"type": "file", "payload": {"url": "https://cdn/x.pdf"}}])
    [msg] = parse_meta_messages(_payload(item))
    assert msg.media_kind == "file"
    assert msg.text == "🖼 media"


def test_the_object_type_travels_with_every_message() -> None:
    """entry.id means the Page id for object='page' and the Instagram professional-account id
    for object='instagram'. The worker cannot look a channel up without knowing which."""
    [page] = parse_meta_messages(_payload(_item(text="hi")))
    ig = {"object": "instagram",
          "entry": [{"id": "IG_17841", "messaging": [_item(text="hi")]}]}
    [insta] = parse_meta_messages(ig)
    assert (page.object_type, page.page_id) == ("page", "PAGE1")
    assert (insta.object_type, insta.page_id) == ("instagram", "IG_17841")


def test_an_old_queued_job_without_object_type_still_decodes() -> None:
    """The API and the worker deploy separately, so a job enqueued by the previous image is
    decoded by the new one. A new required field there is a crashed worker, not a migration."""
    [msg] = parse_meta_messages(_payload(_item(text="hi")))
    raw = msg.as_dict()
    del raw["object_type"]
    assert WebhookMessage.from_dict(raw).object_type == ""


def test_a_share_is_a_link_not_a_downloadable_asset() -> None:
    """A story_mention/share payload url is not media; filing it as media would leave a
    MediaAsset the backfill worker can never complete."""
    item = _item(attachments=[{"type": "story_mention", "payload": {"url": "https://ig/s/1"}}])
    [msg] = parse_meta_messages(_payload(item))
    assert msg.media_url is None
    assert msg.link_url == "https://ig/s/1"


def test_malformed_payloads_are_survivable() -> None:
    """Anything can arrive on a public endpoint, and Meta adds event shapes without notice."""
    for junk in ({}, {"entry": "not-a-list"}, {"entry": [None, 7]},
                 {"entry": [{"id": "P", "messaging": "nope"}]},
                 {"entry": [{"id": "P", "messaging": [None, {"message": "not-a-dict"}]}]}):
        assert parse_meta_messages(junk) == []


def test_round_trip_through_the_queue_preserves_every_field() -> None:
    """The API and the worker are separate containers restarted separately, so the job payload
    travels as plain JSON — a field lost in that hop is a field silently dropped from ingest."""
    item = _item(text="hi", referral={"ad_id": "77"})
    [msg] = parse_meta_messages(_payload(item))
    assert WebhookMessage.from_dict(msg.as_dict()) == msg
