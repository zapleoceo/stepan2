"""Settings page renderer — groups the settings schema into typed, localized fields.

Sections become cards; each field is a compact row (label + right-sized control) that
auto-saves on change (HTMX) to /ui/settings/save and swaps itself back in. Styles are
inline so this stays independent of the shared CSS. Secrets are never echoed back."""
from __future__ import annotations

import html as _h

from app.modules.conversation.routing import parse_smart_stages
from app.modules.settings import schema as S

_INP = (
    "background:#0f1117;border:1px solid #2a2f3a;border-radius:6px;"
    "padding:.35rem .5rem;color:#e8eef4;font-size:.82rem;outline:none"
)
_ROW = (
    "display:flex;align-items:center;justify-content:space-between;gap:1rem;"
    "padding:.5rem .85rem;border-top:1px solid rgba(255,255,255,.035)"
)
_LBL = "font-size:.8rem;color:#cdd6e0"
_HELP = "font-size:.67rem;color:#5f6b78;margin-top:.12rem;line-height:1.25"
_CARD = (
    "border:1px solid #242a35;border-radius:10px;background:#141720;"
    "margin-bottom:.8rem;overflow:hidden"
)
_CARD_H = (
    "display:flex;gap:.5rem;align-items:center;padding:.55rem .85rem;background:#171b26;"
    "color:#8b98a5;font-size:.7rem;font-weight:600;letter-spacing:.05em;text-transform:uppercase"
)

_HX = (
    'hx-post="/ui/settings/save" hx-trigger="change" '
    'hx-target="closest .set-fld" hx-swap="outerHTML"'
)


def _ph(f: S.SettingField, lang: str) -> str:
    return S.tr(f.placeholder, lang) if f.placeholder else ""


def _control(f: S.SettingField, value: str, lang: str) -> str:
    hx_vals = f"hx-vals='{{\"key\": \"{f.key}\"}}'"
    style = f"{_INP};width:{f.width}"
    if f.kind == "multi":  # checkbox group → a comma-list saved via a hidden input
        selected = parse_smart_stages(value)  # effective set, so UI always matches behaviour
        boxes = "".join(
            f'<label style="display:inline-flex;align-items:center;gap:.25rem;font-size:.72rem;'
            f'color:#cdd6e0;cursor:pointer;white-space:nowrap">'
            f'<input type="checkbox" value="{_h.escape(v)}" '
            f'{"checked" if v in selected else ""} onchange="multiSave(this)">'
            f'{_h.escape(S.tr(lbl, lang))}</label>'
            for v, lbl in (f.choices or [])
        )
        hidden = (f'<input type="hidden" name="value" value="{_h.escape(value)}" '
                  f'{_HX} {hx_vals}>')
        return (
            f'<div class="multi-grp" style="display:flex;flex-wrap:wrap;gap:.4rem .8rem;'
            f'justify-content:flex-start">{boxes}{hidden}</div>'
        )
    if f.choices:  # fixed-option dropdown (e.g. knowledge_backend)
        opts = "".join(
            f'<option value="{_h.escape(v)}" {"selected" if v == value else ""}>'
            f'{_h.escape(S.tr(lbl, lang))}</option>'
            for v, lbl in f.choices
        )
        return f'<select name="value" style="{style}" {_HX} {hx_vals}>{opts}</select>'
    if f.kind == "bool":
        on = "selected" if value == "true" else ""
        off = "selected" if value != "true" else ""
        return (
            f'<select name="value" style="{_INP};width:{f.width}" {_HX} {hx_vals}>'
            f'<option value="true" {on}>On</option>'
            f'<option value="false" {off}>Off</option></select>'
        )
    if f.kind == "secret":
        ph = "•••••• saved" if value else _ph(f, lang)
        return (
            f'<input type="password" name="value" value="" placeholder="{_h.escape(ph)}" '
            f'autocomplete="off" style="{style}" {_HX} {hx_vals}>'
        )
    input_type = "number" if f.kind == "int" else "text"
    align = ";text-align:right" if f.kind == "int" else ""
    return (
        f'<input type="{input_type}" name="value" value="{_h.escape(value)}" '
        f'placeholder="{_h.escape(_ph(f, lang))}" style="{style}{align}" {_HX} {hx_vals}>'
    )


def field_html(f: S.SettingField, value: str, lang: str, *, saved: bool = False) -> str:
    """One compact auto-saving row — reused for the panel and the save response."""
    check = ' <span style="color:#51cf66">✓</span>' if saved else ""
    help_txt = S.tr(f.help, lang) if f.help else ""
    help_html = f'<div style="{_HELP}">{_h.escape(help_txt)}</div>' if help_txt else ""
    label = f'<div style="{_LBL}">{_h.escape(S.tr(f.label, lang))}{check}</div>'
    if f.kind == "multi":
        # A wide control (checkbox group) can't share the row — the label collapses to one word
        # per line. Stack it: label + help on top, control full-width below.
        return (
            f'<div class="set-fld" style="{_ROW};display:block">'
            f'{label}{help_html}'
            f'<div style="margin-top:.5rem">{_control(f, value, lang)}</div>'
            f'</div>'
        )
    return (
        f'<div class="set-fld" style="{_ROW}">'
        f'<div style="min-width:0">{label}{help_html}</div>'
        f'<div style="flex-shrink:0">{_control(f, value, lang)}</div>'
        f'</div>'
    )


def _section_html(sec: S.SettingSection, values: dict[str, str], lang: str) -> str:
    rows = "".join(
        field_html(f, values.get(f.key, f.default), lang)
        for f in sec.fields if not f.hidden
    )
    if not rows:  # a section with only hidden fields — don't render an empty card
        return ""
    return (
        f'<div style="{_CARD}">'
        f'<div style="{_CARD_H}"><i class="{sec.icon}"></i>'
        f'{_h.escape(S.tr(sec.title, lang))}</div>'
        f'{rows}</div>'
    )


def settings_form_html(values: dict[str, str], lang: str) -> str:
    """Full settings panel: every schema section rendered with current values.

    No Save button by design — every field auto-saves on change (see _HX). The autosave
    label next to the title says so up front, since a manager used to a Save button won't
    find one here otherwise."""
    from app.api._i18n import t  # noqa: PLC0415
    title = _h.escape(t("nav.settings"))
    autosave = _h.escape(t("set.autosave"))
    body = "".join(_section_html(sec, values, lang) for sec in S.SCHEMA)
    # checkbox group → recompute the comma-list into the hidden input, then fire its autosave
    script = (
        '<script>function multiSave(cb){var g=cb.closest(".multi-grp");'
        'var v=[].slice.call(g.querySelectorAll("input[type=checkbox]:checked"))'
        '.map(function(c){return c.value}).join(",");'
        'var h=g.querySelector("input[type=hidden][name=value]");h.value=v;'
        'if(window.htmx)htmx.trigger(h,"change")}</script>'
    )
    return (
        f'{script}'
        f'<div class="ch"><span class="ch-n">{title}</span>'
        f'<span style="font-size:.68rem;color:#5f6b78;margin-left:.6rem">'
        f'· {autosave}</span></div>'
        f'<div class="pnl-body" style="max-width:1400px">'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));'
        f'gap:.8rem;align-items:start">{body}</div></div>'
    )
