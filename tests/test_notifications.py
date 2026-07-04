"""Notifications: AlertService records the CRM row AND pings the lead's own Telegram forum
topic — branch-language chat summary + reason, then the same in Russian. TelegramNotifier
creates topics and sends into them; transport errors degrade to a status, never raise. No
real network — httpx.AsyncClient is monkeypatched."""
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
    """Records create_topic + send calls. `gone_once` makes the first send into a topic
    report 'topic_gone' so the recreate-and-resend path can be exercised."""

    def __init__(self, *, gone_once: bool = False) -> None:
        self.topics: list[str] = []
        self.icons: list[str | None] = []
        self.sends: list[dict[str, Any]] = []
        self._next_id = 100
        self._gone_once = gone_once

    async def create_topic(self, *, name: str, icon_emoji: str | None = None) -> int | None:
        self.topics.append(name)
        self.icons.append(icon_emoji)
        self._next_id += 1
        return self._next_id

    async def send(self, *, text: str, topic_id: int | None = None) -> str:
        self.sends.append({"text": text, "topic_id": topic_id})
        if self._gone_once and topic_id is not None:
            self._gone_once = False
            return "topic_gone"
        return "ok"


async def _branch(s, name: str, lang: str = "id") -> int:
    b = Branch(name=name, lang=lang)
    s.add(b)
    await s.flush()
    return b.id


async def _lead(s, branch_id: int, phone: str, name: str = "Budi") -> int:
    lead = Lead(branch_id=branch_id, phone_e164=phone, display_name=name)
    s.add(lead)
    await s.flush()
    return lead.id


async def test_raise_alert_writes_row_and_opens_lead_topic(db_session):
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811", name="Budi")
    notifier = FakeNotifier()

    alert = await AlertService(s, branch_id, notifier).raise_alert(
        lead_id, "ready_deal", "ready to buy", "готов купить",
        thread_id=1729, lead_phone="+62811",
    )

    row = (await s.exec(select(ManagerAlert))).one()
    assert row.id == alert.id and row.kind == "ready_deal"
    assert row.summary_en == "ready to buy" and row.summary_ru == "готов купить"
    # one topic opened (named after the lead), fire icon for a deal, one message sent
    assert notifier.topics == ["Budi"] and notifier.icons == ["🔥"]
    assert len(notifier.sends) == 1
    sent = notifier.sends[0]
    assert sent["topic_id"] == 101
    # header (chat # + name) + both reasons (no summary lines without an LLM)
    assert "чат #1729" in sent["text"] and "Budi" in sent["text"]
    assert "ready to buy" in sent["text"] and "готов купить" in sent["text"]
    # topic id is persisted on the lead for reuse
    lead = await s.get(Lead, lead_id)
    assert lead.notify_topic_id == 101


async def test_topic_reused_across_alerts(db_session):
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")
    notifier = FakeNotifier()
    svc = AlertService(s, branch_id, notifier)

    await svc.raise_alert(lead_id, "needs_manager", "q1", "в1")
    await svc.raise_alert(lead_id, "ready_deal", "q2", "в2")

    assert len(notifier.topics) == 1               # created once, reused
    assert [x["topic_id"] for x in notifier.sends] == [101, 101]


async def test_deleted_topic_is_recreated_and_message_resent(db_session):
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")
    notifier = FakeNotifier(gone_once=True)

    await AlertService(s, branch_id, notifier).raise_alert(lead_id, "ready_deal", "en", "ru")

    assert len(notifier.topics) == 2               # first topic gone → recreated
    assert len(notifier.sends) == 2                # resent after recreate
    lead = await s.get(Lead, lead_id)
    assert lead.notify_topic_id == 102             # points at the recreated topic


async def test_alert_is_branch_scoped(db_session):
    s = db_session
    a = await _branch(s, "Jakarta")
    b = await _branch(s, "Hanoi")
    lead_a = await _lead(s, a, "+62811")
    lead_b = await _lead(s, b, "+84900")

    await AlertService(s, a, FakeNotifier()).raise_alert(lead_a, "ready_deal", "en-A", "ru-A")
    await AlertService(s, b, FakeNotifier()).raise_alert(lead_b, "needs_manager", "en-B", "ru-B")

    only_a = list((await s.exec(select(ManagerAlert).where(ManagerAlert.branch_id == a))).all())
    only_b = list((await s.exec(select(ManagerAlert).where(ManagerAlert.branch_id == b))).all())
    assert [r.summary_en for r in only_a] == ["en-A"]
    assert [r.summary_en for r in only_b] == ["en-B"]


