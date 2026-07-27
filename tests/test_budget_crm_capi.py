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


def test_the_send_token_is_the_system_user_one_not_the_legacy_field() -> None:
    """`meta_capi_token` was superseded by `meta_system_user_token` — the settings schema
    marks it "legacy, use the System User token above" and hides it — but the send path kept
    reading the old field. On branch 1 it held `1q2w#E$R`: eight characters, a keyboard walk
    left behind when someone filled the form.

    Eight characters is truthy, so the guard passed and every hand-off posted to Meta and got
    back 401 Unauthorized. All 76 of them, silently — the adapter logs a warning and returns
    False by design so ad tracking can never break a hand-off, and log rotation carried the
    warnings away. Meta received nothing, and the campaigns optimised on the only signal they
    had: a message being started."""
    from types import SimpleNamespace

    from app.adapters.meta_capi import capi_token

    both = SimpleNamespace(**{"meta_system_user_token": "EAAP-real-one",
                              "meta_capi_token": "1q2w#E$R"})
    assert capi_token(both) == "EAAP-real-one"

    # A branch that only ever had the legacy field keeps working.
    legacy_only = SimpleNamespace(**{"meta_system_user_token": "", "meta_capi_token": "EAAB-old"})
    assert capi_token(legacy_only) == "EAAB-old"

    # Whitespace is not a token — a field someone cleared by typing a space stays empty.
    blank = SimpleNamespace(**{"meta_system_user_token": "  ", "meta_capi_token": ""})
    assert capi_token(blank) == ""


def test_a_handoff_sends_with_the_system_user_token(monkeypatch) -> None:
    """End to end through the guard in delivery._handoff: the placeholder must not be what
    reaches Meta."""
    from types import SimpleNamespace

    from app.adapters.meta_capi import capi_token

    cfg = SimpleNamespace(**{
        "meta_pixel_id": "2085648545498314",
        "meta_capi_token": "1q2w#E$R",
        "meta_system_user_token": "EAAPvalid",
    })
    assert cfg.meta_pixel_id and capi_token(cfg)
    assert capi_token(cfg) != cfg.meta_capi_token


def test_the_crm_state_carries_the_answer_to_did_they_buy() -> None:
    """The CRM's MCP has always returned deal_won; nothing parsed it off the state, so the one
    question the business asks had no answer in our data — which is different from zero."""
    from app.modules.crm.gate import _parse

    won = _parse({"exists": True, "crm_id": 217233, "deal_won": True,
                  "manager_called": True, "last_manager_call_at": "2026-07-20T10:00:00"})
    assert won.deal_won is True
    assert won.manager_called is True
    # Not in the contract yet — accepted under either name for the day it is added.
    assert won.won_at is None
    assert _parse({"exists": True, "deal_won_at": "2026-07-20"}).won_at == "2026-07-20"
    assert _parse({"exists": True}).deal_won is False


async def test_outcomes_polls_the_leads_the_gate_deliberately_skips(db_session) -> None:
    """The gate asks "should the bot keep talking?", so it filters to active stages and
    agent_enabled — and a hand-off sets the stage to ready/handed_off/manager AND mutes the
    bot. Between them those filters hid every lead that could have bought: 53 of them, none
    ever polled. The one won deal we knew about surfaced only because that lead happened to be
    checked earlier, while it was still in the funnel."""
    from app.adapters.db.models import Branch, Lead
    from app.domain.enums import Stage
    from app.modules.crm.pull import CrmPullService

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    handed = Lead(branch_id=b.id, stage=Stage.HANDED_OFF, phone_e164="+628111",
                  agent_enabled=False)  # exactly what a hand-off leaves behind
    active = Lead(branch_id=b.id, stage=Stage.QUALIFYING, phone_e164="+628222",
                  agent_enabled=True)
    no_phone = Lead(branch_id=b.id, stage=Stage.READY, phone_e164=None, agent_enabled=False)
    db_session.add_all([handed, active, no_phone])
    await db_session.flush()

    svc = CrmPullService(db_session, b.id, reader=None)
    exited = await svc._stale_exited(limit=10)  # noqa: SLF001
    ids = {lead.id for lead in exited}
    assert handed.id in ids, "a muted, handed-off lead is the whole point"
    assert active.id not in ids, "still in the funnel — that is the gate's job"
    assert no_phone.id not in ids, "no phone means no CRM lookup key"


def test_the_contract_date_was_there_all_along() -> None:
    """deal_won is derived by us — `any(row.typeName == "contract")` over the CRM history — and
    that collapse threw the date away. Every history row carries `date_time`; _last_answered_call
    has always read it off out-call rows. So nothing had to be added on the CRM side."""
    from app.adapters.crm_mcp import CrmMcpReader

    rows = [
        {"typeName": "out-call", "no_answer": "0", "date_time": "2026-07-18T09:00:00"},
        {"typeName": "contract", "date_time": "2026-07-20T14:30:00"},
        {"typeName": "contract", "date_time": "2026-07-02T11:00:00"},
    ]
    out = CrmMcpReader("jakarta")._derive(217233, rows)  # noqa: SLF001
    assert out["deal_won"] is True
    assert out["deal_won_at"].startswith("2026-07-20"), "the NEWEST contract wins"
    assert CrmMcpReader("jakarta")._derive(1, [])["deal_won_at"] is None  # noqa: SLF001


def test_a_deal_that_predates_our_first_message_is_not_ours() -> None:
    """A phone can sit in the CRM long before Stepan writes — a walk-in, a call, last year's
    enquiry. Counting those would credit the bot with other people's sales and teach Meta to
    buy more of an audience that was already converting without it."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.modules.crm.pull import _ours

    lead = SimpleNamespace(created_at=datetime(2026, 7, 15, tzinfo=UTC))
    after = SimpleNamespace(won_at="2026-07-20T14:30:00+00:00")
    before = SimpleNamespace(won_at="2026-05-01T10:00:00+00:00")
    assert _ours(after, lead) is True
    assert _ours(before, lead) is False
    # An unknown or unparseable date counts as ours — under-reporting revenue is the more
    # expensive mistake, since it is the number the whole channel is judged on.
    assert _ours(SimpleNamespace(won_at=None), lead) is True
    assert _ours(SimpleNamespace(won_at="whenever"), lead) is True
