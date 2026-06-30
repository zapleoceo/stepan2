"""Settings page renderer — groups the settings schema into typed, localized fields.

Sections become cards; each field auto-saves on change (HTMX) to /ui/settings/save and
swaps itself back in. Styles are inline so this stays independent of the shared CSS.
Secrets are never echoed back to the browser."""
from __future__ import annotations

import html as _h

from app.modules.settings import schema as S

_INP = (
    "background:#161922;border:1px solid #2a2f3a;border-radius:6px;"
    "padding:.4rem .55rem;color:#e8eef4;font-size:.82rem;outline:none"
)
_LBL = "font-size:.72rem;color:#8b98a5;display:flex;gap:.3rem;align-items:center"
_HELP = "font-size:.67rem;color:#5f6b78;line-height:1.3"
_SEC = (
    "border:1px solid #242a35;border-radius:10px;padding:.85rem .95rem;"
    "margin-bottom:.85rem;background:#141720"
)
_SEC_H = (
    "font-size:.7rem;font-weight:600;letter-spacing:.05em;text-transform:uppercase;"
    "color:#6b7685;margin-bottom:.7rem;display:flex;gap:.45rem;align-items:center"
)
_GRID = "display:flex;flex-wrap:wrap;gap:.7rem 1.1rem"
_FLD = "display:flex;flex-direction:column;gap:.25rem"

_HX = (
    'hx-post="/ui/settings/save" hx-trigger="change" '
    'hx-target="closest .set-fld" hx-swap="outerHTML"'
)


def _ph(f: S.SettingField, lang: str) -> str:
    return S.tr(f.placeholder, lang) if f.placeholder else ""


def _control(f: S.SettingField, value: str, lang: str) -> str:
    hx_vals = f"hx-vals='{{\"key\": \"{f.key}\"}}'"
    style = f"{_INP};width:{f.width}"
    if f.kind == "bool":
        on = "selected" if value == "true" else ""
        off = "selected" if value != "true" else ""
        return (
            f'<select name="value" style="{style}" {_HX} {hx_vals}>'
            f'<option value="true" {on}>On</option>'
            f'<option value="false" {off}>Off</option></select>'
        )
    if f.kind == "secret":
        ph = "•••••• (saved)" if value else _ph(f, lang)
        return (
            f'<input type="password" name="value" value="" placeholder="{_h.escape(ph)}" '
            f'autocomplete="off" style="{style}" {_HX} {hx_vals}>'
        )
    input_type = "number" if f.kind == "int" else "text"
    return (
        f'<input type="{input_type}" name="value" value="{_h.escape(value)}" '
        f'placeholder="{_h.escape(_ph(f, lang))}" style="{style}" {_HX} {hx_vals}>'
    )


def field_html(f: S.SettingField, value: str, lang: str, *, saved: bool = False) -> str:
    """One labelled, auto-saving field block — reused for the panel and the save response."""
    check = '<span style="color:#51cf66">✓</span>' if saved else ""
    help_txt = S.tr(f.help, lang) if f.help else ""
    help_html = f'<div style="{_HELP}">{_h.escape(help_txt)}</div>' if help_txt else ""
    return (
        f'<div class="set-fld" style="{_FLD}">'
        f'<label style="{_LBL}">{_h.escape(S.tr(f.label, lang))} {check}</label>'
        f'{_control(f, value, lang)}{help_html}</div>'
    )


def _section_html(sec: S.SettingSection, values: dict[str, str], lang: str) -> str:
    fields = "".join(
        field_html(f, values.get(f.key, f.default), lang)
        for f in sec.fields if not f.hidden
    )
    return (
        f'<div style="{_SEC}">'
        f'<div style="{_SEC_H}"><i class="{sec.icon}"></i>{_h.escape(S.tr(sec.title, lang))}</div>'
        f'<div style="{_GRID}">{fields}</div></div>'
    )


def settings_form_html(values: dict[str, str], lang: str) -> str:
    """Full settings panel: every schema section rendered with current values."""
    from app.api._i18n import t  # noqa: PLC0415
    title = _h.escape(t("nav.settings"))
    body = "".join(_section_html(sec, values, lang) for sec in S.SCHEMA)
    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body" style="max-width:760px">{body}</div>'
    )
