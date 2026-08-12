"""Раздел «Настройки» разделён по роли.

Устройство самого Степана — системный токен Meta, ключи CRM и sender, дневной бюджет в
долларах, антибан-лимиты, рубильник бота на весь филиал — принадлежит супер-админу. Админу
филиала остаётся то, что касается исключительно его филиала; на 12.08.2026 это расписание
фолоапов по каждому коннектору.

До этого настройки коннектора можно было открыть только через «Филиалы» → канал, а тот пункт
закрыт супер-админом намеренно: заведение и правка филиалов — платформенное действие.
Настройки коннектора попали под тот же замок за компанию, и пять админов филиала Индонезии
(Citra, Excel, Lisa, Maya, Ланиэль) не могли поменять расписание касаний, хотя право на
запись у них есть и маршрут сохранения его признаёт.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from app.modules.settings import schema as S  # noqa: E402


def test_a_branch_admin_sees_only_what_is_marked_for_them() -> None:
    allowed = {f.key for f in S.all_fields() if f.branch_admin}
    assert allowed == {"followup_enabled", "followup_schedule_h"}


def test_everything_else_is_closed_by_default() -> None:
    """Запрет по умолчанию, а не список запрещённого: новая настройка не должна открываться
    менеджеру просто потому, что кто-то забыл про флаг."""
    closed = {f.key for f in S.all_fields() if not f.branch_admin}
    for key in ("meta_system_user_token", "crm_mcp_url", "sender_mcp_url",
                "daily_budget_usd", "daily_cap", "agent_enabled_global", "tg_group_id"):
        assert key in closed, key


def test_the_branch_panel_is_empty_for_a_branch_admin() -> None:
    """Филиальные ключи — это ключи, токены и бюджет. Менеджеру там смотреть не на что."""
    assert S.sections_for_scope("branch", branch_admin_only=True) == []
    assert S.sections_for_scope("branch") != []  # супер-админу всё как было


def test_the_connector_block_keeps_exactly_the_follow_up_section() -> None:
    secs = S.sections_for_scope("channel", branch_admin_only=True)
    assert [f.key for sec in secs for f in sec.fields] == [
        "followup_enabled", "followup_schedule_h"]


def test_a_section_is_split_field_by_field_not_wholesale() -> None:
    """В разделе «Фолоап» рядом лежат расписание касаний (дело филиала) и реактивация с
    еженедельным аудитом обучения (наши механизмы) — разрешение даётся по одному полю."""
    for sec in S.sections_for_scope("channel", branch_admin_only=True):
        keys = {f.key for f in sec.fields}
        assert "reactivation_enabled" not in keys
        assert "learning_audit_enabled" not in keys


# ── запрет на маршруте, а не только в вёрстке ─────────────────────────────────


async def test_a_branch_admin_cannot_save_a_platform_setting() -> None:
    """Скрыть поле в интерфейсе — оформление, а не право: маршрут дёргается напрямую.

    Проверяем самый дорогой ключ на панели — системный токен Meta. Рядом с ним лежат ключи
    CRM и sender и дневной бюджет; все закрыты одним и тем же условием."""
    from fastapi import Request

    from app.api._routes_admin import settings_save_by_key

    req = Request({"type": "http", "headers": [], "state": {}})
    req.state.allowed_branch_ids = [1]   # админ филиала, не супер-админ
    req.state.writable_branch_ids = [1]

    resp = await settings_save_by_key(
        req, key="meta_system_user_token", value="secret", channel_id=None)

    assert resp.status_code == 403


def test_the_gate_lets_a_permitted_key_through_and_stops_the_rest() -> None:
    """Само правило, которым маршрут отвечает 403. Проверки филиала и владения каналом,
    стоящие следом, были и раньше и проверяются своими тестами."""
    budget = S.field_for("daily_budget_usd")
    followup = S.field_for("followup_enabled")
    assert budget is not None and followup is not None

    assert S.may_edit(budget, is_super=True) is True
    assert S.may_edit(budget, is_super=False) is False
    assert S.may_edit(followup, is_super=False) is True
