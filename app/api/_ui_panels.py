"""HTML generators for data panels: coach chat, knowledge, products, members, settings."""
from __future__ import annotations

import html as _h

from ._i18n import current_lang, t
from ._ui_html import _ago, ig_post_url

_ST_ECSS: dict[str, str] = {
    "proposed": "es-p", "applied": "es-a",
    "cancelled": "es-c", "failed": "es-f", "clarify": "es-cl",
}


# ─── coach chat ───────────────────────────────────────────────────────────────

# ─── stage badge helper ───────────────────────────────────────────────────────

_STC: dict[str, str] = {
    "new": "sn", "nurturing": "snu", "qualifying": "sq", "presenting": "sp",
    "objection": "so", "ready": "sr", "handed_off": "sh", "dormant": "sd",
    "manager": "sm",
}
_STAGE_COLOR: dict[str, str] = {
    "new": "#4da6ff", "nurturing": "#d6a96f", "qualifying": "#9b7aff",
    "presenting": "#4adb7a", "objection": "#ffa94d", "ready": "#51cf66",
    "handed_off": "#22b8cf", "dormant": "#868e96", "manager": "#ff6b6b",
}
_ALL_STAGES = (
    "new", "nurturing", "qualifying", "presenting", "objection",
    "ready", "handed_off", "dormant", "manager",
)


def _sbadge(stage: str) -> str:
    return (
        f'<span class="bg {_STC.get(stage, "sd")}">'
        f'{_h.escape(t(f"stage.{stage}"))}</span>'
    )


# ─── leads panel ──────────────────────────────────────────────────────────────

def leads_panel_html(rows: list) -> str:
    """List of leads with stage badge, phone, and creation date."""
    title = _h.escape(t("nav.leads"))
    name_h = _h.escape(t("lead.name"))
    phone_h = _h.escape(t("lead.phone"))
    stage_h = _h.escape(t("lead.stage"))
    created_h = _h.escape(t("lead.created"))
    hint = _h.escape(t("help.leads"))
    trows = "".join(
        f'<tr>'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(r[1] or "—"))}</strong></td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.74rem;color:#4da6ff">'
        f'{_h.escape(str(r[2] or "—"))}</td>'
        f'<td>{_sbadge(str(r[3] or "new"))}</td>'
        f'<td style="color:#4a5568;font-size:.72rem">'
        f'{str(r[4])[:10] if r[4] else "—"}</td>'
        f'</tr>'
        for r in rows  # (id, display_name, phone_e164, stage, created_at)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{hint}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>{name_h}</th><th>{phone_h}</th>'
        f'<th>{stage_h}</th><th>{created_h}</th></tr></thead>'
        f'<tbody>{trows or "<tr><td colspan=4 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


# ─── outbox panel ─────────────────────────────────────────────────────────────

def outbox_panel_html(rows: list) -> str:
    """Read-only outbox queue monitor (last 100 entries)."""
    title = _h.escape(t("nav.outbox"))
    hint = _h.escape(t("help.outbox"))

    def _spill(s: str) -> str:
        css = {"pending": "s-pend", "sent": "s-sent", "failed": "s-fail"}.get(s, "s-pend")
        return f'<span class="st-pill {css}">{_h.escape(s)}</span>'

    trows = "".join(
        f'<tr>'
        f'<td>{_spill(str(r[2]))}</td>'
        f'<td style="color:#6b7685;font-size:.72rem">{_h.escape(str(r[3]))}</td>'
        f'<td style="color:#d0d7de;font-size:.77rem">{_h.escape(str(r[4] or "")[:80])}</td>'
        f'<td style="color:#4a5568;font-size:.7rem;white-space:nowrap">'
        f'{str(r[5])[:16] if r[5] else "—"}</td>'
        f'</tr>'
        for r in rows  # (id, thread_id, status, source, text, scheduled_at)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span>'
        f'<span style="font-size:.68rem;color:#4a5568;margin-left:.5rem">(read-only)</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{hint}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>{_h.escape(t("outbox.status"))}</th>'
        f'<th>{_h.escape(t("outbox.source"))}</th>'
        f'<th>Text</th>'
        f'<th>{_h.escape(t("outbox.scheduled"))}</th></tr></thead>'
        f'<tbody>{trows or "<tr><td colspan=4 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


# ─── coach chat ───────────────────────────────────────────────────────────────

def _coach_pair(
    edit_id: int, req: str, status: str, slug: str | None,
    old_t: str | None, new_t: str | None, summary: str | None,
    created_at: object,
) -> str:
    """Render one CoachingEdit as a manager-message + coach-response bubble pair."""
    mgr = _h.escape(t("who.manager"))
    diff = ""
    if old_t:
        diff += f'<div class="df">{_h.escape(old_t[:400])}</div>'
    if new_t:
        diff += f'<div class="dn">{_h.escape(new_t[:400])}</div>'
    slug_str = f' [{_h.escape(slug)}]' if slug else ""
    actions = ""
    if status == "proposed":
        a_lbl = _h.escape(t("coach.apply"))
        c_lbl = _h.escape(t("coach.cancel"))
        actions = (
            f'<div style="margin-top:.3rem">'
            f'<form style="display:inline" method="post"'
            f' action="/ui/coach/apply/{edit_id}">'
            f'<button class="bx bx-a">{a_lbl}</button></form>'
            f'<form style="display:inline" method="post"'
            f' action="/ui/coach/cancel/{edit_id}">'
            f'<button class="bx bx-c">{c_lbl}</button></form>'
            f'</div>'
        )
    elif status == "applied":
        r_lbl = _h.escape(t("coach.revert"))
        actions = (
            f'<div style="margin-top:.3rem">'
            f'<form style="display:inline" method="post"'
            f' action="/ui/coach/revert/{edit_id}">'
            f'<button class="bx" style="background:#2a3a2a;color:#51cf66">{r_lbl}</button></form>'
            f'</div>'
        )
    summ = _h.escape(summary or "")
    return (
        f'<div class="bb bb-o mgr"><div class="bt">{_h.escape(req)}</div>'
        f'<div class="bm">{mgr} · {_ago(created_at)}</div></div>'  # type: ignore[arg-type]
        f'<div class="bb bb-i" id="ce-{edit_id}">'
        f'<div class="bt">{summ}{diff}{actions}</div>'
        f'<div class="bm">Coach{slug_str} · {_h.escape(status)}</div>'
        f'</div>'
    )


def coach_chat_html(branch_id: int, edits: list, notes: list) -> str:
    """Full coach panel: active rules summary + chat history + input."""
    ph = _h.escape(t("coach.ph"))
    send_lbl = _h.escape(t("chat.send"))
    rules_title = _h.escape(t("coach.rules_title"))
    no_rules = _h.escape(t("coach.no_rules"))

    if notes:
        rule_items = "".join(
            f'<div style="font-size:.77rem;color:#d0d7de;padding:.2rem 0;'
            f'border-bottom:1px solid rgba(255,255,255,.05)">'
            f'{_h.escape(str(n[1])[:120])}</div>'
            for n in notes
        )
    else:
        rule_items = f'<div style="font-size:.74rem;color:#4a5568">{no_rules}</div>'

    rules_section = (
        f'<div style="padding:.45rem .85rem .5rem;border-bottom:1px solid #2d3748;flex-shrink:0">'
        f'<div style="font-size:.68rem;color:#6b7685;font-weight:600;'
        f'text-transform:uppercase;margin-bottom:.25rem">{rules_title}</div>'
        f'{rule_items}</div>'
    )

    history = "".join(
        _coach_pair(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])
        for r in edits
    )

    return (
        f'<div class="ch"><span class="ch-n">Coach KB</span></div>'
        f'{rules_section}'
        f'<div class="msgs" id="coach-msgs">{history}</div>'
        f'<div class="fin">'
        f'<form class="fin-row"'
        f' hx-post="/ui/coach/say" hx-target="#coach-msgs" hx-swap="beforeend"'
        f' hx-on::after-request="this.reset();scrollMsgs(\'coach\')">'
        f'<input type="hidden" name="branch_id" value="{branch_id}">'
        f'<textarea name="request" rows="2" placeholder="{ph}"></textarea>'
        f'<button class="bsn">{send_lbl}</button></form>'
        f'</div>'
    )


