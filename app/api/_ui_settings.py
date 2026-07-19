"""Settings page renderer — groups the settings schema into typed, localized fields.

Sections become cards; each field is a compact row (label + right-sized control) that
auto-saves on change (HTMX) to /ui/settings/save and swaps itself back in. Styles are
inline so this stays independent of the shared CSS. Secrets are never echoed back."""
from __future__ import annotations

import html as _h

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


def _is_wide(f: S.SettingField) -> bool:
    """A control too wide to sit beside its label in a ~300px card — stack it, else the label
    column collapses to one char per line (the CAPI-token / CRM-webhook mess)."""
    w = (f.width or "").strip()
    if w.endswith("%"):
        return True
    if w.endswith("px"):
        try:
            return int(w[:-2]) >= 180
        except ValueError:
            return False
    return False


def _control(
    f: S.SettingField, value: str, lang: str, width: str | None = None,
    channel_id: int | None = None,
) -> str:
    # channel_id present → the field autosaves to the connector tier, else the branch tier.
    _cid = f', "channel_id": "{channel_id}"' if channel_id is not None else ""
    hx_vals = f"hx-vals='{{\"key\": \"{f.key}\"{_cid}}}'"
    style = f"{_INP};width:{width or f.width}"
    if f.kind == "multi":  # checkbox group → a comma-list saved via a hidden input
        selected = {t.strip().lower() for t in (value or "").split(",") if t.strip()}
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
    if f.choices:  # fixed-option dropdown (e.g. reply_routing)
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


def _usage_badge(used: int, cap: int, lang: str) -> str:
    """Live 'used/cap this window' indicator for a rate-limit field — colour banded by how
    close to the ceiling it is (not a hardcoded numeric threshold for the FIELD itself, just
    a generic proximity-to-100% band). `used`/`cap` are computed fresh by the caller from
    real sent counts each request — never baked in here."""
    from app.api._i18n import t  # noqa: PLC0415
    if cap <= 0:  # 0 = unlimited, no ceiling to show usage against
        return ""
    pct = used / cap * 100
    color = "#ff6b6b" if pct >= 100 else "#ffa94d" if pct >= 80 else "#51cf66"
    warn = (f' · {_h.escape(t("set.cap_reached"))}' if pct >= 100 else "")
    return (
        f'<div style="font-size:.68rem;color:{color};margin-top:.15rem">'
        f'{used}/{cap} ({pct:.0f}%){warn}</div>'
    )


def field_html(
    f: S.SettingField, value: str, lang: str, *, saved: bool = False,
    cap_usage: dict[str, tuple[int, int]] | None = None,
    channel_id: int | None = None,
) -> str:
    """One compact auto-saving row — reused for the panel and the save response. `cap_usage`
    (e.g. {"hourly_cap": (used, cap)}) adds a live usage badge under a rate-limit field,
    computed fresh by the route each request — this function never hardcodes a threshold.
    `channel_id` routes the autosave to the connector tier instead of the branch tier."""
    check = ' <span style="color:#51cf66">✓</span>' if saved else ""
    label_txt = S.tr(f.label, lang)
    help_txt = S.tr(f.help, lang) if f.help else ""
    help_html = f'<div style="{_HELP}">{_h.escape(help_txt)}</div>' if help_txt else ""
    usage = cap_usage.get(f.key) if cap_usage else None
    if usage is not None:
        help_html += _usage_badge(usage[0], usage[1], lang)
    label = f'<div style="{_LBL}">{_h.escape(label_txt)}{check}</div>'
    # Help-mode (the ? button) tip: prefer the field's own help text, fall back to its label,
    # so every setting lights up and explains itself when help mode is on.
    tip = (f"{label_txt} — {help_txt}" if help_txt else label_txt).strip(" —")
    dh = f' data-help="{_h.escape(tip)}"' if tip else ""
    if f.kind == "multi" or _is_wide(f):
        # A wide control (checkbox group, token, URL) can't share the row — the label collapses
        # to one word per line. Stack it: label + help on top, control full-width below.
        ctrl = (_control(f, value, lang, channel_id=channel_id) if f.kind == "multi"
                else _control(f, value, lang, "100%", channel_id=channel_id))
        return (
            f'<div class="set-fld"{dh} style="{_ROW};display:block">'
            f'{label}{help_html}'
            f'<div style="margin-top:.5rem">{ctrl}</div>'
            f'</div>'
        )
    return (
        f'<div class="set-fld"{dh} style="{_ROW}">'
        f'<div style="min-width:0">{label}{help_html}</div>'
        f'<div style="flex-shrink:0">{_control(f, value, lang, channel_id=channel_id)}</div>'
        f'</div>'
    )


def _section_html(
    sec: S.SettingSection, values: dict[str, str], lang: str,
    cap_usage: dict[str, tuple[int, int]] | None = None,
    channel_id: int | None = None,
) -> str:
    rows = "".join(
        field_html(f, values.get(f.key, f.default), lang, cap_usage=cap_usage,
                   channel_id=channel_id)
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


def settings_form_html(
    values: dict[str, str], lang: str,
    cap_usage: dict[str, tuple[int, int]] | None = None,
) -> str:
    """Full settings panel: every schema section rendered with current values. `cap_usage`
    (e.g. {"hourly_cap": (used, cap)}) shows a live badge under the anti-ban limit fields —
    computed fresh by the route from real sent counts each request, never hardcoded here.

    No Save button by design — every field auto-saves on change (see _HX). The autosave
    label next to the title says so up front, since a manager used to a Save button won't
    find one here otherwise."""
    from app.api._i18n import t  # noqa: PLC0415
    title = _h.escape(t("nav.settings"))
    autosave = _h.escape(t("set.autosave"))
    # Branch panel shows only branch-scoped settings; connector-scoped ones (follow-ups,
    # anti-ban caps, phone code, Meta) live in the per-channel editor (Филиалы → канал).
    body = "".join(
        _section_html(sec, values, lang, cap_usage) for sec in S.sections_for_scope("branch"))
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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.settings"))}">'
        f'{title}</span>'
        f'<span style="font-size:.68rem;color:#5f6b78;margin-left:.6rem">'
        f'· {autosave}</span></div>'
        f'<div class="pnl-body" style="max-width:1400px">'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));'
        f'gap:.8rem;align-items:start">{body}</div></div>'
    )


def _field_for_kind(f: S.SettingField, kind: str) -> bool:
    """A connector-scope field shows in a channel's editor unless it's Meta-specific (key
    prefixed meta_/fb_), which only makes sense on a meta_business connector."""
    if f.key.startswith(("meta_", "fb_")):
        return kind == "meta_business"
    return True


def channel_settings_html(
    kind: str, values: dict[str, str], lang: str, channel_id: int,
    cap_usage: dict[str, tuple[int, int]] | None = None,
) -> str:
    """Per-connector settings block for the channel editor: the scope='channel' sections,
    each field autosaving to app_setting(branch_id, channel_id, key). Meta fields are hidden
    on non-Meta connectors. `cap_usage` shows the per-channel live anti-ban usage badge.
    Reuses the same field renderer as the branch panel (DRY)."""
    cards = []
    for sec in S.sections_for_scope("channel"):
        kept = S.SettingSection(
            sec.icon, sec.title,
            [f for f in sec.fields if not f.hidden and _field_for_kind(f, kind)])
        if kept.fields:
            cards.append(_section_html(
                kept, values, lang, cap_usage=cap_usage, channel_id=channel_id))
    if not cards:
        return ""
    return (
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));'
        f'gap:.7rem;align-items:start;margin-top:.6rem">{"".join(cards)}</div>'
    )
