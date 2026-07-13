"""Persona-library panels: the browsable library grid (name, version, author, adoption,
favorite, use-for-branch, contact author) and one persona's detail with per-section branch
addendum editors. Persona content is read-only here; only the author updates the core."""
# ruff: noqa: E501 — inline HTML string; long lines are inherent
from __future__ import annotations

import html as _h

from app.adapters.db.models import Persona
from app.api._i18n import t
from app.modules.persona.service import sections


def _contact_link(persona: Persona) -> str:
    c = (persona.author_contact or "").strip()
    who = _h.escape(persona.author_name or "author")
    if not c:
        return f'<span class="pa-author">{who}</span>'
    href = c if c.startswith(("http://", "https://", "mailto:")) else f"https://t.me/{c.lstrip('@')}"
    return (f'<span class="pa-author">{who}</span> '
            f'<a class="pa-contact" href="{_h.escape(href)}" target="_blank" rel="noreferrer"'
            f' data-help="{_h.escape(t("pl.contact_h"))}">'
            f'<i class="fa-regular fa-paper-plane"></i> {_h.escape(t("pl.contact"))}</a>')


def _star(pid: int, on: bool, can_write: bool) -> str:
    if not can_write:
        icon = "fa-solid fa-star" if on else "fa-regular fa-star"
        return f'<span class="pa-star{" on" if on else ""}"><i class="{icon}"></i></span>'
    icon = "fa-solid fa-star" if on else "fa-regular fa-star"
    return (f'<button class="pa-star{" on" if on else ""}"'
            f' hx-post="/ui/personas/{pid}/favorite" hx-target="#main" hx-swap="innerHTML"'
            f' data-help="{_h.escape(t("pl.fav_h"))}" title="{_h.escape(t("pl.fav"))}">'
            f'<i class="{icon}"></i></button>')


def _use_btn(pid: int, active: bool, can_write: bool) -> str:
    if active:
        return f'<span class="pa-use active"><i class="fa-solid fa-check"></i> {_h.escape(t("pl.in_use"))}</span>'
    if not can_write:
        return ""
    return (f'<button class="pa-use" hx-post="/ui/personas/{pid}/use"'
            f' hx-target="#main" hx-swap="innerHTML"'
            f' data-help="{_h.escape(t("pl.use_h"))}">{_h.escape(t("pl.use"))}</button>')


def _card(p: Persona, adopt: tuple[int, int], active: bool, fav: bool, can_write: bool) -> str:
    used, favs = adopt
    tag = _h.escape(f"{p.lang.upper()} · {p.country}".strip(" ·")) if (p.lang or p.country) else ""
    return (
        f'<div class="pa-card{" active" if active else ""}">'
        f'<div class="pa-top">'
        f'<span class="pa-nm">{_h.escape(p.name)}</span>'
        f'<span class="pa-ver">v{_h.escape(p.version)}</span>'
        f'{_star(p.id, fav, can_write)}</div>'
        f'<p class="pa-sum">{_h.escape(p.summary)}</p>'
        f'<div class="pa-meta"><span class="pa-stat" data-help="{_h.escape(t("pl.stat_h"))}">'
        f'{used} {_h.escape(t("pl.branches"))} · {favs} ★</span>'
        + (f'<span class="pa-lang">{tag}</span>' if tag else "")
        + '</div>'
        f'<div class="pa-by">{_h.escape(t("pl.by"))} {_contact_link(p)}</div>'
        f'<div class="pa-acts">'
        f'{_use_btn(p.id, active, can_write)}'
        f'<a class="pa-open" hx-get="/ui/personas/{p.id}" hx-target="#main"'
        f' hx-push-url="/ui/personas/{p.id}">{_h.escape(t("pl.open"))}</a>'
        f'</div></div>'
    )


