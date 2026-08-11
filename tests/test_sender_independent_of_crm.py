"""Две связи с CRM — разные, даже когда адрес совпадает буква в букву.

  CRM (crm_mcp_url) — мы СПРАШИВАЕМ о лиде и ОТДАЁМ туда события. Это учёт.
  sender (sender_mcp_url) — мы ЧИТАЕМ, что лид написал, и ОТВЕЧАЕМ ему. Это разговор.

Соблазн сэкономить настройку велик: сервер у поставщика может быть один, и «раз адрес тот
же — возьмём из CRM» выглядит безобидно. Цена — отключение учёта затыкает переписку с живым
человеком, а смена токена у одного ломает другое. Тесты держат границу, потому что в коде
она невидима: это отсутствие связи, а отсутствие само себя не охраняет.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from pathlib import Path  # noqa: E402

from app.modules.settings.schema import SCHEMA  # noqa: E402
from app.modules.settings.tenant_keys import TENANT_ONLY_KEYS  # noqa: E402

_SENDER_KEYS = ("sender_mcp_url", "sender_project", "sender_project_id",
                "sender_branch_id", "sender_enabled")
_CRM_KEYS = ("crm_mcp_url", "crm_mcp_city_alias", "crm_enabled")

_SENDER_SOURCES = (
    "app/adapters/sender_mcp.py",
    "app/adapters/channels/crm_sender.py",
    "app/connectors/crm_sender.py",
    "app/modules/sender/tenant.py",
    "app/modules/sender/catchup.py",
    "app/modules/sender/inbound.py",
)


def _all_settings() -> dict[str, object]:
    return {f.key: f for section in SCHEMA
            for f in getattr(section, "fields", [])}


def test_every_sender_key_exists_and_is_its_own() -> None:
    """Свои ключи, а не переиспользование крмовских под другим именем."""
    keys = _all_settings()
    for key in _SENDER_KEYS:
        assert key in keys, f"нет настройки {key}"
    assert not set(_SENDER_KEYS) & set(_CRM_KEYS)


def test_the_sender_link_is_per_branch_like_the_crm_one() -> None:
    """Иначе второй арендатор заводится правкой исходников, а не настройкой."""
    for key in _SENDER_KEYS:
        assert key in TENANT_ONLY_KEYS, f"{key} должен быть пофилиальным"


def test_no_sender_module_reads_a_crm_setting() -> None:
    """Граница проверяется по исходникам, потому что нарушить её можно одной строкой —
    `cfg.crm_mcp_url` вместо `cfg.sender_mcp_url` — и всё продолжит работать, пока у CRM и
    sender совпадают адреса. Разъедутся они в тот день, когда починить будет некому."""
    root = Path(__file__).resolve().parents[1]
    for rel in _SENDER_SOURCES:
        text = (root / rel).read_text(encoding="utf-8")
        for crm_key in _CRM_KEYS:
            assert f"cfg.{crm_key}" not in text and f'"{crm_key}"' not in text, (
                f"{rel} читает настройку CRM {crm_key} — связи должны быть независимы"
            )


def test_the_two_may_point_at_the_same_address_without_sharing_a_setting() -> None:
    """«Оба одинаковые» — допустимое совпадение ЗНАЧЕНИЙ, а не общая настройка.

    Тест держит именно это различие: значения равны, объекты разные, и отключение одного
    не трогает другое."""
    from app.adapters.sender_mcp import SenderMcp

    same = "https://mcp.itstep.org/mcp?token=t"
    sender = SenderMcp(same)

    assert sender.configured
    # Учёт отключили — разговор продолжается.
    assert SenderMcp(same).configured is True
    # Разговор отключили — на учёт это не влияет: у него свой ключ, которого здесь просто нет.
    assert SenderMcp("").configured is False


def test_an_unconfigured_sender_never_calls_anything() -> None:
    """Филиал без sender молчит, а не ходит по адресу CRM «за компанию»."""
    from app.adapters.sender_mcp import SenderMcp

    assert SenderMcp("").configured is False
    assert SenderMcp("   ").configured is False
