"""The ad prefill marks exactly ONE message — the first thing that arrives after the tap.

Meta's referral metadata (ad_id / ad_media_id / lead_source) is THREAD-level and comes back on
every later message. Taking it at face value marked everything the lead subsequently typed as
"not their words": 33 live threads on branch 1 were in that state, which silently disabled the
answer gate, told the critic to ignore real questions, and kept the dossier from recording them.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.db.models import Branch, Channel
from app.domain.enums import ChannelKind
from app.modules.leads.ingest import IngestService
from app.ports.channel import InboundMessage

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _channel(s) -> tuple[int, int]:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    return b.id, ch.id


def _msg(ext: str, text: str, at_min: int, *, ad: bool = True) -> InboundMessage:
    return InboundMessage(
        external_thread_id="ig-1", external_id=ext, text=text,
        occurred_at=_NOW + timedelta(minutes=at_min), sender_id="u1",
        ad_id="123" if ad else None,
        lead_source="ad_clicktomsg" if ad else None,
    )


async def test_only_the_first_message_after_the_tap_is_the_prefill(db_session) -> None:  # noqa: ANN001
    bid, cid = await _channel(db_session)
    svc = IngestService(db_session, bid)

    stored = await svc.ingest(cid, [
        _msg("m1", "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?", 0),
        _msg("m2", "berapa lama durasinya kak?", 1),
        _msg("m3", "tapi saya belum terlalu paham", 2),
    ])

    flags = [m.is_ad_referral for m in sorted(stored, key=lambda m: m.occurred_at)]
    assert flags == [True, False, False]


async def test_the_prefill_flag_survives_across_separate_polls(db_session) -> None:  # noqa: ANN001
    """The second message usually arrives on a later ingest tick, not in the same batch."""
    bid, cid = await _channel(db_session)
    svc = IngestService(db_session, bid)

    first = await svc.ingest(cid, [_msg("m1", "Halo! Tertarik kursus.", 0)])
    later = await svc.ingest(cid, [_msg("m2", "berapa harganya?", 5)])

    assert first[0].is_ad_referral is True
    assert later[0].is_ad_referral is False


async def test_a_thread_that_never_came_from_an_ad_flags_nothing(db_session) -> None:  # noqa: ANN001
    bid, cid = await _channel(db_session)
    svc = IngestService(db_session, bid)

    stored = await svc.ingest(cid, [_msg("m1", "halo kak", 0, ad=False)])
    assert stored[0].is_ad_referral is False


async def test_a_flagged_prefill_does_not_trip_the_answer_gate(db_session) -> None:  # noqa: ANN001
    """End to end: the tap opens with a question, what the lead types next is a real question."""
    from app.modules.conversation.reply import _typed_a_question

    bid, cid = await _channel(db_session)
    svc = IngestService(db_session, bid)
    stored = await svc.ingest(cid, [
        _msg("m1", "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?", 0),
        _msg("m2", "berapa lama durasinya kak?", 1),
    ])
    prefill, typed = sorted(stored, key=lambda m: m.occurred_at)

    assert not _typed_a_question(prefill)
    assert _typed_a_question(typed)
