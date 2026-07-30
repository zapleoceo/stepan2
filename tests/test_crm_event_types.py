"""Тип события в CRM зависит от состояния лида, и одно состояние уезжает один раз.

Раньше всё уходило как wait_call: менеджер видел «перезвонить» и на том, кто отказался, и на
том, кто ждёт следующего набора. И дедуп был по факту «когда-то отправляли», а не по
состоянию, поэтому лид, который сначала думал, а потом отказался, вторым событием не
отражался вовсе — а лид 3066, вернувшийся из закрытой стадии в работу, наоборот получил дубль
(crm_pushed_handoff, затем crm_pushed, оба по сути «перезвонить»).
"""
from __future__ import annotations

import pytest

from app.modules.crm.push_mcp import (
    EVENT_REJECT,
    EVENT_THINKING,
    EVENT_WAIT_CALL,
    LeadToPush,
    event_type_for,
    pushed_marker,
)


def _lead(stage: str = "qualifying", lead_type: str | None = None) -> LeadToPush:
    return LeadToPush(lead_id=1, phone="+628123", name="A", stage=stage, product=None,
                      days_idle=0, last_msg="", lead_type=lead_type)


@pytest.mark.parametrize(("stage", "lead_type", "expected"), [
    ("qualifying", None, EVENT_WAIT_CALL),
    ("presenting", "hot", EVENT_WAIT_CALL),
    ("dormant", None, EVENT_THINKING),
    ("qualifying", "non_target", EVENT_REJECT),
    ("dormant", "non_target", EVENT_REJECT),      # отказ важнее молчания
])
def test_state_picks_the_event_type(stage: str, lead_type: str | None,
                                    expected: str) -> None:
    assert event_type_for(_lead(stage, lead_type)) == expected


def test_the_marker_carries_the_type() -> None:
    assert pushed_marker(EVENT_REJECT) == "crm_pushed:reject"
    assert pushed_marker(EVENT_WAIT_CALL) == "crm_pushed:wait_call"
    assert pushed_marker(EVENT_THINKING) != pushed_marker(EVENT_WAIT_CALL)


def test_contract_is_never_ours() -> None:
    """Оформление договора — зона менеджера; Степан такого события не ставит ни при каком
    состоянии лида."""
    for stage in ("new", "qualifying", "presenting", "ready", "manager", "handed_off",
                  "objection", "nurturing", "dormant"):
        for lt in (None, "hot", "warm", "cold", "no_budget", "non_target"):
            assert event_type_for(_lead(stage, lt)) != "contract"