async def test_bilingual_body_orders_branch_then_ru(db_session):
    """Message body: branch-language summary + reason, a divider, then the Russian pair."""
    class _FakeLLM:
        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
            return (
                "[SUMMARY_BRANCH]\nRingkasan Bahasa\n"
                "[SUMMARY_RU]\nСводка по-русски\n"
                "[REASON_BRANCH]\nAlasan Bahasa\n"
            ), {"cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001, ANN201
            return [[0.0] for _ in texts]

    s = db_session
    branch_id = await _branch(s, "Jakarta", lang="id")
    lead_id = await _lead(s, branch_id, "+62811")
    from app.adapters.db.models import Channel, ChannelThread, Message
    from app.domain.enums import ChannelKind
    ch = Channel(branch_id=branch_id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    thread = ChannelThread(lead_id=lead_id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    s.add(Message(branch_id=branch_id, thread_id=thread.id, channel_id=ch.id, external_id="m1",
                  direction="in", sent_by="lead", text="halo"))
    await s.flush()

    notifier = FakeNotifier()
    await AlertService(s, branch_id, notifier, llm=_FakeLLM()).raise_alert(
        lead_id, "ready_openhouse", "Lead ready", "Лид готов", thread_id=thread.id,
    )
    body = notifier.sends[0]["text"]
    # branch-language block (summary + reason) comes before the Russian block
    assert body.index("Ringkasan Bahasa") < body.index("Alasan Bahasa") < body.index("➖")
    assert body.index("➖") < body.index("Сводка по-русски") < body.index("Лид готов")
    # open-house alerts get the calendar topic icon
    assert notifier.icons == ["📆"]


# ─── TelegramNotifier transport (no network) ───────────────────────────────────

class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _CapturingClient:
    """Records posted url + json; returns a canned ok body."""

    captured: dict[str, Any] = {}
    reply: dict[str, Any] = {"ok": True, "result": {"message_thread_id": 555, "message_id": 42}}

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _Resp:
        type(self).captured = {"url": url, "json": json}
        return _Resp(type(self).reply)


class _FailingClient:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _FailingClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> Any:
        raise httpx.ConnectError("boom")


async def test_telegram_create_topic_returns_thread_id(monkeypatch):
    _CapturingClient.reply = {"ok": True, "result": {"message_thread_id": 555}}
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)
    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-1009999)
    tid = await notifier.create_topic(name="Budi", icon_emoji="🔥")
    assert tid == 555
    assert _CapturingClient.captured["url"].endswith("/botTOK/createForumTopic")
    j = _CapturingClient.captured["json"]
    assert j["chat_id"] == -1009999 and j["name"] == "Budi"
    assert j["icon_custom_emoji_id"] == "5312241539987020022"  # 🔥 topic-icon sticker id


async def test_telegram_send_targets_topic_and_group(monkeypatch):
    _CapturingClient.reply = {"ok": True, "result": {"message_id": 1}}
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)
    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-1009999)

    assert await notifier.send(text="hi", topic_id=555) == "ok"
    j = _CapturingClient.captured["json"]
    assert j["chat_id"] == -1009999
    assert j["message_thread_id"] == 555 and j["parse_mode"] == "HTML"

    await notifier.send(text="hi")  # no topic → General, no message_thread_id key
    assert "message_thread_id" not in _CapturingClient.captured["json"]


async def test_telegram_send_reports_topic_gone(monkeypatch):
    _CapturingClient.reply = {"ok": False, "description": "Bad Request: message thread not found"}
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingClient)
    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-100)
    assert await notifier.send(text="x", topic_id=999) == "topic_gone"
    # same error with no topic is just a plain failure, not a recreate signal
    assert await notifier.send(text="x") == "failed"


async def test_telegram_transport_error_is_failed_not_raised(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)
    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-100)
    assert await notifier.send(text="anything") == "failed"


async def test_alert_service_survives_notifier_failure(db_session, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")
    notifier = TelegramNotifier(bot_token=_FAKE_TOKEN, group_chat_id=-100)
    svc = AlertService(s, branch_id, notifier)
    alert = await svc.raise_alert(lead_id, "needs_manager", "e", "r")
    rows = list((await s.exec(select(ManagerAlert))).all())
    assert [r.id for r in rows] == [alert.id]  # row written despite the swallowed transport error


@pytest.mark.parametrize("kind", ["ready_deal", "ready_openhouse", "needs_manager"])
async def test_alert_round_trips_kind(db_session, kind):
    s = db_session
    branch_id = await _branch(s, "Jakarta")
    lead_id = await _lead(s, branch_id, "+62811")
    notifier = FakeNotifier()
    await AlertService(s, branch_id, notifier).raise_alert(lead_id, kind, "en", "ru")
    row = (await s.exec(select(ManagerAlert))).one()
    assert row.kind == kind and len(notifier.sends) == 1