# ─── knowledge sidebar & panel ───────────────────────────────────────────────

def _knowledge_items_html(docs: list, active_id: int | None = None) -> str:
    """Plain <a> items for #know-list — reused in full list and HTMX refresh."""
    items = "".join(
        f'<a class="ti{"  on" if doc[0] == active_id else ""}"'
        f' hx-get="/ui/knowledge/{doc[0]}/edit" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/{doc[0]}/edit"'
        f' onclick="setOn(this)">'
        f'<div class="ti-t"><span class="ti-n">{_h.escape(str(doc[2] or doc[1]))}</span></div>'
        f'<div class="ti-p">{_h.escape(str(doc[1]))}</div>'
        f'</a>'
        for doc in docs
    )
    return items if items else '<div class="emp">—</div>'


def knowledge_list_html(docs: list) -> str:
    """Sidebar list of knowledge docs for .thr column (like thread_list_html)."""
    know_lbl = _h.escape(t("nav.know"))
    create_lbl = _h.escape(t("know.create"))
    return (
        f'<div class="thr-h" style="display:flex;align-items:center;'
        f'justify-content:space-between">'
        f'<span>{know_lbl}</span>'
        f'<a class="btn-sm btn-p" hx-get="/ui/knowledge/new" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/new"'
        f' style="text-decoration:none;font-size:.7rem;padding:.18rem .45rem">'
        f'{create_lbl}</a>'
        f'</div>'
        f'<div id="know-list"'
        f' hx-trigger="refreshKnowledgeList from:body"'
        f' hx-get="/ui/knowledge/list" hx-swap="innerHTML"'
        f' style="flex:1;overflow-y:auto">'
        f'{_knowledge_items_html(docs)}'
        f'</div>'
    )


def knowledge_panel_html(docs: list) -> str:
    """List of KB docs; each card loads the edit view via HTMX."""
    title = _h.escape(t("nav.know"))
    create_lbl = _h.escape(t("know.create"))
    cards = "".join(
        f'<div class="kdoc"'
        f' hx-get="/ui/knowledge/{doc[0]}/edit" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/{doc[0]}/edit">'
        f'<div class="kdoc-slug">{_h.escape(str(doc[1]))}</div>'
        f'<div class="kdoc-title">{_h.escape(str(doc[2] or doc[1]))}</div>'
        f'<div class="kdoc-preview">{_h.escape((doc[3] or "")[:120])}</div>'
        f'</div>'
        for doc in docs  # (id, slug, title, content)
    )
    if not docs:
        cards = '<div class="emp">—</div>'
    return (
        f'<div class="ch"><span class="ch-n">{title}</span>'
        f'<div style="margin-left:auto">'
        f'<a class="btn-sm btn-p" hx-get="/ui/knowledge/new" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/new" style="text-decoration:none">'
        f'{create_lbl}</a></div></div>'
        f'<div class="pnl-body">{cards}</div>'
    )


