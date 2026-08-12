"""The building blocks a settings section is written in.

Separate from schema.py so a section can live in its own module (schema_crm) without
importing the very schema it is assembled into.
"""
from __future__ import annotations

from dataclasses import dataclass

type I18n = dict[str, str]


@dataclass(frozen=True)
class SettingField:
    key: str
    kind: str  # bool | int | text | secret
    default: str
    label: I18n
    placeholder: I18n | None = None
    help: I18n | None = None
    width: str = "120px"
    hidden: bool = False  # in defaults() but never rendered (vestigial/internal keys)
    choices: list[tuple[str, I18n]] | None = None  # text field → dropdown of fixed options
    # "branch" renders in the branch panel and applies to the whole branch; "channel" renders
    # in the per-connector editor and resolves per channel (falling back to branch → platform).
    scope: str = "branch"
    # What a channel-scoped setting REQUIRES of a connector to be worth showing.
    #
    # Every connector used to show every channel setting: comment caps in the WhatsApp
    # editor, follow-up timers on the website channel, and an operator with no way to tell
    # which of them their connector actually reads.
    #
    # Deliberately a requirement and not a list of connector names. The connectors already
    # declare what they are (ConnectorSpec.capabilities, .proactive_outreach), so a setting
    # that names them would have to be edited every time one is added — the exact `if kind ==`
    # sprawl the registry exists to end.
    capability: str | None = None   # Capability value the connector must declare
    needs_outreach: bool = False    # only where the bot may write to a silent lead
    # Видит и меняет ли это админ филиала. По умолчанию НЕТ, и это осознанный запрет по
    # умолчанию: на панели лежат системный токен Meta, ключи CRM и sender, дневной бюджет в
    # долларах, антибан-лимиты и рубильник бота на весь филиал. Новая настройка не должна
    # становиться доступной менеджеру просто потому, что кто-то забыл про этот флаг.
    #
    # Разрешение даётся по одному полю, а не по разделу: в «Фолоапе» соседствуют расписание
    # касаний (дело филиала) и еженедельный аудит обучения с реактивацией (наши механизмы).
    branch_admin: bool = False


@dataclass(frozen=True)
class SettingSection:
    icon: str
    title: I18n
    fields: list[SettingField]


def i18n(ru: str, en: str, id_: str) -> I18n:
    return {"ru": ru, "en": en, "id": id_}


def setting(
    key: str, kind: str, default: str, label: I18n, *,
    ph: I18n | None = None, help: I18n | None = None, width: str = "120px",
    hidden: bool = False, choices: list[tuple[str, I18n]] | None = None,
    scope: str = "branch", capability: str | None = None, needs_outreach: bool = False,
    branch_admin: bool = False,
) -> SettingField:
    return SettingField(key, kind, default, label, ph, help, width, hidden, choices, scope,
                        capability, needs_outreach, branch_admin)
