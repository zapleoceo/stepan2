"""Capability routing (hybrid cost policy): keep chat:smart for money moments, send the
cheap majority to chat:fast, and honour the off switch."""
from __future__ import annotations

from app.domain.enums import Stage
from app.modules.conversation.routing import (
    FAST,
    SMART,
    parse_smart_stages,
    pick_capability,
)


def _pick(**over) -> str:
    base = dict(workflow="reply", stage=Stage.QUALIFYING, lead_type=None,
                last_inbound="halo kak", mode="hybrid")
    base.update(over)
    return pick_capability(**base)


def test_off_mode_always_smart() -> None:
    assert _pick(mode="off") == SMART
    assert _pick(mode="off", stage=Stage.NEW, workflow="followup") == SMART


def test_followups_are_cheap() -> None:
    assert _pick(workflow="followup") == FAST


def test_money_stages_stay_smart() -> None:
    for st in (Stage.PRESENTING, Stage.OBJECTION, Stage.READY):
        assert _pick(stage=st) == SMART


def test_hot_lead_stays_smart() -> None:
    assert _pick(lead_type="hot") == SMART


def test_early_low_stakes_is_fast() -> None:
    assert _pick(stage=Stage.NEW) == FAST
    assert _pick(stage=Stage.QUALIFYING, lead_type="cold") == FAST
    assert _pick(stage=Stage.NURTURING, lead_type="unclear") == FAST


def test_buying_signal_forces_smart_even_early() -> None:
    # A hot signal at an early stage must not be gambled on the cheap model.
    assert _pick(stage=Stage.QUALIFYING, last_inbound="kak gimana cara daftar?") == SMART
    assert _pick(stage=Stage.NEW, last_inbound="mau bayar sekarang dong") == SMART
    assert _pick(stage=Stage.QUALIFYING, last_inbound="Gasss") == SMART
    assert _pick(stage=Stage.QUALIFYING, last_inbound="ini nomor wa 0812 3456 7890") == SMART
    assert _pick(stage=Stage.QUALIFYING, last_inbound="masih mikir dulu ya") == FAST


def test_parse_smart_stages() -> None:
    assert parse_smart_stages("presenting, objection ,ready") == frozenset(
        {"presenting", "objection", "ready"})
    assert parse_smart_stages("presenting,bogus") == frozenset({"presenting"})  # drop unknown
    assert parse_smart_stages("") == frozenset({"presenting", "objection", "ready"})  # → default
    assert parse_smart_stages("nonsense") == frozenset(
        {"presenting", "objection", "ready"})  # all-invalid → default, never all-fast by typo


def test_deep_conversation_forces_smart_regardless_of_stage() -> None:
    # A lead 6+ turns deep represents real invested effort, even stuck in 'qualifying'
    # with no smart_stage/lead_type/buy-signal to trigger the older rules.
    assert _pick(stage=Stage.QUALIFYING, lead_type="cold", inbound_count=5) == FAST
    assert _pick(stage=Stage.QUALIFYING, lead_type="cold", inbound_count=6) == SMART
    assert _pick(stage=Stage.NEW, inbound_count=10) == SMART


def test_guard_regen_history_stays_on_smart_for_this_lead() -> None:
    # Once guard has already had to regenerate a reply for this lead, keep it on smart —
    # a per-LEAD signal, not something a stage or this turn's text could ever show.
    assert _pick(stage=Stage.NEW, guard_regen_count=0) == FAST
    assert _pick(stage=Stage.NEW, guard_regen_count=1) == SMART
    assert _pick(stage=Stage.QUALIFYING, lead_type="cold", guard_regen_count=2) == SMART


def test_smart_stages_is_tunable() -> None:
    # Operator narrows the strong-model stages to objection only → presenting now routes fast.
    only_obj = frozenset({"objection"})
    assert _pick(stage=Stage.PRESENTING, smart_stages=only_obj) == FAST
    assert _pick(stage=Stage.OBJECTION, smart_stages=only_obj) == SMART
    # Widen to include qualifying → qualifying now routes smart.
    assert _pick(stage=Stage.QUALIFYING,
                 smart_stages=frozenset({"qualifying", "presenting"})) == SMART
