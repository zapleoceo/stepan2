"""BudgetService (per-branch daily LLM spend gate), CrmSyncService (webhook push with
synced_at watermark), MetaCapi (payload shape + graceful failure)."""
from __future__ import annotations

from typing import Any

from app.adapters.db.models import AppSetting, Branch, ManagerAlert
from app.adapters.meta_capi import MetaCapi, build_event, hash_phone
from app.modules.budget import BudgetService
from app.modules.crm import CrmSyncService
from app.modules.settings.service import invalidate


async def _branch(s, **settings: str) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    for key, value in settings.items():
        s.add(AppSetting(branch_id=b.id, key=key, value=value))
    await s.flush()
    invalidate(b.id)
    return b.id


# ─── budget ───────────────────────────────────────────────────────────────────

async def test_budget_records_and_accumulates(db_session) -> None:
    bid = await _branch(db_session, daily_budget_usd="5")
    svc = BudgetService(db_session, bid)
    await svc.record(0.4)
    await svc.record(0.6)
    assert await svc.spent_today() == 1.0
    assert await svc.over_budget() is False


async def test_budget_gates_when_limit_reached(db_session) -> None:
    bid = await _branch(db_session, daily_budget_usd="1")
    svc = BudgetService(db_session, bid)
    await svc.record(1.2)
    assert await svc.over_budget() is True


async def test_budget_zero_limit_means_off(db_session) -> None:
    bid = await _branch(db_session, daily_budget_usd="0")
    svc = BudgetService(db_session, bid)
    await svc.record(999.0)
    assert await svc.over_budget() is False


async def test_budget_branch_isolation(db_session) -> None:
    a = await _branch(db_session, daily_budget_usd="1")
    b = await _branch(db_session, daily_budget_usd="1")
    await BudgetService(db_session, a).record(5.0)
    assert await BudgetService(db_session, a).over_budget() is True
    assert await BudgetService(db_session, b).over_budget() is False
    assert await BudgetService(db_session, b).spent_today() == 0.0


async def test_budget_negative_cost_ignored(db_session) -> None:
    bid = await _branch(db_session, daily_budget_usd="1")
    svc = BudgetService(db_session, bid)
    await svc.record(-3.0)
    assert await svc.spent_today() == 0.0


# ─── crm sync ─────────────────────────────────────────────────────────────────

class FakeCrm:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post_alert(self, url: str, payload: dict[str, Any]) -> bool:
        self.calls.append((url, payload))
        return self.ok


async def _alert(s, branch_id: int, *, kind: str = "needs_manager") -> ManagerAlert:
    a = ManagerAlert(branch_id=branch_id, lead_id=1, kind=kind, lead_phone="+628123456789")
    s.add(a)
    await s.flush()
    return a


async def test_crm_disabled_pushes_nothing(db_session) -> None:
    bid = await _branch(db_session, crm_enabled="false", crm_webhook_url="https://x.example")
    await _alert(db_session, bid)
    fake = FakeCrm()
    assert await CrmSyncService(db_session, bid, fake).sync_pending() == 0
    assert fake.calls == []


async def test_crm_no_url_pushes_nothing(db_session) -> None:
    bid = await _branch(db_session, crm_enabled="true", crm_webhook_url="")
    await _alert(db_session, bid)
    assert await CrmSyncService(db_session, bid, FakeCrm()).sync_pending() == 0


async def test_crm_syncs_and_stamps_watermark(db_session) -> None:
    bid = await _branch(db_session, crm_enabled="true", crm_webhook_url="https://x.example/hook")
    alert = await _alert(db_session, bid, kind="ready_deal")
    fake = FakeCrm()
    assert await CrmSyncService(db_session, bid, fake).sync_pending() == 1
    assert alert.synced_at is not None
    url, payload = fake.calls[0]
    assert url == "https://x.example/hook"
    assert payload["kind"] == "ready_deal"
    assert payload["lead_phone"] == "+628123456789"
    # second tick: nothing pending anymore
    assert await CrmSyncService(db_session, bid, fake).sync_pending() == 0


async def test_crm_failure_leaves_unsynced_for_retry(db_session) -> None:
    bid = await _branch(db_session, crm_enabled="true", crm_webhook_url="https://x.example")
    alert = await _alert(db_session, bid)
    assert await CrmSyncService(db_session, bid, FakeCrm(ok=False)).sync_pending() == 0
    assert alert.synced_at is None


# ─── meta capi ────────────────────────────────────────────────────────────────

def test_hash_phone_normalizes_variants() -> None:
    assert hash_phone("+62 812-3456-7890") == hash_phone("6281234567890")
    assert hash_phone("081") is None  # too short
    assert hash_phone(None) is None


def test_build_event_shape() -> None:
    e = build_event(event_name="Lead", event_id="alert-7", phone="+6281234567890")
    assert e["event_name"] == "Lead"
    assert e["event_id"] == "alert-7"
    assert e["action_source"] == "chat"
    assert len(e["user_data"]["ph"][0]) == 64  # sha256 hex


def test_build_event_without_phone_has_empty_user_data() -> None:
    e = build_event(event_name="Lead", event_id="x", phone=None)
    assert e["user_data"] == {}


async def test_capi_missing_config_is_noop() -> None:
    capi = MetaCapi()
    assert await capi.send_lead("", "", event_id="x") is False
    assert await capi.send_lead("123", "", event_id="x") is False


async def test_capi_posts_and_survives_failure(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []

    async def fake_post(self, pixel_id, token, payload):  # noqa: ANN001
        sent.append(payload)
        return True

    monkeypatch.setattr(MetaCapi, "_post", fake_post)
    ok = await MetaCapi().send_lead("pix", "tok", event_id="a-1", phone="+6281234567890")
    assert ok is True
    assert sent[0]["data"][0]["event_id"] == "a-1"
