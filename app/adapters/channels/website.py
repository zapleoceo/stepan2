"""Website chat adapter — the visitor talks to us over one synchronous HTTP request.

There is no API behind this. Inbound is not polled: the browser POSTs the message and the
same request carries the answer back. Outbound does not exist at all — the moment the
response is written the visitor is anonymous again, with no address of any kind. That is the
whole reason the site is its own branch (S6): everything the DM connectors do BETWEEN
conversations has no meaning here.

The two refusals below are the honest shape of that, not stubs waiting to be filled in.
"""
from __future__ import annotations

from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import InboundMessage, SendResult

# Written into outbox.error if anything ever queues a line for a website thread. Says what is
# wrong rather than looking like a transport blip an operator would retry.
NO_SEND_API = "website chat has no outbound channel — the visitor is anonymous"


class WebsiteAdapter:
    """ChannelPort for the public site chat: readable in-request, never writable."""

    kind: ChannelKind = ChannelKind.WEBSITE

    async def fetch_inbound(self) -> list[InboundMessage]:
        """Always empty — there is nothing to poll.

        Not an oversight and not "not implemented yet": the message arrived inside the HTTP
        request that produced the answer, and no copy of it is kept. The ingest cron walks
        every active channel, so this has to answer, and the truthful answer is nothing."""
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        """Always refuses. A visitor who closed the tab has no address to send to."""
        return SendResult(ok=False, error=NO_SEND_API)

    async def session_status(self) -> SessionStatus:
        """No credentials, no session to expire — the page is up as long as the app is."""
        return SessionStatus.ACTIVE