def knowledge_new_html() -> str:
    """Create form for a new KB doc."""
    slug_lbl = _h.escape(t("know.slug_lbl"))
    title_lbl = _h.escape(t("know.title"))
    content_lbl = _h.escape(t("know.content"))
    save_lbl = _h.escape(t("know.save"))
    new_lbl = _h.escape(t("know.new_doc"))
    return (
        f'<div class="ch"><span class="ch-n">{new_lbl}</span></div>'
        f'<div class="pnl-body">'
        f'<form hx-post="/ui/knowledge/create" hx-target="#main" hx-swap="innerHTML">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{slug_lbl}</label>'
        f'<input class="frm-inp" name="slug" placeholder="e.g. faq_pricing"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{title_lbl}</label>'
        f'<input class="frm-inp" name="title"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{content_lbl}</label>'
        f'<textarea class="frm-ta" name="content" rows="18"></textarea></div>'
        f'<div style="display:flex;gap:.5rem;margin-top:.4rem">'
        f'<button class="btn-sm btn-p">{save_lbl}</button>'
        f'</div></form></div>'
    )


def knowledge_edit_html(doc_id: int, slug: str, title: str, content: str) -> str:
    """Edit form for a single KB doc (navigation via sidebar)."""
    title_lbl = _h.escape(t("know.title"))
    content_lbl = _h.escape(t("know.content"))
    save_lbl = _h.escape(t("know.save"))
    return (
        f'<div class="ch">'
        f'<span class="ch-n">{_h.escape(title or slug)}</span>'
        f'<span style="margin-left:.4rem;font-size:.74rem;color:#6b7685">{_h.escape(slug)}</span>'
        f'</div>'
        f'<div class="pnl-body">'
        f'<form hx-post="/ui/knowledge/{doc_id}/save" hx-target="#main" hx-swap="innerHTML">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{title_lbl}</label>'
        f'<input class="frm-inp" name="title" value="{_h.escape(title or "")}">'
        f'</div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{content_lbl}</label>'
        f'<textarea class="frm-ta" name="content" rows="22">{_h.escape(content or "")}</textarea>'
        f'</div>'
        f'<div style="display:flex;gap:.5rem;align-items:center;margin-top:.4rem">'
        f'<button class="btn-sm btn-p">{save_lbl}</button>'
        f'</div>'
        f'</form>'
        f'</div>'
    )


# ─── products panel ───────────────────────────────────────────────────────────

def products_panel_html(products: list) -> str:
    """Clickable list of products with sort_order explanation. Click row → edit form."""
    title = _h.escape(t("nav.products"))
    hint = _h.escape(t("prod.sort_hint"))
    create_lbl = _h.escape(t("prod.create"))
    rows = "".join(
        f'<tr class="kdoc" style="cursor:pointer"'
        f' hx-get="/ui/products/{p[0]}/edit" hx-target="#main"'
        f' hx-push-url="/ui/products/{p[0]}/edit">'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(p[2]))}</strong>'
        f'<br><span class="kdoc-slug">{_h.escape(str(p[1]))}</span></td>'
        f'<td><span class="pill {"p-ok" if p[3] else "p-off"}">{"✓" if p[3] else "✗"}</span></td>'
        f'<td style="color:#6b7685;font-size:.8rem;text-align:center">{p[4]}</td>'
        f'</tr>'
        for p in products  # (id, slug, title, is_active, sort_order)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span>'
        f'<div style="margin-left:auto">'
        f'<a class="btn-sm btn-p" hx-get="/ui/products/new" hx-target="#main"'
        f' hx-push-url="/ui/products/new" style="text-decoration:none">'
        f'{create_lbl}</a></div></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{hint}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>Product</th><th>Active</th><th>Sort</th></tr></thead>'
        f'<tbody>{rows or "<tr><td colspan=3 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


def product_edit_html(
    prod_id: int | None, slug: str, title: str,
    content: str, is_active: bool, sort_order: int,
) -> str:
    """Edit (or create) form for a single product."""
    back_lbl = _h.escape(t("prod.back"))
    save_lbl = _h.escape(t("prod.save"))
    del_lbl = _h.escape(t("prod.delete"))
    title_lbl = _h.escape(t("prod.title_lbl"))
    slug_lbl = _h.escape(t("prod.slug_lbl"))
    content_lbl = _h.escape(t("prod.content_lbl"))
    active_lbl = _h.escape(t("prod.active_lbl"))
    sort_lbl = _h.escape(t("prod.sort_lbl"))
    action = f"/ui/products/{prod_id}/save" if prod_id else "/ui/products/create"
    delete_btn = ""
    if prod_id:
        delete_btn = (
            f'<form style="display:inline" method="post"'
            f' action="/ui/products/{prod_id}/delete"'
            f' onsubmit="return confirm(\'{del_lbl}?\')">'
            f'<button class="btn-sm" style="background:#862e2e;color:#fff">{del_lbl}</button>'
            f'</form>'
        )
    chk = "checked" if is_active else ""
    return (
        f'<div class="ch">'
        f'<a class="btn-g" hx-get="/ui/products/panel" hx-target="#main"'
        f' hx-push-url="/ui/products/panel" style="text-decoration:none">{back_lbl}</a>'
        f'</div>'
        f'<div class="pnl-body">'
        f'<form hx-post="{action}" hx-target="#main" hx-swap="innerHTML">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{slug_lbl}</label>'
        f'<input class="frm-inp" name="slug" value="{_h.escape(slug or "")}"'
        f' {"readonly" if prod_id else ""}></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{title_lbl}</label>'
        f'<input class="frm-inp" name="title" value="{_h.escape(title or "")}"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{content_lbl}</label>'
        f'<textarea class="frm-ta" name="content" rows="14">'
        f'{_h.escape(content or "")}</textarea></div>'
        f'<div class="frm-grp" style="display:flex;align-items:center;gap:.8rem">'
        f'<label style="display:flex;align-items:center;gap:.35rem;font-size:.8rem;'
        f'color:#d0d7de;cursor:pointer">'
        f'<input type="checkbox" name="is_active" value="1" {chk}> {active_lbl}</label>'
        f'<label class="frm-lbl" style="margin:0">{sort_lbl}: </label>'
        f'<input class="frm-inp" style="width:4rem" type="number" name="sort_order"'
        f' value="{sort_order}"></div>'
        f'<div style="display:flex;gap:.5rem;margin-top:.5rem">'
        f'<button class="btn-sm btn-p">{save_lbl}</button>'
        f'{delete_btn}</div>'
        f'</form></div>'
    )


