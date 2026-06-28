"""FollowupRouter — pure channel-selection policy. No I/O: callers pass the data in.

Window-open beats everything (cheapest, the lead is live). When every window is
closed, fall back to a private channel that can bypass the window — WhatsApp first,
then Instagram — by Channel.kind. Trivially testable because nothing is fetched here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.adapters.db.models import ChannelThread
from app.domain.enums import ChannelKind

# Private channels that can re-open a conversation, in fallback priority order.
_FALLBACK_ORDER: tuple[ChannelKind, ...] = (ChannelKind.WHATSAPP, ChannelKind.INSTAGRAM)


@dataclass(frozen=True)
class RoutableThread:
    """A thread paired with its channel kind — the only inputs the policy needs."""
    thread: ChannelThread
    kind: ChannelKind


class FollowupRouter:
    """Stateless follow-up policy: choose where to reach a lead next."""

    @staticmethod
    def choose_channel(
        threads: list[RoutableThread], now: datetime
    ) -> ChannelThread | None:
        """Open-window thread (most recently active) else WhatsApp/Instagram; else None."""
        open_now = [
            r for r in threads
            if r.thread.window_until is not None and r.thread.window_until > now
        ]
        if open_now:
            return max(open_now, key=_last_active).thread

        for kind in _FALLBACK_ORDER:
            candidates = [r for r in threads if r.kind is kind]
            if candidates:
                return max(candidates, key=_last_active).thread
        return None


def _last_active(r: RoutableThread) -> datetime:
    return r.thread.last_in_at or r.thread.created_at
