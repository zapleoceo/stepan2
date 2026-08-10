"""Пара «стадия + тумблер бота» — одно правило на всех, кто её меняет.

Правило жило внутри `ops.move_lead`, то есть было доступно только внешней системе через MCP.
Приёму сообщений понадобилось то же самое, и копия неизбежно бы разошлась — стадия говорила
бы одно, тумблер другое. Здесь оно проверяется само по себе, без сессии и без базы.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.enums import BOT_SILENT_STAGES, HUMAN_LED_STAGES, Stage
from app.domain.funnel import apply_stage


@dataclass
class _Lead:
    stage: Stage = Stage.NEW
    agent_enabled: bool = True
    agent_off_manual: bool = False


def test_manager_takes_the_thread_and_the_bot_goes_quiet() -> None:
    lead = _Lead(stage=Stage.PRESENTING)
    apply_stage(lead, Stage.MANAGER)
    assert lead.stage == Stage.MANAGER
    assert lead.agent_enabled is False


def test_manager_is_silenced_by_the_switch_not_by_the_stage() -> None:
    """Именно поэтому менеджер может вернуть лида: попади MANAGER в BOT_SILENT_STAGES,
    возврат перестал бы работать, а `_revive_bot` и так не трогает HUMAN_LED_STAGES."""
    assert Stage.MANAGER not in BOT_SILENT_STAGES
    assert Stage.MANAGER in HUMAN_LED_STAGES


@pytest.mark.parametrize("target", [
    Stage.QUALIFYING, Stage.PRESENTING, Stage.OBJECTION, Stage.NURTURING, Stage.NEW,
])
def test_handing_back_re_arms_the_bot_and_clears_a_manual_mute(target: Stage) -> None:
    lead = _Lead(stage=Stage.MANAGER, agent_enabled=False, agent_off_manual=True)
    apply_stage(lead, target)
    assert lead.stage == target
    assert lead.agent_enabled is True
    assert lead.agent_off_manual is False


@pytest.mark.parametrize("target", sorted(BOT_SILENT_STAGES))
def test_a_bot_silent_stage_leaves_the_switch_alone(target: Stage) -> None:
    """READY, HANDED_OFF и DORMANT молчат самой стадией. Трогать там тумблер значило бы
    затирать решение человека, которое эта стадия и означает."""
    lead = _Lead(stage=Stage.MANAGER, agent_enabled=False, agent_off_manual=True)
    apply_stage(lead, target)
    assert lead.stage == target
    assert lead.agent_enabled is False
    assert lead.agent_off_manual is True
