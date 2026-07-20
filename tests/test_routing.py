"""Capability routing (hybrid cost policy): keep chat:smart for money moments, send the
cheap majority to chat:fast, and honour the off switch."""
from __future__ import annotations

from app.domain.enums import Stage
from app.modules.conversation.routing import (
    FAST,
    SMART,
    pick_capability,
)


def _pick(**over) -> str:
    # default mid-conversation (inbound_count=3) so tests aren't all the first-reply case
    base = dict(workflow="reply", stage=Stage.QUALIFYING, lead_type=None,
                last_inbound="halo kak", inbound_count=3)
    base.update(over)
    return pick_capability(**base)


def test_first_reply_to_new_lead_is_smart() -> None:
    # the opener decides ~76% of ghosts — never gamble it on the free pool
    assert _pick(inbound_count=1, stage=Stage.NEW) == SMART
    assert _pick(inbound_count=0) == SMART


def test_followups_are_cheap() -> None:
    assert _pick(workflow="followup", followup_attempt=0) == FAST


def test_money_stages_stay_smart() -> None:
    for st in (Stage.PRESENTING, Stage.OBJECTION, Stage.READY):
        assert _pick(stage=st) == SMART


def test_hot_lead_stays_smart() -> None:
    assert _pick(lead_type="hot") == SMART


def test_active_sales_stages_are_smart() -> None:
    # owner 2026-07-20: every active sales stage runs on the strong model, even neutral chatter
    assert _pick(stage=Stage.QUALIFYING, lead_type="cold", last_inbound="oh gitu ya") == SMART
    assert _pick(stage=Stage.NURTURING, lead_type="unclear", last_inbound="oke makasih") == SMART
    # the cheap lane remains for the 'new' stage past the first reply (pre-discovery neutral)
    assert _pick(stage=Stage.NEW, lead_type="cold", last_inbound="oh gitu ya") == FAST


def test_buying_signal_forces_smart_even_early() -> None:
    # A hot signal at an early stage must not be gambled on the cheap model.
    assert _pick(stage=Stage.QUALIFYING, last_inbound="kak gimana cara daftar?") == SMART
    assert _pick(stage=Stage.NEW, last_inbound="mau bayar sekarang dong") == SMART
    assert _pick(stage=Stage.QUALIFYING, last_inbound="Gasss") == SMART
    assert _pick(stage=Stage.QUALIFYING, last_inbound="ini nomor wa 0812 3456 7890") == SMART
    # a soft-no is the save-the-sale (objection-handling) turn — it needs the strong model
    # too (sales-logic audit 2026-07-19); a neutral message still rides the cheap lane
    assert _pick(stage=Stage.QUALIFYING, last_inbound="masih mikir dulu ya") == SMART
    # a neutral acknowledgement in the 'new' stage still rides the cheap lane
    assert _pick(stage=Stage.NEW, last_inbound="oke kak makasih infonya") == FAST


def test_deep_conversation_forces_smart_regardless_of_stage() -> None:
    # A lead 10+ turns deep represents real invested effort. Active sales stages are smart
    # anyway now; the deep-thread rule still lifts a lingering 'new'-stage lead onto smart.
    assert _pick(stage=Stage.NEW, lead_type="cold", inbound_count=6) == FAST
    assert _pick(stage=Stage.NEW, lead_type="cold", inbound_count=9) == FAST
    assert _pick(stage=Stage.NEW, lead_type="cold", inbound_count=10) == SMART
    assert _pick(stage=Stage.NEW, inbound_count=12) == SMART


def test_guard_regen_history_stays_on_smart_for_this_lead() -> None:
    # Once guard has REPEATEDLY had to regenerate replies for this lead, keep it on smart —
    # a per-LEAD signal. A single regen over the whole history is noise (it made every lead
    # who ever tripped one smart forever); two+ is a pattern.
    assert _pick(stage=Stage.NEW, guard_regen_count=0) == FAST
    assert _pick(stage=Stage.NEW, guard_regen_count=1) == FAST
    assert _pick(stage=Stage.NEW, guard_regen_count=2) == SMART
    assert _pick(stage=Stage.QUALIFYING, lead_type="cold", guard_regen_count=3) == SMART


