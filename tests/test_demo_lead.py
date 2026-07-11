"""Landing demo lead capture: contact-gating, tolerant JSON parse, and notify-once dedup."""
from __future__ import annotations

import app.api._demo_lead as dl


class _FakeSettings:
    demo_notify_tg_id = 12345
    bootstrap_super_admin = 0
    tg_bot_token = "tok"  # noqa: S105 — fake token for the test double


class _Broker:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __call__(self):  # BrokerLLM() → instance
        return self

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
        return self._payload, {}


class _Notifier:
    sent: list[str] = []

    def __init__(self, **kw) -> None:  # noqa: ANN003
        pass

    async def send(self, *, text: str, topic_id=None):  # noqa: ANN001
        _Notifier.sent.append(text)
        return "ok"


def _wire(monkeypatch, payload: str) -> None:
    dl._notified.clear()
    _Notifier.sent = []
    monkeypatch.setattr(dl, "settings", lambda: _FakeSettings())
    monkeypatch.setattr(dl, "BrokerLLM", _Broker(payload))
    monkeypatch.setattr(dl, "TelegramNotifier", _Notifier)


def test_has_contactish_matches_email_phone_handle_only_from_user() -> None:
    assert dl._has_contactish([{"role": "user", "content": "reach me at a@b.com"}])
    assert dl._has_contactish([{"role": "user", "content": "+380 99 481 1889"}])
    assert dl._has_contactish([{"role": "user", "content": "im @my_handle on tg"}])
    assert not dl._has_contactish([{"role": "user", "content": "how much does it cost?"}])
    # a contact only in Stepan's own message must not trip the gate
    assert not dl._has_contactish([{"role": "assistant", "content": "email me a@b.com"}])


def test_parse_json_tolerates_fences_and_junk() -> None:
    assert dl._parse_json('{"ready": true}')["ready"] is True
    assert dl._parse_json('```json\n{"ready": false}\n```')["ready"] is False
    assert dl._parse_json("prefix {\"ready\": true} suffix")["ready"] is True
    assert dl._parse_json("not json at all") is None


async def test_notifies_once_and_dedups_same_contact(monkeypatch) -> None:
    _wire(monkeypatch, '{"ready":true,"contact_type":"whatsapp",'
                       '"contact":"+380994811889","wants":"купить Степана","summary":"хочет"}')
    history = [{"role": "user", "content": "beri, my whatsapp +380994811889, i want to buy"}]
    await dl.maybe_notify(history)
    assert len(_Notifier.sent) == 1
    assert "+380994811889" in _Notifier.sent[0]
    assert "WhatsApp" in _Notifier.sent[0]
    # same contact again → no second ping
    await dl.maybe_notify(history)
    assert len(_Notifier.sent) == 1


async def test_no_notify_when_not_ready(monkeypatch) -> None:
    _wire(monkeypatch, '{"ready":false,"contact":"a@b.com"}')
    await dl.maybe_notify([{"role": "user", "content": "just curious, a@b.com"}])
    assert _Notifier.sent == []


async def test_skips_broker_when_no_contact_in_history(monkeypatch) -> None:
    called = {"n": 0}

    class _CountingBroker:
        def __call__(self):
            return self

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            called["n"] += 1
            return "{}", {}

    dl._notified.clear()
    _Notifier.sent = []
    monkeypatch.setattr(dl, "settings", lambda: _FakeSettings())
    monkeypatch.setattr(dl, "BrokerLLM", _CountingBroker)
    monkeypatch.setattr(dl, "TelegramNotifier", _Notifier)
    await dl.maybe_notify([{"role": "user", "content": "how much is it?"}])
    assert called["n"] == 0          # no contact-shaped text → no extraction call
    assert _Notifier.sent == []


async def test_no_notify_without_target_or_token(monkeypatch) -> None:
    class _Empty:
        demo_notify_tg_id = 0
        bootstrap_super_admin = 0
        tg_bot_token = ""

    dl._notified.clear()
    _Notifier.sent = []
    monkeypatch.setattr(dl, "settings", lambda: _Empty())
    monkeypatch.setattr(dl, "TelegramNotifier", _Notifier)
    await dl.maybe_notify([{"role": "user", "content": "buy it, a@b.com"}])
    assert _Notifier.sent == []
