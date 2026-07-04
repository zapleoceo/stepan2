"""Knowledge-base UI: tabbed tree sidebar (Persona | Products), a section editor with
localized placeholder hints, and the edit-history diff view. Navigation is sidebar-only —
clicking a doc/product loads it into #main."""
from __future__ import annotations

import html as _h

from app.modules.knowledge.canonical import canon, loc
from app.modules.knowledge.canonical_docs import CATEGORIES
from app.modules.knowledge.sections import split_sections

from ._i18n import current_lang, t

_CAT_ORDER = ("persona", "playbook", "reference")
_OTHER = "other"


def _cat_label(cat: str, lang: str) -> str:
    if cat in CATEGORIES:
        return loc(CATEGORIES[cat], lang)
    return {"ru": "Прочее", "en": "Other", "id": "Lainnya"}.get(lang, "Other")


def _tabs(active: str) -> str:
    def tab(key: str, url: str, label: str) -> str:
        cls = "kb-tab on" if key == active else "kb-tab"
        return (f'<button class="{cls}" hx-get="{url}" hx-target="#kb-side"'
                f' hx-swap="innerHTML">{_h.escape(label)}</button>')
    reindex = (
        f'<button class="kb-reix" title="{_h.escape(t("kb.reindex"))}"'
        f' hx-post="/ui/knowledge/reindex" hx-target="#kb-reix-out" hx-swap="innerHTML">⟳</button>'
    )
    return (
        '<div class="kb-tabs">'
        f'{tab("persona", "/ui/knowledge/tree", t("kb.tab_persona"))}'
        f'{tab("products", "/ui/knowledge/products", t("nav.products"))}'
        f'{reindex}</div><div id="kb-reix-out"></div>'
    )


def kb_tree_html(docs: list, active_id: int | None = None) -> str:
    """Persona-tab sidebar: docs grouped by category into collapsible groups."""
    lang = current_lang()
    groups: dict[str, list] = {}
    for d in docs:  # (id, slug, title, content, category, sort_order)
        cat = d[4] if d[4] in CATEGORIES else _OTHER
        groups.setdefault(cat, []).append(d)
    out = [_tabs("persona")]
    for cat in (*_CAT_ORDER, _OTHER):
        rows = groups.get(cat)
        if not rows:
            continue
        rows.sort(key=lambda r: (r[5] or 0, str(r[1])))
        items = "".join(_tree_item(d, active_id) for d in rows)
        out.append(
            f'<details class="kb-grp" open><summary>{_h.escape(_cat_label(cat, lang))}'
            f'</summary>{items}</details>')
    if len(out) == 1:
        out.append('<div class="emp">—</div>')
    return f'<div id="kb-side">{"".join(out)}</div>'


def _tree_item(d: object, active_id: int | None) -> str:
    doc_id, slug, title = d[0], str(d[1]), str(d[2] or d[1])
    cls = "ti on" if doc_id == active_id else "ti"
    return (
        f'<a class="{cls}" hx-get="/ui/knowledge/{doc_id}/edit" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/{doc_id}/edit" onclick="setOn(this)">'
        f'<div class="ti-t"><span class="ti-n">{_h.escape(title)}</span></div>'
        f'<div class="ti-p">{_h.escape(slug)}</div></a>'
    )


def kb_products_html(products: list, active_id: int | None = None) -> str:
    """Products-tab sidebar: flat product list."""
    create = (
        f'<a class="btn-sm btn-p" hx-get="/ui/products/new" hx-target="#main"'
        f' hx-push-url="/ui/products/new" style="text-decoration:none;margin:.3rem">'
        f'+ {_h.escape(t("prod.create"))}</a>')
    items = "".join(
        f'<a class="ti{" on" if p[0] == active_id else ""}"'
        f' hx-get="/ui/products/{p[0]}/edit" hx-target="#main"'
        f' hx-push-url="/ui/products/{p[0]}/edit" onclick="setOn(this)">'
        f'<div class="ti-t"><span class="ti-n">{_h.escape(str(p[2]))}</span>'
        f'{"" if p[3] else " <span class=ti-off>✗</span>"}</div>'
        f'<div class="ti-p">{_h.escape(str(p[1]))}</div></a>'
        for p in products  # (id, slug, title, is_active, sort_order)
    )
    body = items or '<div class="emp">—</div>'
    return f'<div id="kb-side">{_tabs("products")}{create}{body}</div>'


