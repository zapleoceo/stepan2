"""Панель коннектора CRM — оператор вводит только то, что ему выдали в CRM.

Адрес sender и токен здесь не спрашиваются: сервер один на всю установку и живёт в
окружении, как и Evolution у соседнего коннектора. Оператор отвечает на единственный вопрос,
на который может ответить, — какие числа его филиала в системе sender.

Три поля, а не два: `project` (буквенный alias) нужен инструменту отправки, `project_id`
(число) приходит в колбеке. Выводить одно из другого нечем — соответствия у нас нет, и
догадка здесь тихо увела бы ответ в чужой филиал.
"""
from __future__ import annotations

import html as _h

from app.api._i18n import t
from app.modules.sender.tenant import KEY_BRANCH_ID, KEY_PROJECT, KEY_PROJECT_ID

from .ui_bits import _ch_err, _ch_hint


def _field(label: str, name: str, value: str, placeholder: str) -> str:
    return (
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(label)}</label>'
        f'<input class="frm-inp" name="{_h.escape(name)}" value="{_h.escape(value)}"'
        f' placeholder="{_h.escape(placeholder)}" autocomplete="off"></div>'
    )


def _ch_crm_sender_form(ch_id: int, error: str = "", get: object = None) -> str:
    read = get if callable(get) else (lambda k, d="": d)
    return (
        f'{_ch_err(error)}'
        f'{_ch_hint(t("ch.crm_sender_hint"))}'
        f'<form hx-post="/ui/channels/{ch_id}/save"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'{_field(t("ch.crm_sender_project"), KEY_PROJECT,
                  str(read(KEY_PROJECT, "") or ""), "crm")}'
        f'{_field(t("ch.crm_sender_project_id"), KEY_PROJECT_ID,
                  str(read(KEY_PROJECT_ID, "") or ""), "6")}'
        f'{_field(t("ch.crm_sender_branch_id"), KEY_BRANCH_ID,
                  str(read(KEY_BRANCH_ID, "") or ""), "435")}'
        f'<button type="submit" class="btn-sm btn-p">{_h.escape(t("ch.save"))}</button>'
        f'</form>'
    )
