"""Poll cadence is a property of the connector, not one schedule for everyone.

The 2-minute ingest cadence exists to protect the private Instagram API: one poll is several
private calls each carrying a deliberate 2-5s delay, a cycle runs ~50s, and a per-minute
schedule both risked overlapping itself and hammered the account. The official Graph API has
none of those properties — it is one authenticated request against a published rate limit.

Leaving both on the same schedule cost the official connector real latency: measured on the
demo branch, a lead's message at 03:09:39 was answered at 03:11:38. Two minutes, of which the
poll gap was the larger half.

Until webhooks are live (blocked on App Review) this is the half we can take back.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.connectors.registry import all_specs, spec_for  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402


def test_the_official_connector_may_poll_every_minute() -> None:
    spec = spec_for(ChannelKind.META_BUSINESS)
    assert spec is not None
    assert spec.polls_every_minute


@pytest.mark.parametrize("kind", [ChannelKind.INSTAGRAM, ChannelKind.WHATSAPP])
def test_private_connectors_keep_their_own_cadence(kind: ChannelKind) -> None:
    """instagrapi and Evolution both ride unofficial APIs where poll frequency is a ban
    vector. Branch 1 — live production, 37k messages — runs on the first of these."""
    spec = spec_for(kind)
    assert spec is not None
    assert not spec.polls_every_minute


def test_a_connector_that_is_never_polled_does_not_claim_a_cadence() -> None:
    """The website connector is answered synchronously inside one HTTP request; there is no
    inbox to poll, so claiming a poll cadence would be meaningless."""
    spec = spec_for(ChannelKind.WEBSITE)
    assert spec is not None
    assert not spec.polls_every_minute


def test_only_connectors_whose_poll_is_cheap_opt_into_per_minute() -> None:
    """A connector added later gets the conservative cadence unless it says otherwise —
    the wrong default here is an account ban, not a slow reply.

    The criterion is what a poll COSTS, not whose API it is. Meta Business qualifies because
    one authenticated Graph request against a published rate limit cannot get a Page banned.
    CRM WhatsApp qualifies for a stronger reason: its fetch_inbound touches no network at all,
    it drains a table the callback already filled, so frequency buys latency for free. The
    private connectors — instagrapi, Evolution — stay slow because there poll frequency IS
    the ban vector."""
    fast = {s.kind for s in all_specs() if s.polls_every_minute}
    assert fast == {ChannelKind.META_BUSINESS, ChannelKind.CRM_SENDER}, (
        "per-minute polling is for connectors whose poll costs nothing to the platform; "
        f"got {sorted(k.value for k in fast)}"
    )
