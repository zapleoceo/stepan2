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
    scope: str = "branch",
) -> SettingField:
    return SettingField(key, kind, default, label, ph, help, width, hidden, choices, scope)