def _section_editor(slug: str, content: str, lang: str) -> str:
    """One textarea per `## ` section; an empty canonical doc shows its section skeleton
    with localized placeholder hints."""
    pairs = split_sections(content)
    hints: dict[str, str] = {}
    if not pairs and (cd := canon(slug)) is not None and cd.sections:
        pairs = [(loc(s.title, lang), "") for s in cd.sections]
        hints = {loc(s.title, lang): loc(s.hint, lang) for s in cd.sections}
    if not pairs:
        pairs = [("", content)]
    blocks = []
    for i, (heading, body) in enumerate(pairs):
        ph = _h.escape(hints.get(heading, ""))
        label = _h.escape(heading) if heading else _h.escape(t("kb.preamble"))
        blocks.append(
            f'<div class="kb-sec"><label class="kb-sec-h">{label}</label>'
            f'<input type="hidden" name="head_{i}" value="{_h.escape(heading)}">'
            f'<textarea class="frm-ta" name="body_{i}" rows="6" placeholder="{ph}">'
            f'{_h.escape(body)}</textarea></div>')
    return f'<input type="hidden" name="nsec" value="{len(pairs)}">' + "".join(blocks)


def kb_editor_html(doc_id: int, slug: str, title: str, content: str,
                   updated_by: str | None = None) -> str:
    lang = current_lang()
    meta = (f'<span class="kb-by">{_h.escape(t("kb.edited_by"))} {_h.escape(updated_by)}</span>'
            if updated_by else "")
    return (
        f'<div class="ch"><span class="ch-n">{_h.escape(title or slug)}</span>'
        f'<span class="ch-slug">{_h.escape(slug)}</span>'
        f'<div style="margin-left:auto;display:flex;gap:.4rem;align-items:center">{meta}'
        f'<a class="btn-sm" hx-get="/ui/knowledge/{doc_id}/history" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/{doc_id}/history">🕘 {_h.escape(t("kb.history"))}</a>'
        f'</div></div>'
        f'<div class="pnl-body">'
        f'<form hx-post="/ui/knowledge/{doc_id}/save" hx-target="#main" hx-swap="innerHTML">'
        f'<div class="frm-grp"><label class="frm-lbl">{_h.escape(t("know.title"))}</label>'
        f'<input class="frm-inp" name="title" value="{_h.escape(title or "")}"></div>'
        f'{_section_editor(slug, content, lang)}'
        f'<div style="margin-top:.5rem"><button class="btn-sm btn-p">'
        f'{_h.escape(t("know.save"))}</button></div>'
        f'</form></div>'
    )


def kb_history_html(edit_url: str, slug: str, revs: list,
                    restore_url: str = "/ui/knowledge/restore") -> str:
    from ._ui_html import _as_dt, _fmt_time  # noqa: PLC0415 (avoid import cycle)
    rows = []
    for r in revs:  # (id, old_content, new_content, old_len, new_len, actor, created_at)
        rid, old_c, new_c, old_len, new_len, actor, created = r
        delta = (new_len or 0) - (old_len or 0)
        sign = f"+{delta}" if delta >= 0 else str(delta)
        when = _fmt_time(_as_dt(created))
        diff = _diff_html(old_c or "", new_c or "")
        rows.append(
            f'<div class="kb-rev"><div class="kb-rev-h">'
            f'<span class="kb-by">{_h.escape(str(actor or "—"))}</span>'
            f'<span class="muted">{_h.escape(str(when))}</span>'
            f'<span class="muted">Δ {sign}</span>'
            f'<form hx-post="{restore_url}" hx-target="#main" hx-swap="innerHTML"'
            f' style="margin-left:auto"><input type="hidden" name="rev_id" value="{rid}">'
            f'<button class="btn-sm">{_h.escape(t("kb.restore"))}</button></form>'
            f'</div>{diff}</div>')
    body = "".join(rows) or f'<div class="emp">{_h.escape(t("kb.no_history"))}</div>'
    return (
        f'<div class="ch"><a class="btn-sm" hx-get="{edit_url}"'
        f' hx-target="#main">← {_h.escape(t("kb.back"))}</a>'
        f'<span class="ch-n" style="margin-left:.5rem">{_h.escape(t("kb.history"))} · '
        f'{_h.escape(slug)}</span></div>'
        f'<div class="pnl-body">{body}</div>'
    )


def _diff_html(old: str, new: str) -> str:
    import difflib  # noqa: PLC0415
    lines = []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1):
        if ln.startswith(("+++", "---", "@@")):
            continue
        cls = "d-add" if ln.startswith("+") else "d-del" if ln.startswith("-") else "d-ctx"
        lines.append(f'<div class="{cls}">{_h.escape(ln[:200])}</div>')
        if len(lines) >= 24:
            lines.append('<div class="d-ctx">…</div>')
            break
    return f'<div class="kb-diff">{"".join(lines)}</div>' if lines else ""
