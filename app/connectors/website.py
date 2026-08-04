"""Website connector — the live chat on the public landing page.

What it HAS: an inbound message, synchronously, inside one HTTP request.
What it does NOT have: a send API, a poll, media, read receipts, a reply window, and any way
at all to reach the visitor once the response has been written. Everything below is that
sentence expressed as data — see ConnectorSpec.proactive_outreach.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.website import WebsiteAdapter
from app.adapters.db.models import Channel
from app.domain.enums import ChannelKind
from app.ports.channel import ChannelPort

from .spec import ConnectorSpec
from .website_ui import _ch_web_form


async def build_port(session: AsyncSession, channel: Channel) -> ChannelPort:  # noqa: ARG001
    """No secrets to load: the transport is the HTTP request the visitor is already making."""
    return WebsiteAdapter()


SPEC = ConnectorSpec(
    kind=ChannelKind.WEBSITE,
    label="Website",
    label_key="ch.kind_web",
    icon_class="fa-solid fa-globe",
    icon_color="#5b8def",
    adapter=WebsiteAdapter,
    build_port=build_port,
    credential_panel=_ch_web_form,
    # No capabilities. Not "none yet" — the private-API powers (revoke, read receipts, profile
    # fetch, media download, comments) are all things you do to an account you can reach, and
    # there is no account here.
    capabilities=frozenset(),
    proactive_outreach=False,
    # A window is a deadline on sending; with no send path there is nothing to be late for.
    send_window=None,
    # An unanswered website turn cannot exist: the answer goes back in the same request, and
    # nothing is stored. Counting these in the inbox's "awaiting reply" split would show the
    # operator a queue they can neither see nor drain.
    counts_as_awaiting=False,
    # The one channel row nobody adds by hand: this row IS the pointer to the branch the public
    # landing page sells from, so offering it in a branch's "add channel" form would let a
    # WRITE-role operator on any tenant repoint that page at their own persona and price list.
    operator_addable=False,
)
