"""Capability routing (hybrid cost policy): keep chat:smart for money moments, send the
cheap majority to chat:fast, and honour the off switch."""
from __future__ import annotations

from app.domain.enums import Stage
from app.modules.conversation.routing import FAST, SMART, pick_capability


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
