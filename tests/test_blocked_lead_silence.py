"""A blocked lead produces nothing: no alert row, no Telegram ping, ever.

Blocking is the owner saying this thread is spam or abuse and is closed. Every notification
after that asks a human to look again at something they already judged.

The rule existed, in exactly one caller — delivery.raise_manager_alert — while six other call
sites reached AlertService directly and skipped it. The loudest of those is `bot_off_message`,
which fires on EVERY inbound from a lead the bot is silent for: block someone who keeps
messaging and Telegram keeps buzzing. So the check moved into the service every alert goes
through, and the duplicate in the caller is gone.
"""
from __future__ import annotations

import pytest
from sqlmodel import select

from app.adapters.db.models import Branch, Lead, ManagerAlert
from app.domain.enums import Stage
from app.modules.notifications.alerts import AlertService


class _Notifier:
    def __init__(self) -> None:
        self.sends: list[str] = []

    async def create_topic(self, *, name: str, icon_emoji=None) -> int:  # noqa: ANN001, ARG002
        return 1

    async def send(self, *, text: str, topic_id=None) -> str:  # noqa: ANN001, ARG002
        self.sends.append(text)
        return "ok"


async def _lead(s, *, blocked: bool) -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, is_blocked=blocked,
                phone_e164="+628123456789")
    s.add(lead)
    await s.flush()
    return b.id, lead.id


@pytest.mark.parametrize("kind", [
    "bot_off_message",   # fires on every inbound — the one that actually buzzes
    "needs_manager",
    "ready_deal",
    "non_target",
    "unmapped_ad",
])
async def test_a_blocked_lead_raises_nothing_whatever_the_kind(db_session, kind) -> None:
    bid, lead_id = await _lead(db_session, blocked=True)
    notifier = _Notifier()
    out = await AlertService(db_session, bid, notifier).raise_alert(
        lead_id=lead_id, kind=kind, summary_en="x", summary_ru="x")
    assert out is None
    assert notifier.sends == []
    assert (await db_session.exec(select(ManagerAlert))).first() is None


async def test_an_unblocked_lead_still_alerts(db_session) -> None:
    bid, lead_id = await _lead(db_session, blocked=False)
    notifier = _Notifier()
    out = await AlertService(db_session, bid, notifier).raise_alert(
        lead_id=lead_id, kind="needs_manager", summary_en="x", summary_ru="x")
    assert out is not None
    assert len(notifier.sends) == 1


def test_the_re_ping_sweep_skips_a_lead_blocked_afterwards() -> None:
    """The alert row is history; the re-ping is a fresh demand on someone's attention, so the
    sweep filters on the CURRENT block flag rather than the state when the alert fired."""
    import inspect

    from app.modules.notifications import escalation

    assert "l.is_blocked = false" in inspect.getsource(escalation)
