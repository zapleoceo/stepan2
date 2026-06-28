"""Leads module — phone normalization, cross-channel merge, ingest dedup, routing.

The load-bearing rule: same phone in one branch = one lead, even across channels;
a follow-up via a second channel must NOT spawn a duplicate lead.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.db.models import Branch, Channel
from app.domain.enums import ChannelKind
from app.modules.leads import (
    FollowupRouter,
    IdentityService,
    IngestService,
    RoutableThread,
    normalize_phone,
)
from app.ports.channel import InboundMessage

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


async def _branch(s, name: str = "Jakarta") -> int:
    b = Branch(name=name, lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _channel(s, branch_id: int, kind: ChannelKind) -> int:
    c = Channel(branch_id=branch_id, kind=kind)
    s.add(c)
    await s.flush()
    return c.id


# --- phone -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+62 812-3456-7890", "6281234567890"),
        ("0812 3456 7890", "081234567890"),
        ("call me: +1 (415) 555-0199 ok?", "14155550199"),
        ("wa.me/6281234567890", "6281234567890"),
        ("12345", None),            # too short
        ("no digits here", None),
        ("", None),
    ],
)
def test_normalize_phone(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


# --- identity / no-duplicate rule ------------------------------------------

async def test_same_phone_two_channels_is_one_lead(db_session) -> None:
    s = db_session
    branch = await _branch(s)
    ig = await _channel(s, branch, ChannelKind.INSTAGRAM)
    wa = await _channel(s, branch, ChannelKind.WHATSAPP)
    svc = IdentityService(s, branch)

    lead_ig, thread_ig = await svc.resolve_or_create("ig_t1", ig, "Budi", "6281111")
    lead_wa, thread_wa = await svc.resolve_or_create("wa_t1", wa, "Budi", "6281111")

    assert lead_ig.id == lead_wa.id  # merged across channels by phone
    assert thread_ig.id != thread_wa.id  # but two distinct threads

    leads = await svc.leads.list()
    assert len(leads) == 1  # no duplicate lead created


async def test_existing_thread_resolves_without_phone(db_session) -> None:
    s = db_session
    branch = await _branch(s)
    ig = await _channel(s, branch, ChannelKind.INSTAGRAM)
    svc = IdentityService(s, branch)

    lead1, t1 = await svc.resolve_or_create("ig_t1", ig, "Budi", None)
    lead2, t2 = await svc.resolve_or_create("ig_t1", ig, "Budi", None)

    assert lead1.id == lead2.id
    assert t1.id == t2.id  # same thread upserted, not duplicated
    assert len(await svc.leads.list()) == 1


async def test_phone_isolated_across_branches(db_session) -> None:
    s = db_session
    branch_a = await _branch(s, "Jakarta")
    branch_b = await _branch(s, "Hanoi")
    ch_a = await _channel(s, branch_a, ChannelKind.WHATSAPP)
    ch_b = await _channel(s, branch_b, ChannelKind.WHATSAPP)

    lead_a, _ = await IdentityService(s, branch_a).resolve_or_create(
        "wa_t1", ch_a, "Budi", "6281111"
    )
    lead_b, _ = await IdentityService(s, branch_b).resolve_or_create(
        "wa_t9", ch_b, "Nguyen", "6281111"
    )

    assert lead_a.id != lead_b.id  # same number, different branches → different leads
    assert lead_a.branch_id == branch_a
    assert lead_b.branch_id == branch_b


# --- ingest ----------------------------------------------------------------

async def test_ingest_dedups_by_external_id(db_session) -> None:
    s = db_session
    branch = await _branch(s)
    ig = await _channel(s, branch, ChannelKind.INSTAGRAM)
    svc = IngestService(s, branch)

    msg = InboundMessage(
        external_thread_id="ig_t1",
        sender_id="42",
        text="halo",
        occurred_at=NOW,
        product_hint="vibe_coding",
    )

    first = await svc.ingest(ig, [msg, msg])  # same message twice in one batch
    second = await svc.ingest(ig, [msg])      # and again in a later batch

    assert len(first) == 1   # dedup within the batch
    assert len(second) == 0  # dedup across batches
    assert len(await svc.messages.list()) == 1


async def test_ingest_opens_window_and_sets_thread(db_session) -> None:
    s = db_session
    branch = await _branch(s)
    ig = await _channel(s, branch, ChannelKind.INSTAGRAM)
    svc = IngestService(s, branch)

    await svc.ingest(
        ig,
        [InboundMessage("ig_t1", "42", "halo", NOW, product_hint="vibe_coding")],
    )

    thread = await svc.identity.threads.by_external(ig, "ig_t1")
    assert thread is not None
    # SQLite returns tz-naive datetimes; compare on the same wall-clock instant.
    assert thread.last_in_at == NOW.replace(tzinfo=None)
    assert thread.window_until == (NOW + timedelta(hours=24)).replace(tzinfo=None)
    assert thread.product_slug == "vibe_coding"


# --- router ----------------------------------------------------------------

def _thread(window_until: datetime | None, last_in_at: datetime | None):
    from app.adapters.db.models import ChannelThread

    return ChannelThread(
        lead_id=1,
        channel_id=1,
        external_thread_id="t",
        window_until=window_until,
        last_in_at=last_in_at,
        created_at=NOW - timedelta(days=1),
    )


def test_router_prefers_open_window_most_recent() -> None:
    open_recent = _thread(NOW + timedelta(hours=1), NOW - timedelta(minutes=5))
    open_old = _thread(NOW + timedelta(hours=1), NOW - timedelta(hours=3))
    closed = _thread(NOW - timedelta(hours=1), NOW - timedelta(minutes=1))

    chosen = FollowupRouter.choose_channel(
        [
            RoutableThread(closed, ChannelKind.META_BUSINESS),
            RoutableThread(open_old, ChannelKind.INSTAGRAM),
            RoutableThread(open_recent, ChannelKind.WHATSAPP),
        ],
        NOW,
    )
    assert chosen is open_recent  # open window, most recently active


def test_router_falls_back_to_whatsapp_when_windows_closed() -> None:
    closed_mbs = _thread(NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    closed_ig = _thread(NOW - timedelta(hours=2), NOW - timedelta(minutes=2))
    closed_wa = _thread(NOW - timedelta(hours=3), NOW - timedelta(minutes=3))

    chosen = FollowupRouter.choose_channel(
        [
            RoutableThread(closed_mbs, ChannelKind.META_BUSINESS),
            RoutableThread(closed_ig, ChannelKind.INSTAGRAM),
            RoutableThread(closed_wa, ChannelKind.WHATSAPP),
        ],
        NOW,
    )
    assert chosen is closed_wa  # WhatsApp bypasses the window, beats Instagram


def test_router_falls_back_to_instagram_when_no_whatsapp() -> None:
    closed_mbs = _thread(NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    closed_ig = _thread(NOW - timedelta(hours=2), NOW - timedelta(minutes=2))

    chosen = FollowupRouter.choose_channel(
        [
            RoutableThread(closed_mbs, ChannelKind.META_BUSINESS),
            RoutableThread(closed_ig, ChannelKind.INSTAGRAM),
        ],
        NOW,
    )
    assert chosen is closed_ig  # no WhatsApp → Instagram is the private fallback


def test_router_returns_none_when_nothing_usable() -> None:
    closed_mbs = _thread(NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    assert (
        FollowupRouter.choose_channel(
            [RoutableThread(closed_mbs, ChannelKind.META_BUSINESS)], NOW
        )
        is None
    )
    assert FollowupRouter.choose_channel([], NOW) is None