# ─── members panel ────────────────────────────────────────────────────────────

def members_panel_html(rows: list) -> str:
    """Members list with user display names and roles."""
    title = _h.escape(t("nav.members"))
    help_txt = _h.escape(t("help.members"))

    def _role(role: str) -> str:
        css = {"manager": "p-mgr", "admin": "p-adm"}.get(role, "p-off")
        return f'<span class="pill {css}">{_h.escape(role)}</span>'

    trows = "".join(
        f'<tr>'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(r[3] or "—"))}</strong>'
        f'<br><span style="font-size:.7rem;color:#4a5568">tg:{r[1]}</span></td>'
        f'<td>{_role(str(r[2]))}</td>'
        f'<td style="color:#6b7685;font-size:.76rem">{r[4]}</td>'
        f'</tr>'
        for r in rows  # (u.id, u.telegram_id, m.role, u.name, m.branch_id)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{help_txt}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>User</th><th>Role</th><th>Branch</th></tr></thead>'
        f'<tbody>{trows or "<tr><td colspan=3 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


# ─── settings panel ───────────────────────────────────────────────────────────

_SETTING_DOCS: dict[str, dict[str, str]] = {
    "daily_cap": {
        "ru": "Макс. сообщений бота в день на одного лида (целое число)",
        "en": "Max bot messages per day per lead (integer)",
        "id": "Maks pesan bot per hari per lead (integer)",
    },
    "hourly_cap": {
        "ru": "Макс. сообщений бота в час на лида",
        "en": "Max bot messages per hour per lead",
        "id": "Maks pesan bot per jam per lead",
    },
    "bot_enabled": {
        "ru": "Включить бота: true / false",
        "en": "Enable bot responses: true / false",
        "id": "Aktifkan bot: true / false",
    },
    "knowledge_backend": {
        "ru": "Движок знаний: direct (текстовый) | rag (векторный поиск) | canary:N (A/B тест)",
        "en": "Knowledge backend: direct (text) | rag (vector search) | canary:N (A/B test)",
        "id": "Backend pengetahuan: direct | rag | canary:N",
    },
    "greeting_enabled": {
        "ru": "Авто-приветствие нового лида при первом сообщении: true / false",
        "en": "Auto-greet new lead on first message: true / false",
        "id": "Salam otomatis lead baru: true / false",
    },
    "followup_delay_h": {
        "ru": "Задержка фолоапа (часов) если лид не ответил",
        "en": "Follow-up delay (hours) if lead doesn't reply",
        "id": "Penundaan follow-up (jam) jika lead tidak membalas",
    },
    "max_thread_messages": {
        "ru": "Макс. сообщений в треде — предохранитель от бесконечных диалогов",
        "en": "Max messages per thread — guard against infinite loops",
        "id": "Maks pesan per thread",
    },
    "deep_sweep_cap": {
        "ru": "Лимит массовой рассылки (deep sweep) на одну сессию",
        "en": "Deep sweep cap per session",
        "id": "Batas deep sweep per sesi",
    },
}


def _set_desc(key: str) -> str:
    doc = _SETTING_DOCS.get(key)
    if not doc:
        return ""
    lang = current_lang()
    return doc.get(lang) or doc.get("en") or ""


def branches_panel_html(rows: list) -> str:
    """List of branches with name, lang, tz, active flag and edit button."""
    title = _h.escape(t("nav.branches"))
    hint = _h.escape(t("help.branches"))
    create_lbl = _h.escape(t("br.create"))
    name_h = _h.escape(t("br.name"))
    lang_h = _h.escape(t("br.lang_lbl"))
    tz_h = _h.escape(t("br.tz"))
    active_h = _h.escape(t("br.active"))
    edit_lbl = _h.escape(t("br.edit"))
    trows = "".join(
        f'<tr>'
        f'<td style="color:#4a5568;font-size:.72rem">{r[0]}</td>'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(r[1] or "—"))}</strong></td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.74rem;color:#4da6ff">'
        f'{_h.escape(str(r[2] or "—"))}</td>'
        f'<td style="color:#d0d7de;font-size:.74rem">UTC+{r[3]}</td>'
        f'<td><span class="pill {"p-ok" if r[4] else "p-off"}">'
        f'{"on" if r[4] else "off"}</span></td>'
        f'<td><button class="act-btn"'
        f' hx-get="/ui/branches/{r[0]}/edit"'
        f' hx-target="#main" hx-push-url="true">{edit_lbl}</button></td>'
        f'</tr>'
        for r in rows  # (id, name, lang, tz_offset_h, is_active)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span>'
        f'<div class="ch-acts">'
        f'<button class="act-btn"'
        f' hx-get="/ui/branches/new"'
        f' hx-target="#main" hx-push-url="true">{create_lbl}</button>'
        f'</div></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{hint}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>ID</th><th>{name_h}</th>'
        f'<th>{lang_h}</th><th>{tz_h}</th>'
        f'<th>{active_h}</th><th></th></tr></thead>'
        f'<tbody>{trows or "<tr><td colspan=6 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


