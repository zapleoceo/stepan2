"""HTML fragments every connector's credential panel shares.

Lives beside the connectors rather than in app/api so one connector is one place: its
spec, its port builder and its panel. Only leaf imports (html, the i18n table) —
nothing under app/connectors may reach back into app/api/_ui_*, which imports the registry.
"""
from __future__ import annotations

import html as _h


def _ch_err(error: str) -> str:
    if not error:
        return ""
    return (
        f'<div style="color:#f03e3e;font-size:.76rem;margin-bottom:.4rem">'
        f'{_h.escape(error)}</div>'
    )


def _ch_step(label: str) -> str:
    return (
        f'<div style="font-size:.68rem;color:#6b7685;letter-spacing:.04em;'
        f'text-transform:uppercase;margin-bottom:.5rem">{_h.escape(label)}</div>'
    )


def _ch_hint(text_: str) -> str:
    return (
        f'<div style="font-size:.72rem;color:#8a94a6;line-height:1.4;margin:-.25rem 0 .6rem">'
        f'{_h.escape(text_)}</div>'
    )
