"""Meta Business credential panel — OAuth button first, manual token paste behind it."""
from __future__ import annotations

import html as _h

from app.api._i18n import t

from .ui_bits import _ch_err


def _ch_meta_form(ch_id: int, error: str = "") -> str:
    # The button first, the paste form second: a client cannot obtain a System User token, and
    # Meta's App Review needs a recording of someone granting the permissions — which only the
    # consent screen behind this button can show. The manual form stays for our own channels
    # and as the fallback when the app is not configured yet.
    return (
        f'{_ch_err(error)}'
        f'<a class="btn-sm btn-p" href="/connect/meta/{ch_id}/start" target="_blank"'
        f' rel="noopener" style="display:inline-block;text-decoration:none;margin-bottom:.6rem">'
        f'{_h.escape(t("ch.connect_fb"))}</a>'
        f'<div style="font-size:.7rem;color:#8a94a6;margin:-.35rem 0 .8rem">'
        f'{_h.escape(t("ch.connect_fb_hint"))}</div>'
        f'<details style="margin-bottom:.6rem"><summary style="cursor:pointer;'
        f'font-size:.75rem;color:#8a94a6">{_h.escape(t("ch.connect_manual"))}</summary>'
        f'<div style="height:.5rem"></div>'
        f'<form hx-post="/ui/channels/{ch_id}/meta/connect"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">Платформа</label>'
        f'<select class="act-sel" name="platform" style="width:100%;padding:.3rem .35rem">'
        f'<option value="facebook_page">Facebook Page (Messenger)</option>'
        f'<option value="instagram_graph">Instagram Graph API</option>'
        f'</select></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.page_id"))}</label>'
        f'<input class="frm-inp" name="page_id" placeholder="123456789"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.token"))}'
        f' <span style="color:#4a5568;font-size:.7rem">(Graph API)</span></label>'
        f'<input class="frm-inp" name="token" placeholder="EAAxx...">'
        f'<div style="font-size:.7rem;color:#8a94a6;margin-top:.2rem">'
        f'Пусто + Facebook Page = токен выведется из System User токена коннектора '
        f'(настройки филиала → meta_system_user_token)</div></div>'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.connect"))}</button>'
        f'</form></details>'
    )