def branch_edit_html(
    bid: int | None,
    name: str,
    lang: str,
    tz: int,
    is_active: bool,
    seeded: bool = False,
) -> str:
    """Form for creating or editing a branch."""
    title = _h.escape(t("br.new" if bid is None else "br.edit_title"))
    action = "/ui/branches/create" if bid is None else f"/ui/branches/{bid}/save"
    _branch_langs = (
        ("id", "Bahasa Indonesia"), ("ms", "Bahasa Melayu"), ("en", "English"),
        ("ru", "Русский"), ("zh", "中文 (Mandarin)"), ("ar", "العربية"),
        ("vi", "Tiếng Việt"), ("th", "ภาษาไทย"), ("hi", "हिन्दी"),
        ("ko", "한국어"), ("ja", "日本語"), ("es", "Español"),
        ("fr", "Français"), ("de", "Deutsch"), ("pt", "Português"),
        ("tr", "Türkçe"),
    )
    lang_opts = "".join(
        f'<option value="{lc}" {"selected" if lc == lang else ""}>'
        f'{lbl} ({lc})</option>'
        for lc, lbl in _branch_langs
    )
    active_checked = "checked" if is_active else ""
    save_lbl = _h.escape(t("br.save"))
    back_lbl = _h.escape(t("br.back"))
    name_lbl = _h.escape(t("br.name"))
    lang_lbl = _h.escape(t("br.lang_lbl"))
    tz_lbl = _h.escape(t("br.tz"))
    active_lbl = _h.escape(t("br.active"))
    seeded_note = (
        f'<div class="hint" style="color:#51cf66;margin-bottom:.5rem">'
        f'{_h.escape(t("br.settings_seeded"))}</div>'
        if seeded else ""
    )
    return (
        f'<div class="ch">'
        f'<button class="act-btn"'
        f' hx-get="/ui/branches/panel"'
        f' hx-target="#main" hx-push-url="true">{back_lbl}</button>'
        f'<span class="ch-n" style="margin-left:.6rem">{title}</span>'
        f'</div>'
        f'<div class="pnl-body">'
        f'{seeded_note}'
        f'<form hx-post="{action}" hx-target="#main" hx-push-url="true"'
        f' style="max-width:400px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{name_lbl}</label>'
        f'<input class="frm-inp" name="name" value="{_h.escape(name)}" required></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{lang_lbl}</label>'
        f'<select class="act-sel" name="lang"'
        f' style="width:100%;padding:.32rem .35rem">{lang_opts}</select></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{tz_lbl} (7 = Jakarta, 3 = Москва, 0 = UTC)</label>'
        f'<input class="frm-inp" name="tz_offset_h" type="number"'
        f' min="-12" max="14" value="{tz}"></div>'
        f'<div class="frm-grp" style="display:flex;align-items:center;gap:.5rem">'
        f'<input type="checkbox" name="is_active" id="br-active" {active_checked}>'
        f'<label class="frm-lbl" for="br-active" style="margin:0">{active_lbl}</label></div>'
        f'<button type="submit" class="btn-sm btn-p">{save_lbl}</button>'
        f'</form>'
        + (_channels_section(bid) if bid is not None else "")
        + '</div>'
    )


def _channels_section(bid: int) -> str:
    ch_title = _h.escape(t("ch.title"))
    add_lbl = _h.escape(t("ch.add"))
    return (
        f'<hr style="border:none;border-top:1px solid #2d3748;margin:1.2rem 0 .7rem">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'margin-bottom:.5rem">'
        f'<span style="font-weight:600;color:#e8eef4;font-size:.82rem">{ch_title}</span>'
        f'<button class="btn-sm btn-p"'
        f' hx-get="/ui/channels/branch/{bid}/new"'
        f' hx-target="#ch-form" hx-swap="innerHTML">{add_lbl}</button>'
        f'</div>'
        f'<div id="ch-list"'
        f' hx-get="/ui/channels/branch/{bid}"'
        f' hx-trigger="load, refreshChannelList from:body"'
        f' hx-swap="innerHTML">'
        f'</div>'
        f'<div id="ch-form" style="margin-top:.75rem"></div>'
    )


def channel_list_partial_html(channels: list, sessions: list, branch_id: int) -> str:
    """HTMX-loaded channel table for #ch-list inside branch edit."""
    session_map = {r[0]: r[1] for r in sessions}
    _kind_lbl = {
        "instagram": "Instagram", "meta_business": "Meta Business", "whatsapp": "WhatsApp",
    }
    _st_cls = {"active": "p-ok", "expired": "p-off", "challenge": "p-off", "none": "p-off"}
    _st_i18n = {
        "active": "ch.st_active", "expired": "ch.st_exp",
        "challenge": "ch.st_chal", "none": "ch.st_none",
    }
    if not channels:
        return (
            f'<div class="emp" style="height:2rem">{_h.escape(t("ch.no_ch"))}</div>'
        )
    rows = ""
    for ch in channels:
        ch_id, kind, handle, acct, active = ch[0], ch[1], ch[2], ch[3], ch[4]
        st = session_map.get(ch_id, "none")
        st_pill = (
            f'<span class="pill {_st_cls.get(st,"p-off")}"'
            + (' style="background:#3a2a1f;color:#ffa94d"' if st == "challenge" else "")
            + f'>{_h.escape(t(_st_i18n.get(st,"ch.st_none")))}</span>'
        )
        active_pill = (
            f'<span class="pill p-ok">{_h.escape(t("ch.active"))}</span>'
            if active else '<span class="pill p-off">off</span>'
        )
        rows += (
            f'<tr>'
            f'<td style="color:#4da6ff;font-size:.77rem">'
            f'{_kind_lbl.get(kind, kind)}</td>'
            f'<td style="font-family:ui-monospace,monospace;font-size:.75rem">'
            f'{_h.escape(handle or acct or "—")}</td>'
            f'<td>{st_pill}</td>'
            f'<td>{active_pill}</td>'
            f'<td style="white-space:nowrap">'
            f'<button class="act-btn" style="margin-right:.2rem"'
            f' hx-get="/ui/channels/{ch_id}/edit"'
            f' hx-target="#ch-form" hx-swap="innerHTML">'
            f'{_h.escape(t("ch.edit"))}</button>'
            f'<button class="act-btn" style="margin-right:.2rem"'
            f' hx-get="/ui/channels/{ch_id}/credential"'
            f' hx-target="#ch-form" hx-swap="innerHTML">'
            f'{_h.escape(t("ch.connect"))}</button>'
            f'<button class="act-btn" style="background:#862e2e"'
            f' hx-post="/ui/channels/{ch_id}/delete"'
            f' hx-target="#ch-list" hx-swap="innerHTML">'
            f'{_h.escape(t("ch.delete"))}</button>'
            f'</td></tr>'
        )
    kind_h = _h.escape(t("ch.kind"))
    handle_h = _h.escape(t("ch.handle"))
    return (
        f'<table class="tbl"><thead><tr>'
        f'<th>{kind_h}</th><th>{handle_h}</th>'
        f'<th>Status</th><th></th><th></th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
    )