def personas_panel_html(
    personas: list[Persona], adopt: dict[int, tuple[int, int]],
    active_id: int | None, fav_ids: set[int], can_write: bool, active_name: str,
) -> str:
    title = _h.escape(t("nav.personas"))
    intro = _h.escape(t("pl.intro"))
    using = (
        f'<div class="pa-using">{_h.escape(t("pl.using"))} <b>{_h.escape(active_name)}</b></div>'
        if active_name else
        f'<div class="pa-using draft">{_h.escape(t("pl.draft"))}</div>'
    )
    cards = "".join(
        _card(p, adopt.get(p.id, (0, 0)), p.id == active_id, p.id in fav_ids, can_write)
        for p in personas
    ) or f'<div class="emp">{_h.escape(t("pl.empty"))}</div>'
    import_btn = (
        f'<form class="pa-imp" hx-post="/ui/personas/import" hx-target="#main"'
        f' hx-swap="innerHTML" data-help="{_h.escape(t("pl.import_h"))}">'
        f'<input name="changelog" maxlength="300"'
        f' placeholder="{_h.escape(t("pl.changed_ph"))}">'
        f'<button class="btn-sm btn-p" type="submit">{_h.escape(t("pl.import"))}</button></form>'
        if can_write else ""
    )
    return (
        f'<style>{_PERSONA_CSS}</style>'
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("pl.intro"))}">{title}</span>'
        f'{import_btn}</div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{intro}</div>'
        f'{using}'
        f'<div class="pa-grid">{cards}</div>'
        f'<p class="mnote" style="margin-top:1rem">{_h.escape(t("pl.stats_note"))}</p>'
        f'</div>'
    )


def _section_block(pid: int, title: str, slug: str, body: str, add: str, can_write: bool) -> str:
    editor = (
        f'<form class="pa-add" hx-post="/ui/personas/{pid}/addendum" hx-target="#main"'
        f' hx-swap="innerHTML"><input type="hidden" name="section" value="{_h.escape(slug)}">'
        f'<label class="pa-add-l" data-help="{_h.escape(t("pl.add_h"))}">{_h.escape(t("pl.add_label"))}</label>'
        f'<textarea name="text" rows="2" placeholder="{_h.escape(t("pl.add_ph"))}">{_h.escape(add)}</textarea>'
        f'<button class="btn-sm btn-p" type="submit">{_h.escape(t("pl.save"))}</button></form>'
        if can_write else
        (f'<div class="pa-add-ro">{_h.escape(t("pl.add_label"))}: '
         f'{_h.escape(add) if add else _h.escape(t("pl.add_none"))}</div>')
    )
    return (
        f'<div class="pa-sec">'
        f'<div class="pa-sec-h">{_h.escape(title)}</div>'
        f'<div class="pa-sec-b">{_h.escape(body)}</div>'
        f'{editor}</div>'
    )


def _fmt_day(dt: object) -> str:
    return dt.strftime("%d %b %Y") if hasattr(dt, "strftime") else ""


def _history_html(history: list[Persona]) -> str:
    if not history:
        return ""
    rows = "".join(
        f'<div class="pa-hist-row">'
        f'<span class="pa-hist-v">v{_h.escape(v.version)}</span>'
        f'<span class="pa-hist-d">{_h.escape(_fmt_day(v.created_at))}</span>'
        f'<span class="pa-hist-a">{_h.escape(v.author_name or "—")}</span>'
        f'<div class="pa-hist-note">{_h.escape((v.changelog or "").strip() or "—")}</div>'
        f'</div>'
        for v in history
    )
    return (
        f'<div class="pa-hist"><div class="pa-hist-h">{_h.escape(t("pl.history"))}</div>{rows}</div>'
    )


def persona_detail_html(
    p: Persona, addendum: dict[str, str], active: bool, fav: bool, can_write: bool,
    history: list[Persona] | None = None, stat: tuple[int, int] = (0, 0),
) -> str:
    secs = "".join(
        _section_block(p.id, tt, sl, body, addendum.get(sl, ""), can_write)
        for tt, sl, body in sections(p.content)
    )
    used, favs = stat
    stats_line = (
        f'<span class="pa-stat" data-help="{_h.escape(t("pl.stat_h"))}">'
        f'{used} {_h.escape(t("pl.branches"))} · {favs} ★</span>'
    )
    return (
        f'<style>{_PERSONA_CSS}</style>'
        f'<div class="ch">'
        f'<button class="act-btn" hx-get="/ui/personas" hx-target="#main"'
        f' hx-push-url="/ui/personas">{_h.escape(t("pl.back"))}</button>'
        f'<span class="ch-n" style="margin-left:.6rem">{_h.escape(p.name)}</span>'
        f'<span class="pa-ver" style="margin-left:.4rem">v{_h.escape(p.version)}</span>'
        f'<div class="ch-acts">{_star(p.id, fav, can_write)}{_use_btn(p.id, active, can_write)}</div>'
        f'</div>'
        f'<div class="pnl-body">'
        f'<div class="pa-by" style="margin-bottom:.6rem">{_h.escape(t("pl.by"))} '
        f'{_contact_link(p)} &nbsp; {stats_line}</div>'
        f'{_history_html(history or [])}'
        f'<div class="hint">{_h.escape(t("pl.detail_intro"))}</div>'
        f'{secs}'
        f'<p class="mnote" style="margin-top:1rem">{_h.escape(t("pl.readonly_note"))}</p>'
        f'</div>'
    )


