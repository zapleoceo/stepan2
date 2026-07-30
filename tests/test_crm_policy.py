"""Политика поведения по результату из CRM: таблица, а не разветвлённый код.

Пять значений — не весь каталог CRM (там их десять), а ровно те, которыми филиал Джакарты
пользуется на самом деле: за 30 дней из 2000 контактов wait_call 1486, result_think 225,
result_next_enrollment 189, result_fail 177, result_event 21. Остальные пять за месяц не
встретились ни разу, и писать в них значит класть лида в корзину, которую никто не открывает.
"""
from __future__ import annotations

import pytest

from app.modules.crm.policy import POLICIES, crm_state_block, policy_for


@pytest.mark.parametrize("status", [
    "wait_call", "result_think", "result_next_enrollment", "result_fail", "result_event",
])
def test_every_status_the_branch_actually_uses_has_a_policy(status: str) -> None:
    assert policy_for(status) is not None


def test_statuses_the_branch_never_uses_have_none() -> None:
    """contract Степан не ставит сознательно: оформление договора — зона менеджера."""
    for dead in ("contract", "study_yes", "material", "waiting_registration", "interview"):
        assert policy_for(dead) is None
    assert policy_for(None) is None
    assert policy_for("") is None


def test_only_the_three_that_need_a_conversation_let_stepan_speak_first() -> None:
    speaks = {k for k, v in POLICIES.items() if v.initiates}
    assert speaks == {"result_think", "result_next_enrollment", "result_fail"}


def test_wait_call_never_initiates() -> None:
    """Самый частый результат — 74% контактов. Менеджер ждёт следующего созвона: сами не
    пишем, но на входящее отвечаем (это уже решает гейт)."""
    assert POLICIES["wait_call"].initiates is False


def test_the_block_names_the_manager_the_date_and_the_goal() -> None:
    block = crm_state_block(
        "result_think", manager="Asih Angesti", when="2026-07-29T15:37:16+07:00")
    assert block is not None
    assert "Asih Angesti" in block
    assert "2026-07-29" in block
    assert "result_think" in block
    assert "сколько времени ему нужно" in block


def test_a_planned_call_forbids_initiative_in_words_the_model_can_follow() -> None:
    block = crm_state_block("wait_call", next_contact_at="2026-08-01T00:00:00+00:00")
    assert block is not None and "2026-08-01" in block
    assert "сам не пиши" in block


def test_an_unknown_status_produces_no_block() -> None:
    """Молчание лучше выдуманной инструкции: промт и так на пределе."""
    assert crm_state_block("something_new") is None
    assert crm_state_block(None) is None