def channel_new_form_html(branch_id: int) -> str:
    """Form to create a new channel (kind selector + metadata)."""
    title = _h.escape(t("ch.new"))
    kind_opts = "".join(
        f'<option value="{v}">{_h.escape(t(k))}</option>'
        for v, k in (
            ("instagram", "ch.kind_ig"),
            ("meta_business", "ch.kind_meta"),
            ("whatsapp", "ch.kind_wa"),
        )
    )
    save_lbl = _h.escape(t("ch.save"))
    handle_lbl = _h.escape(t("ch.handle"))
    return (
        f'<div style="font-weight:600;color:#e8eef4;font-size:.82rem;margin-bottom:.55rem">'
        f'{title}</div>'
        f'<form hx-post="/ui/channels/branch/{branch_id}/create"'
        f' hx-target="#ch-list" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.kind"))}</label>'
        f'<select class="act-sel" name="kind"'
        f' style="width:100%;padding:.3rem .35rem">{kind_opts}</select></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{handle_lbl}'
        f' <span style="color:#4a5568;font-size:.7rem">'
        f'(username / номер / handle)</span></label>'
        f'<input class="frm-inp" name="handle"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.page_id"))}'
        f' <span style="color:#4a5568;font-size:.7rem">(опц.)</span></label>'
        f'<input class="frm-inp" name="account_id"></div>'
        f'<div class="frm-grp" style="display:flex;align-items:center;gap:.5rem">'
        f'<input type="checkbox" name="is_active" id="ch-active" checked>'
        f'<label class="frm-lbl" for="ch-active" style="margin:0">'
        f'{_h.escape(t("ch.active"))}</label></div>'
        f'<button type="submit" class="btn-sm btn-p">{save_lbl}</button>'
        f'</form>'
    )


def channel_edit_form_html(
    ch_id: int, kind: str, handle: str, account_id: str, is_active: bool,
) -> str:
    """Form to edit channel metadata (handle, account_id, active)."""
    _kind_lbl = {
        "instagram": "Instagram", "meta_business": "Meta Business", "whatsapp": "WhatsApp",
    }
    checked = "checked" if is_active else ""
    save_lbl = _h.escape(t("ch.save"))
    return (
        f'<div style="font-weight:600;color:#4da6ff;font-size:.8rem;margin-bottom:.55rem">'
        f'{_kind_lbl.get(kind, kind)} #{ch_id}</div>'
        f'<form hx-post="/ui/channels/{ch_id}/save"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.handle"))}</label>'
        f'<input class="frm-inp" name="handle" value="{_h.escape(handle)}"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.page_id"))}</label>'
        f'<input class="frm-inp" name="account_id" value="{_h.escape(account_id)}"></div>'
        f'<div class="frm-grp" style="display:flex;align-items:center;gap:.5rem">'
        f'<input type="checkbox" name="is_active" id="ch-a{ch_id}" {checked}>'
        f'<label class="frm-lbl" for="ch-a{ch_id}" style="margin:0">'
        f'{_h.escape(t("ch.active"))}</label></div>'
        f'<button type="submit" class="btn-sm btn-p">{save_lbl}</button>'
        f'</form>'
    )


def channel_credential_html(ch_id: int, kind: str, status: str) -> str:
    """Credential entry/status panel for a channel (loaded into #ch-form)."""
    _st_cls = {"active": "p-ok", "expired": "p-off", "challenge": "p-off"}
    _st_i18n = {"active": "ch.st_active", "expired": "ch.st_exp", "challenge": "ch.st_chal"}
    st_pill = (
        f'<span class="pill {_st_cls.get(status, "p-off")}"'
        + (' style="background:#3a2a1f;color:#ffa94d"' if status == "challenge" else "")
        + f'>{_h.escape(t(_st_i18n.get(status, "ch.st_none")))}</span>'
    )
    header = (
        f'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.6rem">'
        f'<span style="font-weight:600;color:#e8eef4;font-size:.82rem">'
        f'{_h.escape(t("ch.connect"))}</span>{st_pill}</div>'
    )
    body = _ch_connected(ch_id) if status == "active" else _ch_form_for(ch_id, kind)
    return header + body


def _ch_form_for(ch_id: int, kind: str) -> str:
    if kind == "instagram":
        return _ch_ig_form(ch_id)
    if kind == "meta_business":
        return _ch_meta_form(ch_id)
    if kind == "whatsapp":
        return _ch_wa_form(ch_id)
    return '<div class="emp">Unknown channel kind</div>'


def _ch_connected(ch_id: int) -> str:
    """Post-login state: session is active — confirm it and offer a reconnect."""
    return (
        f'<div style="color:#51cf66;font-size:.8rem;margin-bottom:.55rem">'
        f'{_h.escape(t("ch.session_ok"))}</div>'
        f'<button class="btn-sm" hx-get="/ui/channels/{ch_id}/form"'
        f' hx-target="#ch-form" hx-swap="innerHTML">'
        f'{_h.escape(t("ch.reconnect"))}</button>'
    )


def _ch_err(error: str) -> str:
    if not error:
        return ""
    return (
        f'<div style="color:#f03e3e;font-size:.76rem;margin-bottom:.4rem">'
        f'{_h.escape(error)}</div>'
    )


