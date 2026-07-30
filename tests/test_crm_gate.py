"""CRM read-gate: verdict policy, fail-open behaviour, and stand-down on hold."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import AppSetting, Branch, CrmLeadState, Lead, StageEvent  # noqa: E402
from app.domain.enums import Stage  # noqa: E402
from app.modules.crm.gate import CrmGate, compute_verdict  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

_URL = "https://crm.example/lead-state"


class _Reader:
    """Fake CRM reader returning a canned payload (or None to simulate an outage)."""

    def __init__(self, payload: dict | None, *, fail: bool = False) -> None:
        self._payload = payload
        self._fail = fail
        self.calls = 0

    async def get_state(self, url, secret, phone):  # noqa: ANN001, ANN201
        self.calls += 1
        return None if self._fail else self._payload


async def _branch(s, **settings: str) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    for k, v in {"crm_read_enabled": "true", "crm_state_url": _URL, **settings}.items():
        s.add(AppSetting(branch_id=b.id, key=k, value=v))
    await s.flush()
    invalidate(b.id)
    return b.id


async def _lead(s, bid: int, stage: Stage = Stage.QUALIFYING) -> Lead:
    lead = Lead(branch_id=bid, phone_e164="+628123", stage=stage, agent_enabled=True)
    s.add(lead)
    await s.flush()
    return lead


# ─── verdict policy ─────────────────────────────────────────────────────────────

def test_verdict_trusts_explicit_crm_field() -> None:
    assert compute_verdict({"verdict": "hold", "reason": "x"})[0] == "hold"
    assert compute_verdict({"verdict": "proceed"})[0] == "proceed"


def test_verdict_derives_hold_from_ownership_and_flags() -> None:
    assert compute_verdict({"owner": "manager"})[0] == "hold"
    assert compute_verdict({"deal_won": True})[0] == "hold"
    assert compute_verdict({"paid": True})[0] == "hold"
    assert compute_verdict({"next_contact_at": "2026-07-05T09:00:00Z"})[0] == "hold"


def test_verdict_proceed_when_clean() -> None:
    v, reason = compute_verdict({"owner": "bot", "exists": True})
    assert v == "proceed" and reason == ""


# ─── allow_send gating ──────────────────────────────────────────────────────────

async def test_gate_off_allows(db_session) -> None:
    bid = await _branch(db_session, crm_read_enabled="false")
    lead = await _lead(db_session, bid)
    ok, _ = await CrmGate(db_session, bid, _Reader({"verdict": "hold"})).allow_send(lead, "agent")
    assert ok is True


async def test_manager_send_bypasses_gate(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)
    r = _Reader({"verdict": "hold"})
    ok, _ = await CrmGate(db_session, bid, r).allow_send(lead, "manager")
    assert ok is True and r.calls == 0  # never even asked the CRM


async def test_unreachable_crm_fails_open(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)
    ok, _ = await CrmGate(db_session, bid, _Reader(None, fail=True)).allow_send(lead, "agent")
    assert ok is True  # CRM outage must never silence the bot


async def test_a_closed_deal_blocks_and_stands_lead_down(db_session) -> None:
    """The harsh branch, and the only one that should be harsh: the deal is done, so a
    chatty bot can only talk a paying customer back out of it."""
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, Stage.PRESENTING)
    ok, reason = await CrmGate(
        db_session, bid, _Reader({"exists": True, "deal_won": True})).allow_send(lead, "agent")
    assert ok is False and "deal won" in reason
    assert lead.stage == Stage.MANAGER and lead.agent_enabled is False  # stood down

    from sqlalchemy import func, select
    n = (await db_session.execute(
        select(func.count()).select_from(StageEvent).where(StageEvent.lead_id == lead.id)
    )).scalar()
    assert n == 1  # journaled the hand-off

    cached = (await db_session.execute(
        select(CrmLeadState).where(CrmLeadState.lead_id == lead.id))).scalars().first()
    assert cached is not None and cached.verdict == "hold"  # state cached


# ─── the two kinds of hold (behaviour change, 30.07.2026) ───────────────────────

async def test_a_manager_hold_still_lets_stepan_answer(db_session) -> None:
    """Changed deliberately. Every hold used to run through the same stand-down: an answered
    manager call moved the lead to MANAGER and set agent_enabled=False for 72 hours, so a
    lead writing "а можно частями?" got three days of silence. `wait_call` — the result that
    follows most answered calls — is 74% of everything the branch records, so this was the
    normal case, not an edge one."""
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, Stage.PRESENTING)
    gate = CrmGate(db_session, bid,
                   _Reader({"exists": True, "owner": "manager", "manager_called": True}))
    ok, _ = await gate.allow_send(lead, "agent")
    assert ok is True                                   # отвечаем
    assert lead.stage == Stage.PRESENTING               # и НЕ забираем лида у бота
    assert lead.agent_enabled is True


@pytest.mark.parametrize("source", ["followup", "reactivation"])
async def test_a_manager_hold_stops_stepan_from_starting_anything(
    db_session, source: str,
) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, Stage.PRESENTING)
    gate = CrmGate(db_session, bid,
                   _Reader({"exists": True, "manager_called": True}))
    ok, reason = await gate.allow_send(lead, source)
    assert ok is False and "manager called" in reason
    assert lead.agent_enabled is True                   # молчим по инициативе, не глушим совсем


def test_hold_kind_reads_flags_not_prose() -> None:
    from app.modules.crm.gate import hold_kind
    assert hold_kind({"deal_won": True}) == "silence"
    assert hold_kind({"paid": True}) == "silence"
    assert hold_kind({"manager_called": True}) == "initiative"
    assert hold_kind({"next_contact_at": "2026-08-01"}) == "initiative"
    assert hold_kind({"owner": "manager"}) == "initiative"
    assert hold_kind({}) == ""


async def test_proceed_allows_and_caches(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)
    ok, _ = await CrmGate(
        db_session, bid, _Reader({"exists": True, "owner": "bot"})).allow_send(lead, "agent")
    assert ok is True and lead.agent_enabled is True


async def test_fresh_cache_avoids_refetch(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)
    r = _Reader({"exists": True, "owner": "bot"})
    gate = CrmGate(db_session, bid, r)
    await gate.allow_send(lead, "agent")   # first call fetches + caches
    await gate.allow_send(lead, "agent")   # second call served from fresh cache
    assert r.calls == 1
