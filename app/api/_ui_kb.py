"""Knowledge-base UI: persona/facts tree sidebar, a section editor with localized placeholder
hints, and the edit-history diff view. Navigation is sidebar-only — clicking a doc loads it
into #main. Products live only under /ui/products/panel now — this page used to also carry a
Products tab, which just duplicated that entry point without adding anything (2026-07-23)."""
from __future__ import annotations

import html as _h

from app.modules.knowledge.canonical import canon, loc
from app.modules.knowledge.canonical_docs import CATEGORIES

from ._i18n import current_lang, t

_CAT_ORDER = ("persona", "playbook", "reference")
_OTHER = "other"


def _cat_label(cat: str, lang: str) -> str:
    if cat in CATEGORIES:
        return loc(CATEGORIES[cat], lang)
    return {"ru": "Прочее", "en": "Other", "id": "Lainnya"}.get(lang, "Other")


def kb_tree_html(docs: list, active_id: int | None = None) -> str:
    """Persona-tab sidebar: docs grouped by category. When the view spans >1 branch, each
    branch's category groups are wrapped in an outer collapsible branch group
    (Branch → Category → docs) so per-branch copies of a slug don't read as duplicates."""
    lang = current_lang()
    branches = sorted({d[7] for d in docs if len(d) > 7})
    multi = len(branches) > 1
    by_branch: dict[str, dict[str, list]] = {}
    for d in docs:  # (id, slug, title, content, category, sort_order, updated_by, branch_name)
        cat = d[4] if d[4] in CATEGORIES else _OTHER
        br = d[7] if len(d) > 7 else ""
        by_branch.setdefault(br, {}).setdefault(cat, []).append(d)
    out: list[str] = []
    for br in (branches or [""]):
        cats = by_branch.get(br, {})
        cat_groups = []
        for cat in (*_CAT_ORDER, _OTHER):
            rows = cats.get(cat)
            if not rows:
                continue
            rows.sort(key=lambda r: (r[5] or 0, str(r[1])))
            items = "".join(_tree_item(d, active_id) for d in rows)
            cat_groups.append(
                f'<details class="kb-grp" open><summary>{_h.escape(_cat_label(cat, lang))}'
                f'</summary>{items}</details>')
        if not cat_groups:
            continue
        if multi:  # outer branch layer wrapping this branch's category groups
            out.append(
                f'<details class="kb-branch" open><summary>{_h.escape(br)}</summary>'
                f'{"".join(cat_groups)}</details>')
        else:
            out.extend(cat_groups)
    if not out:
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




def _doc_editor(slug: str, content: str, lang: str) -> str:
    """ONE textarea holding the document's whole markdown.

    It used to be one textarea per `## ` section. That split was functional back when the
    prompt pulled individual sections (objection_snippets / market_snippets picked the
    matching `## category` bank); with the free-only cutover the whole doc rides in the
    cached prefix, so the boundaries no longer decide anything — while the split-editor
    form (`nsec` + hidden `head_i`) could only ever edit EXISTING section bodies: adding a
    section, renaming a heading or pasting a rewritten doc in one piece was impossible
    without going into the database. An empty canonical doc gets its section skeleton
    pre-filled as a starting point, with the localized hints as the placeholder."""
    body = content
    ph = ""
    if not content.strip() and (cd := canon(slug)) is not None and cd.sections:
        body = "\n\n".join(f"## {loc(s.title, lang)}\n" for s in cd.sections)
        ph = "\n".join(f"## {loc(s.title, lang)} — {loc(s.hint, lang)}" for s in cd.sections)
    rows = max(18, min(48, body.count("\n") + 6))
    return (
        f'<div class="kb-sec"><label class="kb-sec-h">{_h.escape(t("kb.doc_md"))}</label>'
        f'<textarea class="frm-ta kb-md" name="content" rows="{rows}"'
        f' placeholder="{_h.escape(ph)}">{_h.escape(body)}</textarea></div>')


def kb_editor_html(doc_id: int, slug: str, title: str, content: str,
                   updated_by: str | None = None) -> str:
    lang = current_lang()
    meta = (f'<span class="kb-by">{_h.escape(t("kb.edited_by"))} {_h.escape(updated_by)}</span>'
            if updated_by else "")
    return (
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.know"))}">'
        f'{_h.escape(title or slug)}</span>'
        f'<span class="ch-slug">{_h.escape(slug)}</span>'
        f'<div style="margin-left:auto;display:flex;gap:.4rem;align-items:center">{meta}'
        f'<button type="button" class="btn-sm" id="kb-tr-{doc_id}" onclick="kbTrAll(this)"'
        f' data-doc="{doc_id}" data-lbl="{_h.escape(t("kb.tr_all"))}"'
        f' data-lbl2="{_h.escape(t("kb.tr_orig"))}">🌐 {_h.escape(t("kb.tr_all"))}</button>'
        f'<a class="btn-sm" hx-get="/ui/knowledge/{doc_id}/history" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/{doc_id}/history">🕘 {_h.escape(t("kb.history"))}</a>'
        f'</div></div>'
        f'<div class="pnl-body">'
        f'<form id="kb-form-{doc_id}" hx-post="/ui/knowledge/{doc_id}/save" hx-target="#main"'
        f' hx-swap="innerHTML">'
        f'<div class="frm-grp"><label class="frm-lbl">{_h.escape(t("know.title"))}</label>'
        f'<input class="frm-inp kb-tr-f" name="title" value="{_h.escape(title or "")}"></div>'
        f'{_doc_editor(slug, content, lang)}'
        f'<div id="kb-tr-note-{doc_id}" class="kb-tr-note" style="display:none">'
        f'{_h.escape(t("kb.tr_note"))}</div>'
        f'<div style="margin-top:.5rem"><button class="btn-sm btn-p" id="kb-save-{doc_id}">'
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
