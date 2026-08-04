"""WhatsApp (Evolution API) credential panel — three plain fields."""
from __future__ import annotations

import html as _h

from app.api._i18n import t

from .ui_bits import _ch_err


def _ch_wa_form(ch_id: int, error: str = "") -> str:
    return (
        f'{_ch_err(error)}'
        f'<form hx-post="/ui/channels/{ch_id}/wa/connect"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_url"))}</label>'
        f'<input class="frm-inp" name="base_url"'
        f' placeholder="https://evolution.example.com"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_inst"))}</label>'
        f'<input class="frm-inp" name="instance"'
        f' placeholder="my-instance"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_key"))}</label>'
        f'<input class="frm-inp" name="api_key"></div>'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.connect"))}</button>'
        f'</form>'
    )
