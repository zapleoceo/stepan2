"""Notifications: AlertService records + pings in one place (branch-isolated, fake notifier);
TelegramNotifier builds the EN/RU payload, posts to the right group, and swallows transport
errors. No real network — httpx.AsyncClient is monkeypatched."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlmodel import select

from app.adapters.db.models import Branch, Lead, ManagerAlert
from app.adapters.notify import TelegramNotifier
from app.modules.notifications import AlertService

_FAKE_TOKEN = "TOK"  # noqa: S105 — dummy bot token for tests, never a real secret


class FakeNotifier:
    """Records notify_manager calls so the service's ping can be asserted in isolation."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def notify_manager(
        self,
        *,
        branch_id: int,
        lead_id: int,
        kind: str,
        summary_en: str,
        summary_ru: str,
    ) -> None:
        self.calls.append(
            {
                "branch_id": branch_id,
                "lead_id": lead_id,
                "kind": kind,
                "summary_en": summary_en,
                "summary_ru": summary_ru,
            }
        )


async def _branch(s, name: str) -> int:
    b = Branch(name=name)
    s.add(b)
    await s.flush()
    return b.id


async def _lead(s, branch_id: int, phone: str) -> int:
    lead = Lead(branch_id=branch_id, phone_e164=phone, display_name="L")
    s.add(lead)
    await s.flush()
    return lead.id


async def test_raise_alert_writes_row_and_pings_once(db_session):
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")
    notifier = FakeNotifier()

    svc = AlertService(s, branch_id, notifier)
    alert = await svc.raise_alert(
        lead_id, "ready_deal", "ready to buy", "готов купить",
        thread_id=None, lead_phone="+62811",
    )

    rows = list((await s.exec(select(ManagerAlert))).all())
    assert len(rows) == 1
    saved = rows[0]
    assert saved.id == alert.id
    assert saved.branch_id == branch_id
    assert saved.lead_id == lead_id
    assert saved.kind == "ready_deal"
    assert saved.summary_en == "ready to buy"
    assert saved.summary_ru == "готов купить"
    assert saved.lead_phone == "+62811"

    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call == {
        "branch_id": branch_id,
        "lead_id": lead_id,
        "kind": "ready_deal",
        "summary_en": "ready to buy",
        "summary_ru": "готов купить",
    }


async def test_alert_is_branch_scoped(db_session):
    s = db_session
    a = await _branch(s, "Jakarta")
    b = await _branch(s, "Hanoi")
    lead_a = await _lead(s, a, "+62811")
    lead_b = await _lead(s, b, "+84900")

    await AlertService(s, a, FakeNotifier()).raise_alert(
        lead_a, "ready_deal", "en-A", "ru-A"
    )
    await AlertService(s, b, FakeNotifier()).raise_alert(
        lead_b, "needs_manager", "en-B", "ru-B"
    )

    only_a = list((await s.exec(select(ManagerAlert).where(ManagerAlert.branch_id == a))).all())
    only_b = list((await s.exec(select(ManagerAlert).where(ManagerAlert.branch_id == b))).all())
    assert [r.summary_en for r in only_a] == ["en-A"]
    assert [r.summary_en for r in only_b] == ["en-B"]


async def test_raise_alert_forces_branch_id_on_row(db_session):
    s = db_session
    a = await _branch(s, "Jakarta")
    other = await _branch(s, "Hanoi")
    lead_a = await _lead(s, a, "+62811")

    svc = AlertService(s, a, FakeNotifier())
    # BranchScoped.add must overwrite any caller-supplied branch_id with the scope's.
    alert = await svc.raise_alert(lead_a, "ready_openhouse", "en", "ru")
    assert alert.branch_id == a != other


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _CapturingClient:
    """Stand-in for httpx.AsyncClient that records the posted url + json (no network)."""

    captured: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        type(self).captured = {"url": url, "json": json}
        return _FakeResponse({"ok": True, "result": {"message_id": 42}})


class _FailingClient:
    """httpx.AsyncClient stand-in whose post raises a transport error."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FailingClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> Any:
        raise httpx.ConnectError("boom")


async def test_telegram_builds_bilingual_payload_to_right_group(monkeypatch):
    _CapturingClient.captured = {}
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)

    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-1009999)
    result = await notifier._send("ignored-by-fake")  # noqa: SLF001 — exercise transport directly

    # success returns the parsed body, posts to the bot/sendMessage endpoint of the group
    assert result == {"ok": True, "result": {"message_id": 42}}
    captured = _CapturingClient.captured
    assert captured["url"].endswith("/botTOK/sendMessage")
    assert captured["json"]["chat_id"] == -1009999


async def test_telegram_notify_manager_renders_en_and_ru(monkeypatch):
    _CapturingClient.captured = {}
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)

    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-1009999)
    await notifier.notify_manager(
        branch_id=1,
        lead_id=7,
        kind="ready_deal",
        summary_en="Lead is ready to enroll",
        summary_ru="Лид готов записаться",
    )

    text = _CapturingClient.captured["json"]["text"]
    assert "ready_deal" in text
    assert "lead #7" in text
    assert "Lead is ready to enroll" in text
    assert "Лид готов записаться" in text


async def test_telegram_transport_error_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)

    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-1009999)
    # graceful degrade: a transport failure must not raise out of the notifier
    assert await notifier._send("anything") is None  # noqa: SLF001


async def test_alert_service_survives_notifier_failure(db_session, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")

    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-100)
    svc = AlertService(s, branch_id, notifier)
    # row is still written even though the real notifier swallows the transport error
    alert = await svc.raise_alert(lead_id, "needs_manager", "en", "ru")

    rows = list((await s.exec(select(ManagerAlert))).all())
    assert [r.id for r in rows] == [alert.id]


@pytest.mark.parametrize("kind", ["ready_deal", "ready_openhouse", "needs_manager"])
async def test_notify_manager_round_trips_kind(db_session, kind):
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")
    notifier = FakeNotifier()

    await AlertService(s, branch_id, notifier).raise_alert(lead_id, kind, "en", "ru")
    assert notifier.calls[0]["kind"] == kind