def _ch_ig_form(ch_id: int, step: str = "login", flow_id: str = "", error: str = "") -> str:
    err = _ch_err(error)
    if step == "2fa":
        return (
            f'{err}'
            f'<form hx-post="/ui/channels/{ch_id}/ig/verify"'
            f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:320px">'
            f'<input type="hidden" name="flow_id" value="{_h.escape(flow_id)}">'
            f'<div class="frm-grp">'
            f'<label class="frm-lbl">{_h.escape(t("ch.code_2fa"))}</label>'
            f'<input class="frm-inp" name="code" autocomplete="one-time-code" autofocus></div>'
            f'<button type="submit" class="btn-sm btn-p">'
            f'{_h.escape(t("ch.verify"))}</button>'
            f'</form>'
        )
    return (
        f'{err}'
        f'<form hx-post="/ui/channels/{ch_id}/ig/start"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:340px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.ig_json"))}</label>'
        f'<textarea class="frm-ta" name="session_json" rows="3"'
        f' placeholder=\'{{"device_settings":...}}\' style="min-height:4rem"></textarea></div>'
        f'<div style="color:#6b7685;font-size:.71rem;text-align:center;margin:.3rem 0">'
        f'{_h.escape(t("ch.or_login"))}</div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.username"))}</label>'
        f'<input class="frm-inp" name="username"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.password"))}</label>'
        f'<input class="frm-inp" name="password" type="password"></div>'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.ig_login"))}</button>'
        f'</form>'
    )


def _ch_meta_form(ch_id: int, error: str = "") -> str:
    return (
        f'{_ch_err(error)}'
        f'<form hx-post="/ui/channels/{ch_id}/meta/connect"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.page_id"))}</label>'
        f'<input class="frm-inp" name="page_id" placeholder="123456789"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.token"))}'
        f' <span style="color:#4a5568;font-size:.7rem">(Graph API)</span></label>'
        f'<input class="frm-inp" name="token" placeholder="EAAxx..."></div>'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.connect"))}</button>'
        f'</form>'
    )


def _ch_wa_form(ch_id: int, error: str = "") -> str:
    return (
        f'{_ch_err(error)}'
        f'<form hx-post="/ui/channels/{ch_id}/wa/connect"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_url"))}</label>'
        f'<input class="frm-inp" name="base_url"'
        f' placeholder="https://evolution.example.com"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_inst"))}</label>'
        f'<input class="frm-inp" name="instance"'
        f' placeholder="my-instance"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.wa_key"))}</label>'
        f'<input class="frm-inp" name="api_key"></div>'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.connect"))}</button>'
        f'</form>'
    )


def settings_panel_html(settings: list) -> str:
    """Settings list with inline save forms and per-key descriptions."""
    title = _h.escape(t("nav.settings"))
    save_lbl = _h.escape(t("set.save"))
    rows = ""
    for s in settings:
        sid, _bid, key, value = s  # (id, branch_id, key, value)
        desc = _set_desc(key)
        desc_html = f'<div class="set-desc">{_h.escape(desc)}</div>' if desc else ""
        rows += (
            f'<tr>'
            f'<td style="min-width:160px"><span class="set-key">{_h.escape(key)}</span>'
            f'{desc_html}</td>'
            f'<td>'
            f'<form hx-post="/ui/settings/{sid}/save" hx-target="this"'
            f' hx-swap="outerHTML" style="display:flex;gap:.35rem;align-items:center">'
            f'<input class="set-val" name="value" value="{_h.escape(str(value))}">'
            f'<button class="btn-sm btn-p">{save_lbl}</button>'
            f'</form>'
            f'</td>'
            f'</tr>'
        )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<table class="tbl">'
        f'<thead><tr><th>Key</th><th>Value</th></tr></thead>'
        f'<tbody>{rows or "<tr><td colspan=2 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


# ─── reports panel ────────────────────────────────────────────────────────────

def _ad_funnel_html(rows: list) -> str:
    """Per-ad funnel table: leads from each ad → pipeline / won / conv%, + FB deep link."""
    if not rows:
        return ""
    hdr = (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:1rem 0 .35rem">'
        f'{_h.escape(t("rep.ad_funnel"))}</h3>'
    )
    body = ""
    for ad_id, ad_media_id, total, pipeline, won, dormant in rows:
        total = int(total or 0)
        won = int(won or 0)
        conv = round(won / total * 100, 1) if total else 0.0
        fb = _h.escape(
            "https://adsmanager.facebook.com/adsmanager/manage/ads?"
            f"selected_ad_ids={ad_id}"
        )
        ad_cell = (
            f'<a href="{fb}" target="_blank" rel="noreferrer"'
            f' style="color:#4da6ff;font-family:ui-monospace,monospace;font-size:.7rem">'
            f'{_h.escape(str(ad_id))}</a>'
        )
        if ad_media_id:
            post = ig_post_url(str(ad_media_id))
            if post:
                ad_cell += (
                    f' <a href="{_h.escape(post)}" target="_blank" rel="noreferrer"'
                    f' title="IG post" style="text-decoration:none">📷</a>'
                )
        body += (
            f'<tr><td>{ad_cell}</td>'
            f'<td class="rep-n">{total}</td>'
            f'<td class="rep-n" style="color:#9b7aff">{int(pipeline or 0)}</td>'
            f'<td class="rep-n" style="color:#51cf66">{won}</td>'
            f'<td class="rep-n" style="color:#868e96">{int(dormant or 0)}</td>'
            f'<td class="rep-n" style="color:#ffa94d">{conv}%</td></tr>'
        )
    return (
        f'{hdr}<table class="rep-tbl"><thead><tr>'
        f'<th>{_h.escape(t("rep.ad"))}</th>'
        f'<th style="text-align:right">{_h.escape(t("rep.total"))}</th>'
        f'<th style="text-align:right">{_h.escape(t("rep.pipeline"))}</th>'
        f'<th style="text-align:right">{_h.escape(t("rep.won"))}</th>'
        f'<th style="text-align:right">{_h.escape(t("rep.dormant"))}</th>'
        f'<th style="text-align:right">{_h.escape(t("rep.conv"))}</th>'
        f'</tr></thead><tbody>{body}</tbody></table>'
    )


