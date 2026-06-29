"""HTML generators for data panels: coach chat, knowledge, products, members, settings."""
from __future__ import annotations

import html as _h

from ._i18n import current_lang, t
from ._ui_html import _ago

_ST_ECSS: dict[str, str] = {
    "proposed": "es-p", "applied": "es-a",
    "cancelled": "es-c", "failed": "es-f", "clarify": "es-cl",
}


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
        f'<form class="fin"'
        f' hx-post="/ui/coach/say" hx-target="#coach-msgs" hx-swap="beforeend"'
        f' hx-on::after-request="this.reset();scrollMsgs(\'coach\')">'
        f'<input type="hidden" name="branch_id" value="{branch_id}">'
        f'<textarea name="request" rows="2" placeholder="{ph}"></textarea>'
        f'<button class="bsn">{send_lbl}</button></form>'
    )


# ─── knowledge panel ──────────────────────────────────────────────────────────

def knowledge_panel_html(docs: list) -> str:
    """List of KB docs; each card loads the edit view via HTMX."""
    title = _h.escape(t("nav.know"))
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
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">{cards}</div>'
    )


def knowledge_edit_html(doc_id: int, slug: str, title: str, content: str) -> str:
    """Edit form for a single KB doc."""
    back_lbl = _h.escape(t("know.back"))
    title_lbl = _h.escape(t("know.title"))
    content_lbl = _h.escape(t("know.content"))
    save_lbl = _h.escape(t("know.save"))
    return (
        f'<div class="ch">'
        f'<a class="btn-g" hx-get="/ui/knowledge/panel" hx-target="#main"'
        f' hx-push-url="/ui/knowledge/panel" style="text-decoration:none">'
        f'{back_lbl}</a>'
        f'<span class="kdoc-slug" style="margin-left:.4rem">{_h.escape(slug)}</span>'
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
        f'<div style="display:flex;gap:.5rem;margin-top:.4rem">'
        f'<button class="btn-sm btn-p">{save_lbl}</button>'
        f'</div>'
        f'</form>'
        f'</div>'
    )


# ─── products panel ───────────────────────────────────────────────────────────

def products_panel_html(products: list) -> str:
    """Read-only list of products with sort_order explanation."""
    title = _h.escape(t("nav.products"))
    hint = _h.escape(t("prod.sort_hint"))
    rows = "".join(
        f'<tr>'
        f'<td style="color:#4a5568;font-size:.72rem">{p[0]}</td>'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(p[2]))}</strong>'
        f'<br><span class="kdoc-slug">{_h.escape(str(p[1]))}</span></td>'
        f'<td><span class="pill {"p-ok" if p[3] else "p-off"}">{"✓" if p[3] else "✗"}</span></td>'
        f'<td style="color:#6b7685;font-size:.8rem;text-align:center">{p[4]}</td>'
        f'</tr>'
        for p in products  # (id, slug, title, is_active, sort_order)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{hint}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>ID</th><th>Product</th><th>Active</th><th>Sort</th></tr></thead>'
        f'<tbody>{rows or "<tr><td colspan=4 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
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