_PERSONA_CSS = (
    ".pa-using{font-size:.85rem;color:#9aa3b2;margin:.6rem 0 1rem}"
    ".pa-using b{color:#e8eef4}.pa-using.draft{color:#e2b33d}"
    ".pa-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem}"
    ".pa-card{background:#0e1014;border:1px solid #20232b;border-radius:14px;padding:1.1rem}"
    ".pa-card.active{border-color:#ff5c35;background:linear-gradient(180deg,rgba(255,92,53,.08),#0e1014 60%)}"
    ".pa-top{display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem}"
    ".pa-nm{font-weight:600;color:#e8eef4}"
    ".pa-ver{font-family:monospace;font-size:.64rem;color:#666e7d;border:1px solid #2b2f38;border-radius:6px;padding:.1rem .4rem}"
    ".pa-star{margin-left:auto;background:none;border:none;color:#5f6b78;cursor:pointer;font-size:.95rem}"
    ".pa-star.on{color:#e2b33d}"
    ".pa-sum{font-size:.86rem;color:#9aa3b2;line-height:1.5;margin-bottom:.7rem}"
    ".pa-meta{display:flex;gap:.5rem;flex-wrap:wrap;font-size:.7rem;color:#666e7d;margin-bottom:.5rem}"
    ".pa-stat{border:1px solid #20232b;border-radius:6px;padding:.12rem .45rem}"
    ".pa-lang{font-family:monospace}"
    ".pa-by{font-size:.76rem;color:#666e7d}.pa-author{color:#9aa3b2}"
    ".pa-contact{color:#4da3ff;margin-left:.2rem}"
    ".pa-acts{display:flex;gap:.5rem;align-items:center;margin-top:.9rem}"
    ".pa-use{background:#f2f4f7;color:#000;border:none;border-radius:8px;padding:.4rem .8rem;font-size:.8rem;font-weight:600;cursor:pointer}"
    ".pa-use.active{background:rgba(76,195,138,.14);color:#4cc38a;font-weight:600}"
    ".pa-open{color:#9aa3b2;font-size:.8rem;border:1px solid #2b2f38;border-radius:8px;padding:.4rem .7rem;cursor:pointer}"
    ".pa-open:hover{color:#e8eef4}"
    ".pa-sec{border-top:1px solid #20232b;padding:1.1rem 0}"
    ".pa-sec-h{font-family:var(--disp,inherit);font-weight:600;color:#e8eef4;margin-bottom:.4rem}"
    ".pa-sec-b{font-size:.88rem;color:#9aa3b2;line-height:1.55;white-space:pre-wrap}"
    ".pa-add{display:flex;flex-direction:column;gap:.4rem;margin-top:.7rem}"
    ".pa-add-l{font-size:.72rem;color:#e2b33d;text-transform:uppercase;letter-spacing:.06em}"
    ".pa-add textarea{background:#1a1f2b;border:1px solid #2d3748;border-radius:8px;color:#c9d1d9;padding:.5rem;font-size:.85rem;font-family:inherit;resize:vertical}"
    ".pa-add-ro{font-size:.8rem;color:#666e7d;margin-top:.6rem}"
    ".pa-add button{align-self:flex-start}"
    ".pa-imp{display:flex;gap:.4rem;align-items:center;margin-left:auto}"
    ".pa-imp input{background:#1a1f2b;border:1px solid #2d3748;border-radius:8px;color:#c9d1d9;"
    "font-size:.78rem;padding:.32rem .5rem;width:220px}"
    ".pa-hist{border:1px solid #20232b;border-radius:12px;padding:.9rem 1rem;margin:1rem 0}"
    ".pa-hist-h{font-family:var(--disp,inherit);font-weight:600;color:#e8eef4;font-size:.9rem;"
    "margin-bottom:.6rem}"
    ".pa-hist-row{display:grid;grid-template-columns:auto auto 1fr;gap:.6rem;align-items:baseline;"
    "padding:.4rem 0;border-top:1px solid #20232b}"
    ".pa-hist-row:first-of-type{border-top:none}"
    ".pa-hist-v{font-family:monospace;font-size:.7rem;color:#ff5c35}"
    ".pa-hist-d{font-size:.7rem;color:#666e7d}"
    ".pa-hist-a{font-size:.7rem;color:#9aa3b2}"
    ".pa-hist-note{grid-column:1/-1;font-size:.85rem;color:#c9d1d9;line-height:1.5}"
)