def reports_panel_html(
    stage_counts: dict[str, int],
    hour_in: dict[int, int],
    hour_out: dict[int, int],
    ad_funnel: list | None = None,
) -> str:
    _pipeline = ("new", "nurturing", "qualifying", "presenting", "objection")
    _won = ("ready", "handed_off")
    total = sum(stage_counts.values())
    pipeline = sum(stage_counts.get(s, 0) for s in _pipeline)
    won = sum(stage_counts.get(s, 0) for s in _won)
    dormant = stage_counts.get("dormant", 0)
    manager_n = stage_counts.get("manager", 0)
    conv = round(won / total * 100, 1) if total else 0.0

    def _kpi(label: str, value: str, color: str = "#e8eef4") -> str:
        return (
            f'<div class="kpi">'
            f'<div class="kpi-n" style="color:{color}">{_h.escape(value)}</div>'
            f'<div class="kpi-l">{_h.escape(t(label))}</div></div>'
        )

    kpis = (
        _kpi("rep.total", str(total))
        + _kpi("rep.pipeline", str(pipeline), "#9b7aff")
        + _kpi("rep.won", str(won), "#51cf66")
        + _kpi("rep.conv", f"{conv}%", "#ffa94d")
        + _kpi("rep.dormant", str(dormant), "#868e96")
    )

    # distribution bar (pipeline / won / dormant / manager)
    bar_parts = [
        (pipeline, "#9b7aff"), (won, "#51cf66"),
        (dormant, "#868e96"), (manager_n, "#ff6b6b"),
    ]
    bar_segs = "".join(
        f'<div style="flex-grow:{n};background:{c};display:flex;align-items:center;'
        f'justify-content:center;color:rgba(0,0,0,.7);font-size:.6rem;font-weight:700;'
        f'min-width:16px" title="{round(n/total*100,1) if total else 0}%">'
        f'{round(n/total*100,1) if total else 0}%</div>'
        for n, c in bar_parts if n
    )
    status_bar = (
        f'<div style="display:flex;height:18px;border-radius:3px;overflow:hidden;'
        f'gap:1px;margin:.5rem 0 .9rem">{bar_segs}</div>'
    )

    # stage breakdown table
    tbl_rows = ""
    for s in _ALL_STAGES:
        n = stage_counts.get(s, 0)
        badge = (
            f'<span class="bg {_STC.get(s, "sd")}">'
            f'{_h.escape(t(f"stage.{s}"))}</span>'
        )
        bar_w = round(n / (max(stage_counts.values(), default=1) or 1) * 80)
        tbl_rows += (
            f'<tr><td>{badge}</td>'
            f'<td class="rep-n">{n}'
            f'<span style="display:inline-block;width:{bar_w}px;height:5px;'
            f'background:{_STAGE_COLOR.get(s,"#4a5568")};opacity:.45;'
            f'border-radius:2px;margin-left:.4rem;vertical-align:middle"></span>'
            f'</td></tr>'
        )

    by_stage_lbl = _h.escape(t("rep.by_stage"))
    stage_lbl = _h.escape(t("rep.stage"))
    count_lbl = _h.escape(t("rep.count"))
    stage_table = (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:.7rem 0 .35rem">{by_stage_lbl}</h3>'
        f'<table class="rep-tbl"><thead>'
        f'<tr><th>{stage_lbl}</th><th style="text-align:right">{count_lbl}</th></tr>'
        f'</thead><tbody>{tbl_rows}</tbody></table>'
    )

    # hourly activity chart
    max_val = max(max(hour_in.values(), default=0), max(hour_out.values(), default=0), 1)
    hour_bars = ""
    for h in range(24):
        n_in = hour_in.get(h, 0)
        n_out = hour_out.get(h, 0)
        h_in = round(n_in / max_val * 100)
        h_out = round(n_out / max_val * 100)
        hour_bars += (
            f'<div class="hbar" title="{h:02d}:00 ↓{n_in} ↑{n_out}">'
            f'<div style="flex:1;display:flex;flex-direction:column;gap:1px;'
            f'justify-content:flex-end;width:100%">'
            f'<div style="height:{h_in}%;background:#4da6ff;'
            f'{"min-height:1px" if n_in else ""}"></div>'
            f'<div style="height:{h_out}%;background:#51cf66;'
            f'{"min-height:1px" if n_out else ""}"></div>'
            f'</div>'
            f'<div class="hbar-l">{h if h % 4 == 0 else ""}</div>'
            f'</div>'
        )

    title_lbl = _h.escape(t("rep.title"))
    act_lbl = _h.escape(t("rep.activity"))
    in_lbl = _h.escape(t("rep.msgs_in"))
    out_lbl = _h.escape(t("rep.msgs_out"))

    return (
        f'<div class="ch"><span class="ch-n">{title_lbl}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="kpi-row">{kpis}</div>'
        f'{status_bar}'
        f'{stage_table}'
        f'{_ad_funnel_html(ad_funnel or [])}'
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:1rem 0 .35rem">{act_lbl} (24h)</h3>'
        f'<div class="hchart">{hour_bars}</div>'
        f'<div style="font-size:.63rem;color:#4a5568;margin-top:.3rem">'
        f'<span style="color:#4da6ff">▮</span> {in_lbl}&nbsp;&nbsp;'
        f'<span style="color:#51cf66">▮</span> {out_lbl}'
        f'</div>'
        f'</div>'
    )
