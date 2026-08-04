"""Website credential panel — there are no credentials to collect.

Every other connector's panel takes a token or a login. This one exists because the channel
editor asks each spec for a panel, and answering "Unknown channel kind" would read as a bug.
It states what the channel is and links to the page it serves.
"""
from __future__ import annotations

import html as _h

from app.api._i18n import t

from .ui_bits import _ch_err, _ch_hint, _ch_step


def _ch_web_form(ch_id: int, error: str = "") -> str:
    return (
        f'{_ch_err(error)}'
        f'{_ch_step(t("ch.web_step"))}'
        f'{_ch_hint(t("ch.web_hint"))}'
        f'<a class="btn-sm btn-p" id="ch-web-{ch_id}" href="/" target="_blank"'
        f' rel="noopener">{_h.escape(t("ch.web_open"))}</a>'
    )
