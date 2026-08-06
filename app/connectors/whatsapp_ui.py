"""WhatsApp pairing panel — number in, QR out, then it watches for the phone to confirm.

The panel used to ask for an Evolution URL, an instance name and an API key: three fields
that only make sense to whoever had already created the instance by hand somewhere else.
Evolution is OUR service on the internal network now, so its address and key come from the
environment and the operator answers the only question they can actually answer — whose
number is this, and are we allowed to write to it."""
from __future__ import annotations

import html as _h

from app.api._i18n import t

from .ui_bits import _ch_err


def wa_instance_name(phone: str) -> str:
    """`wa-6281211120213` — derived, never typed. The digits ARE the identity: two channels
    for one number would fight over the same linked-device slot."""
    return "wa-" + "".join(ch for ch in phone if ch.isdigit())


def _ch_wa_form(ch_id: int, error: str = "") -> str:
    return (
        f'{_ch_err(error)}'
        f'<form hx-post="/ui/channels/{ch_id}/wa/pair"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_phone"))}</label>'
        f'<input class="frm-inp" name="phone" placeholder="+62 811-1185-8519"'
        f' autocomplete="off"></div>'
        f'<label class="frm-grp" style="display:flex;gap:8px;align-items:flex-start">'
        f'<input type="checkbox" name="read_only" value="1" checked style="margin-top:3px">'
        f'<span><b>{_h.escape(t("ch.wa_ro"))}</b><br>'
        f'<span class="frm-hint">{_h.escape(t("ch.wa_ro_hint"))}</span></span></label>'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.wa_pair"))}</button>'
        f'</form>'
    )


def wa_qr_panel(ch_id: int, qr: str, phone: str, error: str = "") -> str:
    """The QR plus what to do with it, polling until the phone confirms the link.

    The steps are spelled out because the person holding the phone is a manager, not an
    operator: an unexplained QR in an admin panel is something people close."""
    steps = "".join(
        f'<li style="margin-bottom:.25rem">{_h.escape(t(k))}</li>'
        for k in ("ch.wa_step1", "ch.wa_step2", "ch.wa_step3")
    )
    img = (
        f'<img src="{_h.escape(qr)}" alt="QR" width="240" height="240"'
        f' style="background:#fff;padding:8px;border-radius:8px">'
        if qr else f'<div class="emp">{_h.escape(t("ch.wa_qr_none"))}</div>'
    )
    return (
        f'{_ch_err(error)}'
        # Polls the pairing state, not the QR: a swap on every tick would replace the image
        # mid-scan and the phone would never finish reading it.
        f'<div hx-get="/ui/channels/{ch_id}/wa/state" hx-trigger="every 3s"'
        f' hx-target="#ch-form" hx-swap="innerHTML">'
        f'<div style="font-size:.8rem;color:#9fb0c0;margin-bottom:.5rem">'
        f'{_h.escape(t("ch.wa_pairing_for"))} <b>{_h.escape(phone)}</b></div>'
        f'<ol style="font-size:.78rem;color:#9fb0c0;padding-left:1.1rem;margin:0 0 .7rem">'
        f'{steps}</ol>'
        f'{img}'
        f'<div style="margin-top:.7rem;display:flex;gap:.4rem">'
        f'<button class="btn-sm" hx-post="/ui/channels/{ch_id}/wa/pair"'
        f' hx-target="#ch-form" hx-swap="innerHTML">'
        f'{_h.escape(t("ch.wa_qr_new"))}</button>'
        f'<button class="btn-sm" hx-post="/ui/channels/{ch_id}/wa/cancel"'
        f' hx-target="#ch-form" hx-swap="innerHTML">'
        f'{_h.escape(t("ch.cancel"))}</button></div>'
        f'</div>'
    )
