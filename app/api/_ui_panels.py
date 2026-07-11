"""HTML generators for data panels: coach chat, knowledge, products, members, settings."""
from __future__ import annotations

import html as _h
import json as _json
from datetime import UTC, datetime, timedelta

from ._i18n import current_lang, t
from ._ui_html import _STAGE_COLOR, _STAGE_ICON, _ago, _as_dt, ig_post_url

_ST_ECSS: dict[str, str] = {
    "proposed": "es-p", "applied": "es-a",
    "cancelled": "es-c", "failed": "es-f", "clarify": "es-cl",
}


# ─── coach chat ───────────────────────────────────────────────────────────────

# ─── stage badge helper ───────────────────────────────────────────────────────
# _STAGE_COLOR / _STAGE_ICON are the ONE canonical funnel palette (defined in _ui_html.py,
# also driving the .sn/.sq/... badge CSS there) — imported, never redefined, so the pipeline
# stage colors are identical everywhere they appear (inbox badges, funnel chart, segment
# tree's per-stage boxes). See _SEG_META below for the separate, deliberately non-colliding
# classifier/intent palette.

_STC: dict[str, str] = {
    "new": "sn", "nurturing": "snu", "qualifying": "sq", "presenting": "sp",
    "objection": "so", "ready": "sr", "handed_off": "sh", "dormant": "sd",
    "manager": "sm",
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

def leads_panel_html(rows: list, tz_by_branch: dict[int, int] | None = None) -> str:
    """List of leads with stage badge, phone, and creation date (branch-local)."""
    title = _h.escape(t("nav.leads"))
    name_h = _h.escape(t("lead.name"))
    phone_h = _h.escape(t("lead.phone"))
    stage_h = _h.escape(t("lead.stage"))
    created_h = _h.escape(t("lead.created"))
    hint = _h.escape(t("help.leads"))
    tz = tz_by_branch or {}

    def _created(v: object, branch_id: object) -> str:
        dt = _as_dt(v)
        if dt is None:
            return "—"
        dt += timedelta(hours=tz.get(branch_id, 0))
        return dt.strftime("%Y-%m-%d")

    trows = "".join(
        f'<tr>'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(r[1] or "—"))}</strong></td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.74rem;color:#4da6ff">'
        f'{_h.escape(str(r[2] or "—"))}</td>'
        f'<td>{_sbadge(str(r[3] or "new"))}</td>'
        f'<td style="color:#4a5568;font-size:.72rem">'
        f'{_created(r[4], r[5])}</td>'
        f'</tr>'
        for r in rows  # (id, display_name, phone_e164, stage, created_at, branch_id)
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

def outbox_count_html(n: int) -> str:
    """Inner content of the sidebar Outbox nav badge — polled every 15s. Empty when
    nothing is queued, which the '.na-badge:empty{display:none}' CSS rule hides."""
    return str(n) if n > 0 else ""


def inbox_awaiting_badge_html(in_queue: int, off: int) -> str:
    """Inbox nav badge, split into two clickable numbers that sum to the total unanswered:
    IN the generation queue (Stepan will reply, orange) and NOT in it (bot off / silent stage /
    too old / reply already queued — Stepan won't, grey). Empty when nothing awaits (hidden)."""
    if in_queue + off <= 0:
        return ""

    def _num(cls: str, n: int, val: str, tip: str) -> str:
        js = ("event.stopPropagation();event.preventDefault();"
              f"location.href='/ui/inbox?awaiting={val}';return false")
        return f'<span class="{cls}" title="{_h.escape(t(tip))}" onclick="{js}">{n}</span>'

    return (_num("iaw iaw-q", in_queue, "queue", "inbox.await_queue")
            + _num("iaw iaw-off", off, "off", "inbox.await_off"))


def outbox_panel_html(
    rows: list, tz_by_branch: dict[int, int] | None = None,
    quiet_by_branch: dict[int, tuple[int, int]] | None = None,
    cap_status: dict[int, tuple[bool, bool]] | None = None,
    sending_paused: dict[int, bool] | None = None,
) -> str:
    """Read-only outbox queue monitor (last 100 entries). `cap_status` = {branch_id:
    (hourly_reached, daily_reached)} — computed live from real counts by the caller each
    request, never hardcoded here, so a pending/due row that's actually being held back by
    the anti-ban send cap shows why instead of just looking silently stuck. `sending_paused`
    = {branch_id: bool} — the branch's own send_outbox master switch (independent of the
    bot on/off toggle); when paused, EVERY due row is held, including manager sends."""
    title = _h.escape(t("nav.outbox"))
    hint = _h.escape(t("help.outbox"))
    tz = tz_by_branch or {}
    quiet = quiet_by_branch or {}
    caps = cap_status or {}
    paused = sending_paused or {}

    def _spill(s: str) -> str:
        css = {"pending": "s-pend", "sent": "s-sent", "failed": "s-fail"}.get(s, "s-pend")
        return f'<span class="st-pill {css}">{_h.escape(s)}</span>'

    def _chat_link(tid: object) -> str:
        return (
            f'<a class="oq-chat" hx-get="/ui/chat/{tid}" hx-target="#main"'
            f' hx-push-url="true" href="/ui/inbox" onclick="setOpenThread({tid})">'
            f'#{_h.escape(str(tid))}</a>'
        )

    def _ts(v: object, branch_id: object) -> str:
        dt = _as_dt(v)
        if dt is None:
            return "—"
        dt += timedelta(hours=tz.get(branch_id, 0))
        return dt.strftime("%H:%M:%S")

    now = datetime.now(UTC).replace(tzinfo=None)

    def _in_quiet(branch_id: object) -> int | None:
        """quiet_end hour if we're currently inside this branch's quiet window, else None."""
        qs, qe = quiet.get(branch_id, (0, 0))
        if qs == qe:
            return None
        hour = (now + timedelta(hours=tz.get(branch_id, 0))).hour
        inside = (hour >= qs or hour < qe) if qs > qe else (qs <= hour < qe)
        return qe if inside else None

    def _eta(status: object, scheduled: object, source: object, branch_id: object) -> str:
        # this queue is pending-only, so 'sent time' is always blank — show instead when the
        # send is due (scheduled_at, ± the ~20s poll; a snapshot at page load).
        if str(status) != "pending":
            return "—"
        dt = _as_dt(scheduled)
        if dt is None:
            return "—"
        secs = (dt - now).total_seconds()
        # sending is fully paused for this branch (independent of the bot on/off toggle) —
        # applies to EVERY due row, manager sends included, since send_outbox skips the whole
        # branch when this is off.
        if secs <= 5 and paused.get(branch_id):
            return f'<span style="color:#ff8787">⏸ {_h.escape(t("outbox.sending_paused"))}</span>'
        # follow-ups are HELD during quiet hours — they won't go out until quiet lifts, even
        # if their scheduled_at is already due.
        qe = _in_quiet(branch_id)
        if str(source) == "followup" and qe is not None:
            return (f'<span style="color:#ffa94d">🔇 '
                    f'{_h.escape(t("outbox.quiet_until", h=f"{qe:02d}"))}</span>')
        # due but held back by the hourly/daily anti-ban send cap (manager sends bypass it,
        # so exempt those) — without this the row would just say "now" and never move,
        # looking like a silent bug instead of the deliberate anti-ban throttle it is.
        if secs <= 5 and str(source) != "manager":
            hourly_hit, daily_hit = caps.get(branch_id, (False, False))
            if hourly_hit or daily_hit:
                which = t("outbox.cap_hour") if hourly_hit else t("outbox.cap_day")
                held = _h.escape(t("outbox.cap_held", limit=which))
                return f'<span style="color:#ff8787">⏳ {held}</span>'
        if secs <= 5:
            return f'<span style="color:#51cf66">{_h.escape(t("outbox.now"))}</span>'
        if secs < 60:
            return _h.escape(t("outbox.in_s", n=int(secs)))
        if secs < 3600:
            return _h.escape(t("outbox.in_m", n=int(secs // 60)))
        return _h.escape(t("outbox.in_h", n=round(secs / 3600, 1)))

    trows = "".join(
        f'<tr>'
        f'<td>{_chat_link(r[1])}</td>'
        f'<td>{_spill(str(r[2]))}</td>'
        f'<td style="color:#6b7685;font-size:.72rem">{_h.escape(str(r[3]))}</td>'
        f'<td style="color:#d0d7de;font-size:.77rem">{_h.escape(str(r[4] or "")[:70])}</td>'
        f'<td style="color:#4a5568;font-size:.7rem;white-space:nowrap">{_ts(r[5], r[7])}</td>'
        f'<td style="color:#93a1b3;font-size:.7rem;white-space:nowrap">'
        f'{_eta(r[2], r[5], r[3], r[7])}</td>'
        f'</tr>'
        for r in rows  # (id, thread_id, status, source, text, scheduled_at, sent_at, branch_id)
    )
    return (
        f'<div class="ch"><span class="ch-n">{title}</span>'
        f'<span style="font-size:.68rem;color:#4a5568;margin-left:.5rem">(read-only)</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{hint}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>{_h.escape(t("outbox.chat"))}</th>'
        f'<th>{_h.escape(t("outbox.status"))}</th>'
        f'<th>{_h.escape(t("outbox.source"))}</th>'
        f'<th>Text</th>'
        f'<th>{_h.escape(t("outbox.scheduled"))}</th>'
        f'<th>{_h.escape(t("outbox.eta"))}</th></tr></thead>'
        f'<tbody>{trows or "<tr><td colspan=6 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


# ─── coach chat ───────────────────────────────────────────────────────────────

def _coach_bubbles(
    edit_id: int, req: str, status: str, slug: str | None,
    old_t: str | None, new_t: str | None, summary: str | None,
    created_at: object,  # noqa: ARG001
) -> tuple[str, str]:
    """Build (manager_bubble_label, coach_response_bubble) for one CoachingEdit."""
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
    label = _h.escape(t(f"coach.st.{status}")) if t(f"coach.st.{status}") != f"coach.st.{status}" \
        else _h.escape(status)
    # the coach's response bubble only — the manager's own bubble is rendered separately
    # (optimistically on send, or by _coach_pair for history).
    if status == "thinking":
        # answer is generating in the background — self-replace via poll until it lands, so
        # the answer shows up even if the manager left the page and came back.
        body = f'<span class="spin"></span> {_h.escape(t("coach.generating"))}'
        poll = (
            f' hx-get="/ui/coach/edit/{edit_id}" hx-trigger="every 2s"'
            f' hx-swap="outerHTML" hx-target="this"'
        )
    else:
        body = f'{summ}{diff}{actions}'
        poll = ""
    resp = (
        f'<div class="bb bb-i" id="ce-{edit_id}"{poll}>'
        f'<div class="bt">{body}</div>'
        f'<div class="bm">Coach{slug_str} · {label}</div>'
        f'</div>'
    )
    return mgr, resp  # (unused mgr label kept for signature parity; see _coach_pair)


def _coach_response(
    edit_id: int, req: str, status: str, slug: str | None,  # noqa: ARG001
    old_t: str | None, new_t: str | None, summary: str | None, created_at: object,  # noqa: ARG001
) -> str:
    """Just the coach's answer/proposal bubble — the /coach/say response (the manager's own
    message is appended optimistically on the client the instant they hit send)."""
    return _coach_bubbles(edit_id, req, status, slug, old_t, new_t, summary, created_at)[1]


def _coach_pair(
    edit_id: int, req: str, status: str, slug: str | None,
    old_t: str | None, new_t: str | None, summary: str | None, created_at: object,
) -> str:
    """Manager message + coach response bubble pair — used to render the history."""
    mgr = _h.escape(t("who.manager"))
    resp = _coach_bubbles(edit_id, req, status, slug, old_t, new_t, summary, created_at)[1]
    return (
        f'<div class="bb bb-o mgr"><div class="bt">{_h.escape(req)}</div>'
        f'<div class="bm">{mgr} · {_ago(created_at)}</div></div>'  # type: ignore[arg-type]
        f'{resp}'
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

    mgr_lbl = _h.escape(t("who.manager"))
    think_msgs = _h.escape(_json.dumps(
        [t("coach.think1"), t("coach.think2"), t("coach.think3"), t("coach.think4")]))
    return (
        f'<div class="ch"><span class="ch-n">Coach KB</span></div>'
        f'{rules_section}'
        f'<div class="msgs" id="coach-msgs">{history}</div>'
        # a detailed 'thinking' line (cycling stages via JS) shown while the chat:deep call
        # is in flight — htmx toggles .htmx-request on #coach-thinking (the hx-indicator).
        f'<div id="coach-thinking" class="htmx-indicator coach-think" data-msgs=\'{think_msgs}\'>'
        f'<span class="spin"></span> <span id="coach-think-txt"></span></div>'
        f'<div class="fin">'
        # coachSend appends the manager's own bubble instantly (optimistic, like a real chat)
        # and starts the cycling status; the POST returns only the coach's reply bubble.
        f'<form class="fin-row" data-mgr="{mgr_lbl}"'
        f' hx-post="/ui/coach/say" hx-target="#coach-msgs" hx-swap="beforeend"'
        f' hx-indicator="#coach-thinking"'
        f' hx-on::before-request="coachSend(this)"'
        f' hx-on::after-request="coachThinkStop();scrollMsgs(\'coach\')">'
        f'<textarea name="request" rows="2" placeholder="{ph}"'
        f' onkeydown="entSend(event)"></textarea>'
        f'<button class="bsn">{send_lbl}</button></form>'
        f'</div>'
    )


# ─── products panel ───────────────────────────────────────────────────────────

def products_panel_html(products: list) -> str:
    """Clickable list of products with sort_order explanation. Click row → edit form.
    Rows: (id, slug, title, is_active, sort_order, kind, branch_name)."""
    title = _h.escape(t("nav.products"))
    hint = _h.escape(t("prod.sort_hint"))
    create_lbl = _h.escape(t("prod.create"))
    # when the view spans >1 branch, badge each row so per-branch copies of the same slug
    # read as distinct rows, not duplicates (same pattern as the multi-branch inbox list).
    multi_branch = len({p[6] for p in products if len(p) > 6}) > 1

    def _badges(p: object) -> str:
        b = ""
        if len(p) > 6 and multi_branch:
            b += f'<span class="br-badge">{_h.escape(str(p[6]))}</span>'
        if len(p) > 5 and p[5] and p[5] != "course":
            b += f'<span class="kind-badge">{_h.escape(str(p[5]))}</span>'
        return b

    rows = "".join(
        f'<tr class="kdoc" style="cursor:pointer"'
        f' hx-get="/ui/products/{p[0]}/edit" hx-target="#main"'
        f' hx-push-url="/ui/products/{p[0]}/edit">'
        f'<td><strong style="color:#e8eef4">{_h.escape(str(p[2]))}</strong>{_badges(p)}'
        f'<br><span class="kdoc-slug">{_h.escape(str(p[1]))}</span></td>'
        f'<td><span class="pill {"p-ok" if p[3] else "p-off"}">{"✓" if p[3] else "✗"}</span></td>'
        f'<td style="color:#6b7685;font-size:.8rem;text-align:center">{p[4]}</td>'
        f'</tr>'
        for p in products  # (id, slug, title, is_active, sort_order, kind, branch_name)
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
    hist_btn = (
        f'<a class="btn-sm" hx-get="/ui/products/{prod_id}/history" hx-target="#main"'
        f' hx-push-url="/ui/products/{prod_id}/history" style="margin-left:auto">'
        f'🕘 {_h.escape(t("kb.history"))}</a>' if prod_id else ""
    )
    return (
        f'<div class="ch">'
        f'<span class="ch-n">{_h.escape(title or slug or back_lbl)}</span>'
        f'{f"<span class=ch-slug>{_h.escape(slug)}</span>" if slug else ""}'
        f'{hist_btn}'
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


_TZ_LIST: list[tuple[int, str]] = [
    (14, "UTC+14 — Kiritimati"),
    (13, "UTC+13 — Samoa (Apia), Nuku'alofa"),
    (12, "UTC+12 — Auckland, Fiji, Petropavlovsk"),
    (11, "UTC+11 — Solomon Islands, Vladivostok"),
    (10, "UTC+10 — Sydney, Brisbane, Guam"),
    (9,  "UTC+9  — Tokyo, Seoul, Yakutsk"),
    (8,  "UTC+8  — Beijing, Singapore, KL, Manila, Irkutsk"),
    (7,  "UTC+7  — Jakarta, Bangkok, Hanoi, Krasnoyarsk"),
    (6,  "UTC+6  — Dhaka, Almaty, Omsk"),
    (5,  "UTC+5  — Karachi, Tashkent, Ekaterinburg"),
    (4,  "UTC+4  — Dubai, Baku, Yerevan"),
    (3,  "UTC+3  — Moscow, Riyadh, Nairobi"),
    (2,  "UTC+2  — Cairo, Johannesburg, Helsinki"),
    (1,  "UTC+1  — Paris, Berlin, Lagos"),
    (0,  "UTC+0  — London, Lisbon, Reykjavik"),
    (-1, "UTC−1  — Cape Verde, Azores"),
    (-2, "UTC−2  — South Georgia"),
    (-3, "UTC−3  — Buenos Aires, Brasilia, São Paulo"),
    (-4, "UTC−4  — New York (EDT), Santiago, La Paz"),
    (-5, "UTC−5  — New York (EST), Lima, Bogotá"),
    (-6, "UTC−6  — Mexico City, Chicago, Guatemala"),
    (-7, "UTC−7  — Denver, Phoenix"),
    (-8, "UTC−8  — Los Angeles, Vancouver, Seattle"),
    (-9, "UTC−9  — Alaska (Anchorage)"),
    (-10, "UTC−10 — Honolulu, Hawaii"),
    (-11, "UTC−11 — Pago Pago, Midway"),
    (-12, "UTC−12 — Baker Island, Howland Island"),
]


def _tz_opts(current: int) -> str:
    return "".join(
        f'<option value="{offset}" {"selected" if offset == current else ""}>'
        f'{label}</option>'
        for offset, label in _TZ_LIST
    )


def branch_edit_html(
    bid: int | None,
    name: str,
    lang: str,
    tz: int,
    is_active: bool,
    seeded: bool = False,
    kb_source_branch_id: int | None = None,
    other_branches: list[tuple[int, str]] | None = None,
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
        f'<label class="frm-lbl">{tz_lbl}</label>'
        f'<select class="act-sel" name="tz_offset_h"'
        f' style="width:100%;padding:.32rem .35rem">{_tz_opts(tz)}</select></div>'
        f'<div class="frm-grp" style="display:flex;align-items:center;gap:.5rem">'
        f'<input type="checkbox" name="is_active" id="br-active" {active_checked}>'
        f'<label class="frm-lbl" for="br-active" style="margin:0">{active_lbl}</label></div>'
        + (_kb_link_field(kb_source_branch_id, other_branches or [])
           if bid is not None else "")
        + f'<button type="submit" class="btn-sm btn-p">{save_lbl}</button>'
        + '</form>'
        + (_kb_copy_section(bid, kb_source_branch_id, other_branches or [])
           if bid is not None else "")
        + (_channels_section(bid) if bid is not None else "")
        + '</div>'
    )


def _kb_link_field(kb_source: int | None, others: list[tuple[int, str]]) -> str:
    """Inside the branch form: link this branch's KB to another (live). Saved on Save."""
    opts = '<option value="">— своя база знаний —</option>' + "".join(
        f'<option value="{i}" {"selected" if i == kb_source else ""}>{_h.escape(nm)}</option>'
        for i, nm in others)
    return (
        '<div class="frm-grp"><label class="frm-lbl">База знаний из филиала</label>'
        f'<select class="act-sel" name="kb_source_branch_id"'
        f' style="width:100%;padding:.32rem .35rem">{opts}</select></div>')


def _kb_copy_section(bid: int, kb_source: int | None, others: list[tuple[int, str]]) -> str:
    """Below the form: a one-time copy of another branch's KB, and the linked-note."""
    linked = ('<div class="hint" style="color:#e2b33d;margin:.3rem 0">База берётся из '
              'другого филиала (read-only здесь). Правь её в филиале-источнике.</div>'
              if kb_source else "")
    if not others:
        return linked
    opts = "".join(f'<option value="{i}">{_h.escape(nm)}</option>' for i, nm in others)
    # hx-target used to be "#panel" — no such element exists anywhere in this app (the
    # branch edit form itself renders inside "#main", same as every other nav panel), so
    # the copy request had nowhere to swap its response into: clicking "Скопировать" did
    # run the copy server-side but the button visibly did nothing. Fixed to "#main", plus
    # an hx-indicator spinner and hx-disabled-elt so a copy in progress is visible and
    # can't be double-submitted.
    return (
        '<div style="margin-top:.7rem;border-top:1px solid #2d3748;padding-top:.6rem">'
        + linked +
        f'<form id="kbcp-{bid}" hx-post="/ui/branches/{bid}/copy-kb"'
        ' hx-target="#main" hx-swap="innerHTML"'
        ' hx-indicator="#kbcp-ind" hx-disabled-elt="find button"'
        ' hx-confirm="Скопировать базу знаний из выбранного филиала? Текущая база этого'
        ' филиала будет заменена." style="display:flex;gap:.4rem;align-items:center">'
        '<span class="hint" style="min-width:96px">Скопировать из:</span>'
        f'<select class="act-sel" name="src_branch_id" style="flex:1">{opts}</select>'
        '<button class="btn-sm" type="submit">Скопировать</button>'
        # no inline display: here — .htmx-indicator{display:none} must win until htmx adds
        # .htmx-request during the request (an inline display would always override it).
        '<span id="kbcp-ind" class="htmx-indicator"'
        ' style="font-size:.78rem;color:#8899aa">'
        '<span class="spin" style="margin-right:.35rem;vertical-align:middle"></span>'
        'Копируется…</span>'
        '</form>'
        + '</div>')


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
        f'<div style="font-size:.72rem;color:#8a94a6;margin:-.3rem 0 .6rem">'
        f'{_h.escape(t("ch.active_hint"))}</div>'
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


def _ch_ig_form(
    ch_id: int, step: str = "login", flow_id: str = "", error: str = "",
    kind: str = "", username: str = "",
) -> str:
    """Two-step Instagram connect flow: (1) credentials, (2) resolving whatever Instagram
    asked for. Step 2's content switches on `kind` — instagrapi hits THREE unrelated
    Instagram mechanisms that all land here:
    - `kind='2fa'` — real 2FA, code from an authenticator app/SMS, resolved by re-login.
    - `kind='challenge'` — a security "is this really you" check, code emailed/texted,
      resolved via challenge_resolve.
    - `kind='manual'` — a checkpoint instagrapi flags as NOT resolvable by any text code at
      all (Bloks redirect / native in-app approval) — no code field; only a "confirm in the
      real Instagram app, then retry" button, reusing the same client/device fingerprint.
    Showing all three as a bare "2FA code" field used to make a challenge/manual checkpoint
    look like a missing-2FA problem, so turning 2FA off didn't stop the prompt (real
    report, 2026-07-08).

    IMPORTANT — hx-disabled-elt/hx-indicator on the <form> ITSELF, not per-button:
    htmx 1.9.12 has a real bug (confirmed empirically, not documented) where an element
    with hx-disabled-elt="find button" and/or hx-indicator="find .htmx-indicator" on an
    ANCESTOR <form> silently swallows the click of any OTHER descendant that has its own
    independent hx-get/hx-post — the request never leaves the browser, no console error.
    This broke "Start over" and the app-confirm button from day one (real report,
    2026-07-09: clicking either did visibly nothing). Fix: never put these two attributes
    on a <form> that contains more than one independently-triggering element — set
    hx-disabled-elt="this" and hx-indicator="#<id>" on each button individually instead."""
    err = _ch_err(error)
    if step == "2fa":
        spin_id = f"ig-spin-{ch_id}"
        spin = (
            f'<span id="{spin_id}" class="htmx-indicator" style="margin-left:.5rem;'
            f'color:#8b98a5;font-size:.72rem">⏳ {_h.escape(t("ch.logging_in"))}</span>'
        )
        who = (
            f'<div style="font-size:.76rem;color:#9aa5b1;margin-bottom:.6rem">'
            f'{_h.escape(t("ch.for_account"))} <b>@{_h.escape(username)}</b></div>'
            if username else ""
        )
        if kind == "manual":
            return (
                f'{_ch_step(t("ch.step2"))}{who}{err}'
                f'{_ch_hint(t("ch.hint_manual"))}'
                f'<form hx-post="/ui/channels/{ch_id}/ig/verify" hx-target="#ch-form"'
                f' hx-swap="innerHTML" style="max-width:340px">'
                f'<input type="hidden" name="flow_id" value="{_h.escape(flow_id)}">'
                f'<button type="submit" class="btn-sm btn-p" hx-disabled-elt="this"'
                f' hx-indicator="#{spin_id}">{_h.escape(t("ch.retry_manual"))}</button>'
                f'<button type="button" class="btn-sm btn-g" style="margin-left:.4rem"'
                f' hx-disabled-elt="this" hx-indicator="#{spin_id}"'
                f' hx-get="/ui/channels/{ch_id}/form" hx-target="#ch-form" hx-swap="innerHTML">'
                f'{_h.escape(t("ch.start_over"))}</button>{spin}'
                f'</form>'
            )
        is_challenge = kind == "challenge"
        code_lbl = t("ch.code_challenge") if is_challenge else t("ch.code_2fa")
        hint = t("ch.hint_challenge") if is_challenge else t("ch.hint_2fa")
        # Instagram can fire the 2FA code prompt AND an in-app "was this you?" push for
        # the SAME login attempt at once. If the operator already approved the push,
        # making them type a code that isn't even needed just to reach the eventual
        # manual-retry step is pointless — this button skips straight to a plain retry.
        app_confirm_btn = (
            f'<div style="margin-top:.4rem">'
            f'<button type="button" class="btn-sm btn-g"'
            f' hx-post="/ui/channels/{ch_id}/ig/verify" hx-target="#ch-form"'
            f' hx-swap="innerHTML" hx-include="closest form" hx-vals=\'{{"skip_code":"1"}}\''
            f' hx-disabled-elt="this" hx-indicator="#{spin_id}">'
            f'{_h.escape(t("ch.already_confirmed"))}</button></div>'
            if not is_challenge else ""
        )
        return (
            f'{_ch_step(t("ch.step2"))}{who}{err}'
            f'<form hx-post="/ui/channels/{ch_id}/ig/verify" hx-target="#ch-form"'
            f' hx-swap="innerHTML" style="max-width:340px">'
            f'<input type="hidden" name="flow_id" value="{_h.escape(flow_id)}">'
            f'<div class="frm-grp">'
            f'<label class="frm-lbl">{_h.escape(code_lbl)}</label>'
            f'<input class="frm-inp" name="code" autocomplete="one-time-code" autofocus></div>'
            f'{_ch_hint(hint)}'
            f'<button type="submit" class="btn-sm btn-p" hx-disabled-elt="this"'
            f' hx-indicator="#{spin_id}">{_h.escape(t("ch.verify"))}</button>'
            f'{app_confirm_btn}'
            f'<button type="button" class="btn-sm btn-g" style="margin-left:.4rem"'
            f' hx-disabled-elt="this" hx-indicator="#{spin_id}"'
            f' hx-get="/ui/channels/{ch_id}/form" hx-target="#ch-form" hx-swap="innerHTML">'
            f'{_h.escape(t("ch.start_over"))}</button>{spin}'
            f'</form>'
        )
    spin = (
        f'<span class="htmx-indicator" style="margin-left:.5rem;color:#8b98a5;'
        f'font-size:.72rem">⏳ {_h.escape(t("ch.logging_in"))}</span>'
    )
    return (
        f'{_ch_step(t("ch.step1"))}{err}'
        f'<form hx-post="/ui/channels/{ch_id}/ig/start" hx-target="#ch-form"'
        f' hx-swap="innerHTML" hx-disabled-elt="find button"'
        f' hx-indicator="find .htmx-indicator" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.username"))}</label>'
        f'<input class="frm-inp" name="username" autocomplete="username"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.password"))}</label>'
        f'<input class="frm-inp" name="password" type="password"'
        f' autocomplete="current-password"></div>'
        f'{_ch_hint(t("ch.hint_login"))}'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.ig_login"))}</button>{spin}'
        f'</form>'
        # Session-JSON import is a power-user escape hatch (paste an already-logged-in
        # instagrapi session, skip the login/2FA dance entirely) — collapsed by default so
        # it doesn't compete with the normal path for attention.
        f'<details style="margin-top:.7rem">'
        f'<summary style="font-size:.72rem;color:#6b7685;cursor:pointer">'
        f'{_h.escape(t("ch.advanced_json"))}</summary>'
        f'<form hx-post="/ui/channels/{ch_id}/ig/start" hx-target="#ch-form"'
        f' hx-swap="innerHTML" hx-disabled-elt="find button"'
        f' hx-indicator="find .htmx-indicator" style="max-width:360px;margin-top:.5rem">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.ig_json"))}</label>'
        f'<textarea class="frm-ta" name="session_json" rows="3"'
        f' placeholder=\'{{"device_settings":...}}\' style="min-height:4rem"></textarea></div>'
        f'{_ch_hint(t("ch.hint_json"))}'
        f'<button type="submit" class="btn-sm">{_h.escape(t("ch.save"))}</button>{spin}'
        f'</form></details>'
    )


def _ch_meta_form(ch_id: int, error: str = "") -> str:
    return (
        f'{_ch_err(error)}'
        f'<form hx-post="/ui/channels/{ch_id}/meta/connect"'
        f' hx-target="#ch-form" hx-swap="innerHTML" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">Платформа</label>'
        f'<select class="act-sel" name="platform" style="width:100%;padding:.3rem .35rem">'
        f'<option value="facebook_page">Facebook Page (Messenger)</option>'
        f'<option value="instagram_graph">Instagram Graph API</option>'
        f'</select></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.page_id"))}</label>'
        f'<input class="frm-inp" name="page_id" placeholder="123456789"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.token"))}'
        f' <span style="color:#4a5568;font-size:.7rem">(Graph API)</span></label>'
        f'<input class="frm-inp" name="token" placeholder="EAAxx...">'
        f'<div style="font-size:.7rem;color:#8a94a6;margin-top:.2rem">'
        f'Пусто + Facebook Page = токен выведется из System User токена коннектора '
        f'(настройки филиала → meta_system_user_token)</div></div>'
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

def _fb_ad_url(ad_id: object, business_id: str = "", account_id: str = "") -> str:
    """Link to the ad in Meta's public Ad Library, which resolves ANY ad by id.

    An Ads Manager deep link only works when the ad lives in the operator's configured ad
    account — but verified live, the ad_id Instagram gives (ad_context_data.ad_id) is almost
    never in that account: these lead-gen ads run under a different (agency) account, all
    published from the same FB page. Ads Manager then shows 'not found'. The Ad Library keys
    off the ad id alone, so it always opens the real, live ad (creative, copy, status,
    advertiser) — view-only, no spend/results, but it reliably lands on the right ad.
    business_id/account_id are kept for signature stability; the Ad Library needs neither."""
    return _h.escape(f"https://www.facebook.com/ads/library/?id={ad_id}")


def admap_cell_inner(
    ad_id: object, mapped: str | None, suggested: str | None,
    products: list[tuple[str, str]],
) -> str:
    """Inner HTML of the product-mapping cell: a <select> (upserts the map on change) plus,
    when the ad is still unmapped, a one-click ⚡ suggestion chip from history. Shared by
    the reports table and the POST /ui/ads/{ad_id}/product response so both render identically."""
    aid = _h.escape(str(ad_id))
    opts = f'<option value="">— {_h.escape(t("rep.ad_no_product"))} —</option>'
    for slug, title in products:
        sel = " selected" if mapped == slug else ""
        opts += f'<option value="{_h.escape(slug)}"{sel}>{_h.escape(title)}</option>'
    sel_html = (
        f'<select class="admap-sel" name="product" hx-post="/ui/ads/{aid}/product"'
        f' hx-trigger="change" hx-target="#admap-{aid}" hx-swap="innerHTML">{opts}</select>'
    )
    hint = ""
    if not mapped and suggested:
        title = dict(products).get(suggested, suggested)
        hint = (
            f'<button class="admap-sug" hx-post="/ui/ads/{aid}/product"'
            f' hx-vals=\'{{"product":"{_h.escape(suggested)}"}}\' hx-trigger="click"'
            f' hx-target="#admap-{aid}" hx-swap="innerHTML"'
            f' title="{_h.escape(t("rep.ad_suggest_hint"))}">⚡ {_h.escape(title)}</button>'
        )
    return sel_html + hint


def _ad_menu_cell(ad_id: object, ad_media_id: object, fb_url: str) -> str:
    """Ad-id cell: a <details> menu (open this ad's chats | open in FB) + an IG-post link."""
    aid = _h.escape(str(ad_id))
    items = (
        f'<a href="/ui/inbox?ad_id={aid}">💬 {_h.escape(t("rep.ad_open_chats"))}</a>'
        f'<a href="{fb_url}" target="_blank" rel="noreferrer">'
        f'↗ {_h.escape(t("rep.ad_open_fb"))}</a>'
    )
    cell = (
        f'<details class="admenu"><summary>{aid}</summary>'
        f'<div class="admenu-pop">{items}</div></details>'
    )
    if ad_media_id:
        post = ig_post_url(str(ad_media_id))
        if post:
            cell += (
                f' <a class="ad-ig" href="{_h.escape(post)}" target="_blank" rel="noreferrer"'
                f' data-ig="{_h.escape(str(ad_media_id))}" title="IG post">📷</a>'
            )
    return cell


# Client-side sort + per-column filter for the ad-funnel table. Inline so it ships with the
# htmx fragment; handlers are called via inline on* attrs, so redefining on each swap is a
# no-op (no listener stacking). A cell's sort/filter value is the mapping <select>'s value,
# else the ad-id <summary>, else the cell text — so the interactive product/ad cells sort too.
_AD_FUNNEL_JS = (
    "<script>"
    "function _adCellVal(td){if(!td)return'';"
    "var s=td.querySelector('select.admap-sel');if(s)return s.value;"
    "var sm=td.querySelector('summary');if(sm)return sm.textContent.trim();"
    "return td.textContent.trim();}"
    "function repSort(th){var tbl=th.closest('table');"
    "var idx=Array.prototype.indexOf.call(th.parentNode.children,th);"
    "var num=th.getAttribute('data-num')==='1';var asc=th.getAttribute('data-asc')!=='1';"
    "tbl.querySelectorAll('th.rep-sort').forEach(function(h){h.removeAttribute('data-asc');"
    "var a=h.querySelector('.rep-arr');if(a)a.textContent='';});"
    "th.setAttribute('data-asc',asc?'1':'0');"
    "var ar=th.querySelector('.rep-arr');if(ar)ar.textContent=asc?' \\u25B2':' \\u25BC';"
    "var tb=tbl.querySelector('tbody');"
    "var rs=Array.prototype.slice.call(tb.querySelectorAll('tr'));"
    "rs.sort(function(a,b){var x=_adCellVal(a.children[idx]),y=_adCellVal(b.children[idx]);"
    "if(num){x=parseFloat(x.replace(/[^0-9.\\-]/g,''))||0;"
    "y=parseFloat(y.replace(/[^0-9.\\-]/g,''))||0;return asc?x-y:y-x;}"
    "return asc?x.localeCompare(y):y.localeCompare(x);});"
    "rs.forEach(function(r){tb.appendChild(r);});}"
    "function repFilter(el){var tbl=el.closest('table');var fr=el.closest('tr');"
    "var fs=Array.prototype.slice.call(fr.querySelectorAll('.rep-f')).map(function(f){"
    "return{idx:Array.prototype.indexOf.call(f.parentNode.parentNode.children,f.parentNode),"
    "type:f.getAttribute('data-f'),val:f.value.trim().toLowerCase()};});"
    "var tb=tbl.querySelector('tbody');"
    "Array.prototype.slice.call(tb.querySelectorAll('tr')).forEach(function(r){var show=true;"
    "fs.forEach(function(f){if(!f.val)return;"
    "var cv=_adCellVal(r.children[f.idx]).toLowerCase();"
    "if(f.type==='text'){if(cv.indexOf(f.val)<0)show=false;}"
    "else if(f.type==='eq'){if(cv!==f.val)show=false;}"
    "else if(f.type==='min'){var n=parseFloat(cv.replace(/[^0-9.\\-]/g,''))||0;"
    "if(n<parseFloat(f.val))show=false;}});"
    "r.style.display=show?'':'none';});}"
    "</script>"
)


def _count_cell(aid: str, grp: str, n: int, color: str) -> str:
    """A funnel count that links to the matching chat list (ad + stage group). grp '' =
    every chat of the ad; otherwise a group from AD_FUNNEL_GROUPS (pipeline|won|dormant)."""
    style = f' style="color:{color}"' if color else ""
    qs = f"/ui/inbox?ad_id={aid}" + (f"&grp={grp}" if grp else "")
    return f'<td class="rep-n"{style}><a class="rep-lnk" href="{qs}">{n}</a></td>'


def _ad_funnel_header(cols: list[tuple[str, bool, str, bool]],
                      products: list[tuple[str, str]]) -> str:
    """Two header rows: clickable sort headers + a per-column filter row.
    cols entries: (label_key, numeric, filter_kind[text|eq|min], align_right)."""
    ths = ""
    for key, num, _kind, right in cols:
        style = ' style="text-align:right"' if right else ""
        ths += (
            f'<th class="rep-sort"{style} data-num="{1 if num else 0}"'
            f' onclick="repSort(this)">{_h.escape(t(key))}<span class="rep-arr"></span></th>'
        )
    fths = ""
    for _key, _num, kind, right in cols:
        style = ' style="text-align:right"' if right else ""
        if kind == "eq":  # product exact-match dropdown
            opts = f'<option value="">{_h.escape(t("rep.f_all"))}</option>' + "".join(
                f'<option value="{_h.escape(s)}">{_h.escape(tt)}</option>' for s, tt in products)
            ctrl = f'<select class="rep-f" data-f="eq" onchange="repFilter(this)">{opts}</select>'
        elif kind == "min":  # numeric ≥ threshold
            ctrl = ('<input class="rep-f" data-f="min" type="number" min="0"'
                    ' placeholder="≥" oninput="repFilter(this)">')
        else:  # substring match
            ctrl = ('<input class="rep-f" data-f="text"'
                    ' placeholder="🔍" oninput="repFilter(this)">')
        fths += f'<th{style}>{ctrl}</th>'
    return f'<thead><tr>{ths}</tr><tr class="rep-fltr">{fths}</tr></thead>'


def _ad_funnel_html(
    rows: list, business_id: str = "", account_id: str = "", *,
    mappings: dict[str, str] | None = None,
    suggestions: dict[str, str] | None = None,
    products: list[tuple[str, str]] | None = None,
) -> str:
    """Per-ad funnel table: leads from each ad → pipeline / won / conv%, an ad-action menu,
    and (single-branch only) an operator product-mapping column with a history suggestion.
    Columns sort on header click and filter via the per-column row (client-side, see JS)."""
    if not rows:
        return ""
    mappings = mappings or {}
    suggestions = suggestions or {}
    show_map = bool(products)  # product column only when a single branch is in scope
    hdr = (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:1rem 0 .35rem">'
        f'{_h.escape(t("rep.ad_funnel"))}</h3>'
    )
    body = ""
    for ad_id, ad_media_id, total, pipeline, won, dormant in rows:
        total = int(total or 0)
        won = int(won or 0)
        conv = round(won / total * 100, 1) if total else 0.0
        fb = _fb_ad_url(ad_id, business_id, account_id)
        cell_inner = admap_cell_inner(
            ad_id, mappings.get(str(ad_id)), suggestions.get(str(ad_id)), products or [])
        map_cell = (
            f'<td class="admap" id="admap-{_h.escape(str(ad_id))}">'
            f'{cell_inner}'
            f'</td>'
        ) if show_map else ""
        aid = _h.escape(str(ad_id))
        body += (
            f'<tr><td>{_ad_menu_cell(ad_id, ad_media_id, fb)}</td>'
            f'{map_cell}'
            f'{_count_cell(aid, "", total, "")}'
            f'{_count_cell(aid, "pipeline", int(pipeline or 0), "#9b7aff")}'
            f'{_count_cell(aid, "won", won, "#51cf66")}'
            f'{_count_cell(aid, "dormant", int(dormant or 0), "#868e96")}'
            f'<td class="rep-n" style="color:#ffa94d">{conv}%</td></tr>'
        )
    cols: list[tuple[str, bool, str, bool]] = [("rep.ad", False, "text", False)]
    if show_map:
        cols.append(("rep.ad_product", False, "eq", False))
    cols += [
        ("rep.total", True, "min", True), ("rep.pipeline", True, "min", True),
        ("rep.won", True, "min", True), ("rep.dormant", True, "min", True),
        ("rep.conv", True, "min", True),
    ]
    head = _ad_funnel_header(cols, products or [])
    return (
        f'{hdr}<table class="rep-tbl rep-sortable">'
        f'{head}<tbody>{body}</tbody></table>{_AD_FUNNEL_JS}'
    )


# Pipeline order for the one-line funnel (side stages shown separately below).
_FUNNEL_PIPELINE = ("new", "nurturing", "qualifying", "presenting", "objection", "ready")
_FUNNEL_SIDE = ("handed_off", "dormant", "manager")

# Flow diagram: pipeline spine on the top lane, terminal exits on the bottom lane.
_FLOW_SPINE = ("new", "nurturing", "qualifying", "presenting", "objection", "ready", "handed_off")
_FLOW_EXITS = ("dormant", "manager")


def _funnel_flow_html(
    flow: list, reach: dict[str, int] | None = None, total_leads: int = 0,
) -> str:
    """The whole funnel as a server-rendered SVG flow (Sankey-style): each lead's real path
    from first message (entry) through every stage transition to an exit, reconstructed from the
    stage_event audit log. Link thickness ∝ distinct leads on that transition; node bar/label =
    distinct leads that passed through the stage (`reach`) — a real headcount ≤ total leads, so
    the entry bar never reads higher than the lead base. Falls back to edge-derived throughput
    when `reach` is absent. `total_leads` (all leads in the window) drives the standalone
    "no movement" bucket = leads that entered but have no transition yet, so entry + no-movement
    reconcile to the whole base. Each node has a hover <title> explaining how the stage is
    determined. Back-edges (e.g. presenting→qualifying) curve, so churn and drop-off are visible,
    not just the happy path. Empty (→ caller falls back to the line funnel) when there's no
    transition history for the window."""
    edges = [(str(a), str(b), int(c)) for a, b, c in flow if int(c) > 0 and str(a) != str(b)]
    if not edges:
        return ""
    out_sum: dict[str, int] = {}
    in_sum: dict[str, int] = {}
    for a, b, c in edges:
        out_sum[a] = out_sum.get(a, 0) + c
        in_sum[b] = in_sum.get(b, 0) + c
    edge_tp = {s: max(out_sum.get(s, 0), in_sum.get(s, 0)) for s in set(out_sum) | set(in_sum)}
    tp = {s: (reach.get(s, edge_tp[s]) if reach else edge_tp[s]) for s in edge_tp}
    max_tp = max(tp.values(), default=1) or 1
    max_c = max((c for _, _, c in edges), default=1) or 1

    vw, vh, bar_w = 700, 210, 16
    left, right, top_y, bot_y = 46, vw - 46, 66, 168
    step = (right - left) / (len(_FLOW_SPINE) - 1)
    pos: dict[str, tuple[float, float]] = {
        s: (left + i * step, top_y) for i, s in enumerate(_FLOW_SPINE)
    }
    pos["dormant"] = (left + 2 * step, bot_y)
    pos["manager"] = (left + 4 * step, bot_y)

    def bar_h(s: str) -> float:
        return max(10.0, min(96.0, tp.get(s, 0) / max_tp * 96))

    links = ""
    for a, b, c in sorted(edges, key=lambda e: e[2]):  # thin first so thick links draw on top
        if a not in pos or b not in pos:
            continue
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        fwd = x1 >= x0
        sx = x0 + bar_w / 2 if fwd else x0 - bar_w / 2
        tx = x1 - bar_w / 2 if fwd else x1 + bar_w / 2
        w = max(1.0, min(22.0, c / max_c * 22))
        mx = (sx + tx) / 2
        links += (
            f'<path d="M{sx:.0f},{y0:.0f} C{mx:.0f},{y0:.0f} {mx:.0f},{y1:.0f} {tx:.0f},{y1:.0f}"'
            f' fill="none" stroke="{_STAGE_COLOR.get(b, "#4a5568")}" stroke-width="{w:.1f}"'
            f' opacity="0.3"/>'
        )
    nodes = ""
    for s, (x, y) in pos.items():
        t_s = tp.get(s, 0)
        if t_s <= 0:
            continue
        h = bar_h(s)
        col = _STAGE_COLOR.get(s, "#4a5568")
        # 'new' is the entry point (every lead's first message), not a "still-new" bucket —
        # label it "entry" so its throughput doesn't read as current occupancy of stage new.
        lbl = _h.escape(t("flow.entry") if s == "new" else t(f"stage.{s}"))
        below = s in _FLOW_EXITS
        ty = y + h / 2 + 12 if below else y - h / 2 - 5
        desc = _h.escape(t("flow.entry_desc") if s == "new" else t(f"sdesc.{s}"))
        nodes += (
            f'<a href="/ui/inbox?stage={s}" style="cursor:pointer">'
            f'<g><title>{lbl}: {t_s} — {desc}</title>'
            f'<rect x="{x - bar_w / 2:.0f}" y="{y - h / 2:.0f}" width="{bar_w}" height="{h:.0f}"'
            f' rx="3" fill="{col}"/>'
            f'<text x="{x:.0f}" y="{ty:.0f}" text-anchor="middle" fill="#9aa7b5"'
            f' font-size="9">{lbl} · {t_s}</text></g></a>'
        )
    # "no movement": leads that entered (first message) but have no transition logged yet, so
    # entry + this = the whole base. A standalone dashed node under the entry (no links flow to
    # it — that is the point: they never moved).
    moved = reach.get("*", 0) if reach else 0
    stuck = max(0, total_leads - moved)
    if stuck > 0:
        sx, sy = left, bot_y
        sh = max(10.0, min(96.0, stuck / max_tp * 96))
        slbl = _h.escape(t("flow.stuck"))
        sdesc = _h.escape(t("flow.stuck_desc"))
        nodes += (
            f'<g><title>{slbl}: {stuck} — {sdesc}</title>'
            f'<rect x="{sx - bar_w / 2:.0f}" y="{sy - sh / 2:.0f}" width="{bar_w}"'
            f' height="{sh:.0f}" rx="3" fill="#3a4250" stroke="#4a5568"'
            f' stroke-dasharray="3 2"/>'
            f'<text x="{sx:.0f}" y="{sy + sh / 2 + 12:.0f}" text-anchor="middle" fill="#6b7685"'
            f' font-size="9">{slbl} · {stuck}</text></g>'
        )
    svg = (
        f'<svg viewBox="0 0 {vw} {vh}" style="width:100%;max-width:720px;height:auto"'
        f' xmlns="http://www.w3.org/2000/svg">{links}{nodes}</svg>'
    )
    return (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:.9rem 0 .35rem">'
        f'{_h.escape(t("rep.funnel"))}</h3><div class="seg-tree">{svg}</div>'
    )


def _funnel_line_html(stage_counts: dict[str, int]) -> str:
    """One-line sales funnel: each pipeline stage as a step (count + % of total), a tooltip
    describing HOW the stage is determined, in order — extensible by editing the tuples."""
    total = sum(stage_counts.values()) or 1
    steps = ""
    for s in _FUNNEL_PIPELINE:
        n = stage_counts.get(s, 0)
        pct = round(n / total * 100)
        color = _STAGE_COLOR.get(s, "#4a5568")
        steps += (
            f'<div class="fnl-step" title="{_h.escape(t(f"sdesc.{s}"))}">'
            f'<div class="fnl-bar" style="background:{color}"></div>'
            f'<div class="fnl-num">{n}</div>'
            f'<div class="fnl-nm">{_h.escape(t(f"stage.{s}"))}</div>'
            f'<div class="fnl-pct">{pct}%</div></div>'
        )
    side = ""
    for s in _FUNNEL_SIDE:
        n = stage_counts.get(s, 0)
        if not n:
            continue
        side += (
            f'<span class="fnl-side" title="{_h.escape(t(f"sdesc.{s}"))}"'
            f' style="border-color:{_STAGE_COLOR.get(s,"#4a5568")}">'
            f'{_h.escape(t(f"stage.{s}"))} {n}</span>'
        )
    side_row = f'<div class="fnl-side-row">{side}</div>' if side else ""
    return (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:.9rem 0 .4rem">'
        f'{_h.escape(t("rep.funnel"))}</h3>'
        f'<div class="fnl-line">{steps}</div>{side_row}'
    )


_QUICK_RANGES = (
    ("1h", "rep.range_1h"), ("2h", "rep.range_2h"), ("4h", "rep.range_4h"),
    ("8h", "rep.range_8h"), ("12h", "rep.range_12h"), ("24h", "rep.range_24h"),
    ("7d", "rep.range_7d"), ("30d", "rep.range_30d"),
    ("60d", "rep.range_60d"), ("90d", "rep.range_90d"), ("", "rep.range_all"),
)


def _quick_range_html(active_range: str) -> str:
    """One-click preset buttons — each fires its own htmx GET immediately (no Apply
    click, no date typing) and clears the manual date pickers since the two are
    mutually exclusive filters."""
    chips = "".join(
        f'<a class="rep-preset{" on" if key == active_range else ""}"'
        f' hx-get="/ui/reports/panel{f"?range={key}" if key else ""}" hx-target="#main"'
        f' hx-push-url="true">{_h.escape(t(label))}</a>'
        for key, label in _QUICK_RANGES
    )
    return f'<div class="rep-presets">{chips}</div>'


def _date_range_form_html(date_from: str, date_to: str, active_range: str = "") -> str:
    """Quick-range presets plus From/To date pickers filtering the whole report by the
    lead's conversation-start date.

    Auto-applies on change of EITHER date (no Apply click needed, no full reload) — htmx's
    hx-trigger="change" listens on the form and fires from either input independently."""
    return (
        f'{_quick_range_html(active_range)}'
        f'<form class="rep-dates" hx-get="/ui/reports/panel" hx-target="#main"'
        f' hx-push-url="true" hx-trigger="change">'
        f'<label>{_h.escape(t("rep.from"))}'
        f'<input type="date" name="date_from" value="{_h.escape(date_from)}"></label>'
        f'<label>{_h.escape(t("rep.to"))}'
        f'<input type="date" name="date_to" value="{_h.escape(date_to)}"></label>'
        f'<span class="rep-dhint">{_h.escape(t("rep.date_hint"))}</span>'
        f'</form>'
    )


# Classifier/intent palette — a temperature scale, deliberately using DIFFERENT hexes than
# _STAGE_COLOR's funnel/pipeline palette above, so the same color never means two different
# things when a segment card and a stage box sit side by side (they used to share exact hex
# values — hot==manager, warm==ready, cold==new, no_budget==qualifying — which is why the
# reports panel read as one undifferentiated wall of color). 'student' is an audience, not a
# segment here — see _AUD_ORDER.
_SEG_META = (  # (key, colour, i18n label) — this tuple order IS the display order everywhere
    ("hot", "#f06595", "seg.hot"),
    ("warm", "#ffd43b", "seg.warm"),
    ("cold", "#748ffc", "seg.cold"),
    ("no_budget", "#be4bdb", "seg.no_budget"),
    ("non_target", "#5c636a", "seg.non_target"),
    ("unclear", "#4a5568", "seg.unclear"),
)
# Fixed rank by temperature (hottest first, unclassified last) — the SAME order in every
# audience block, regardless of win-rate or volume, so "hot/warm/cold/..." always reads
# top-to-bottom the same way instead of reshuffling per block (that's what made the reports
# panel look inconsistent — win-rate sort put segments in a different order per audience).
_SEG_RANK = {k: i for i, (k, _c, _l) in enumerate(_SEG_META)}
_AUD_ORDER = ("adult", "unknown", "student")  # sub-tree order; 'unknown' = not yet classified


def _segment_subtree_svg(
    rows: list, root_label: str, aud_key: str = "", seg_stage_map: dict | None = None,
) -> str:
    """One audience's segment tree: a root node (its total) branching into each intent
    segment, link thickness ∝ volume, in the fixed temperature order (_SEG_RANK) — the same
    order in every audience block. To the RIGHT of each segment node, a row of small stage
    boxes (the funnel inside that segment): one box per non-empty stage with its count,
    clickable to that audience+segment+stage's chats. `seg_stage_map` = {lead_type:
    {stage: count}}."""
    meta = {k: (c, lbl) for k, c, lbl in _SEG_META}
    leaves = []
    for key, n, won in rows:
        if n <= 0:
            continue
        color, lbl = meta.get(key, ("#4a5568", "seg.unclear"))
        leaves.append((color, _h.escape(t(lbl)), n, won, key))
    if not leaves:
        return ""
    leaves.sort(key=lambda r: _SEG_RANK.get(r[4], len(_SEG_META)))
    total = sum(r[2] for r in leaves)
    n_seg = len(leaves)
    ssm = seg_stage_map or {}
    row_h, top, node_x, node_w, node_h = 46, 14, 372, 236, 34
    link_x0, mid_x = 128, 250
    bx0, bw, bh, bgap = node_x + node_w + 12, 46, 34, 6  # stage boxes right of each node
    max_boxes = max(
        (sum(1 for st in _ALL_STAGES if int(ssm.get(k, {}).get(st, 0) or 0) > 0)
         for _c, _l, _n, _w, k in leaves), default=0)
    w = bx0 + max_boxes * (bw + bgap) if max_boxes else node_x + node_w + 10
    height = top * 2 + n_seg * row_h
    root_cy = height // 2
    links, nodes = "", ""
    for i, (color, label, cnt, won, key) in enumerate(leaves):
        cy = top + row_h // 2 + i * row_h
        thick = max(2, round(cnt / total * 34))
        links += (
            f'<path d="M{link_x0},{root_cy} C{mid_x},{root_cy} {mid_x},{cy} {node_x},{cy}"'
            f' fill="none" stroke="{color}" stroke-width="{thick}" opacity="0.5"/>'
        )
        pct = round(cnt / total * 100)
        won_pct = round(won / cnt * 100) if cnt else 0
        y = cy - node_h // 2
        desc = _h.escape(t(f"segdesc.{key}"))
        tip = t("seg.tip", label=label, cnt=cnt, pct=pct, won_pct=won_pct, desc=desc)
        aud_q = f"&audience={aud_key}" if aud_key else ""
        nodes += (
            f'<a href="/ui/inbox?lead_type={key}{aud_q}"'
            f' style="cursor:pointer">'
            f'<g><title>{tip}</title>'
            f'<rect x="{node_x}" y="{y}" width="{node_w}" height="{node_h}" rx="6"'
            f' fill="#141925" stroke="#2d3748"/>'
            f'<rect x="{node_x}" y="{y}" width="4" height="{node_h}" rx="2" fill="{color}"/>'
            f'<text x="{node_x + 14}" y="{cy - 2}" fill="{color}" font-size="12"'
            f' font-weight="600">{label}</text>'
            f'<text x="{node_x + node_w - 10}" y="{cy - 1}" text-anchor="end" fill="#e8eef4"'
            f' font-size="14" font-weight="700">{cnt}</text>'
            f'<text x="{node_x + 14}" y="{cy + 11}" fill="#6b7685" font-size="9">'
            f'{pct}% · won {won_pct}%</text></g></a>'
        )
        # stage boxes to the right — the funnel inside this segment
        by, j = cy - bh // 2, 0
        for st in _ALL_STAGES:
            c = int(ssm.get(key, {}).get(st, 0) or 0)
            if c <= 0:
                continue
            bx = bx0 + j * (bw + bgap)
            j += 1
            scol = _STAGE_COLOR.get(st, "#868e96")
            sicon = _STAGE_ICON.get(st, "•")
            slabel = _h.escape(t(f"stage.{st}"))
            stip = f"{slabel}: {c}"
            nodes += (
                f'<a href="/ui/inbox?lead_type={key}{aud_q}&stage={st}" style="cursor:pointer">'
                f'<g><title>{stip}</title>'
                f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="5"'
                f' fill="#141925" stroke="#2d3748"/>'
                f'<rect x="{bx}" y="{by}" width="{bw}" height="3" rx="1.5" fill="{scol}"/>'
                # icon caption above the count so each box reads on its own, not just on hover —
                # same glyph the main inbox funnel uses for this stage, tying the two views
                # together visually.
                f'<text x="{bx + bw / 2:.0f}" y="{by + 15}" text-anchor="middle"'
                f' font-size="10">{sicon}</text>'
                f'<text x="{bx + bw / 2:.0f}" y="{by + 28}" text-anchor="middle" fill="#e8eef4"'
                f' font-size="12" font-weight="700">{c}</text></g></a>'
            )
    root = (
        f'<rect x="6" y="{root_cy - 30}" width="122" height="60" rx="8" fill="#1a2230"'
        f' stroke="#2d3748"/>'
        f'<text x="67" y="{root_cy - 7}" text-anchor="middle" fill="#8899aa"'
        f' font-size="10">{root_label}</text>'
        f'<text x="67" y="{root_cy + 16}" text-anchor="middle" fill="#e8eef4"'
        f' font-size="22" font-weight="700">{total}</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {height}" style="width:100%;max-width:{w}px;height:auto"'
        f' xmlns="http://www.w3.org/2000/svg">{links}{root}{nodes}</svg>'
    )


def _segment_tree_html(segments: list, seg_stage_by_aud: dict | None = None) -> str:
    """Two-axis lead breakdown: one segment sub-tree per audience (adults, then students),
    each root showing that audience's total, branching into intent segments by win rate, with
    the stage funnel drawn as boxes to the right of each segment. Rows: (audience, lead_type,
    total, won); a legacy 3-tuple is audience 'adult'. `seg_stage_by_aud` =
    {aud: {lead_type: {stage: count}}}. Server-rendered SVG — no client JS."""
    by_aud: dict[str, list] = {}
    for s in segments:
        if len(s) >= 4:
            aud, key, n, won = str(s[0]), str(s[1]), int(s[2]), int(s[3] or 0)
        else:  # legacy (lead_type, total, won) with no audience axis
            aud, key, n, won = "adult", str(s[0]), int(s[1]), int(s[2] or 0)
        if n <= 0:
            continue
        by_aud.setdefault(aud, []).append((key, n, won))
    if not by_aud:
        return ""
    auds = [a for a in _AUD_ORDER if a in by_aud] + [a for a in by_aud if a not in _AUD_ORDER]
    single = len(auds) == 1
    blocks = ""
    for aud in auds:
        # With one audience, keep the familiar "Total leads" root; else name each audience.
        root_label = _h.escape(t("rep.total") if single else t(f"aud.{aud}"))
        svg = _segment_subtree_svg(by_aud[aud], root_label, aud_key=aud,
                                   seg_stage_map=(seg_stage_by_aud or {}).get(aud, {}))
        if not svg:
            continue
        cap = "" if single else (
            f'<div style="font-size:.72rem;color:#8899aa;margin:.6rem 0 .1rem;'
            f'font-weight:600">{_h.escape(t(f"aud.{aud}"))}</div>')
        blocks += f'{cap}<div class="seg-tree">{svg}</div>'
    if not blocks:
        return ""
    return (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:1rem 0 .35rem">'
        f'{_h.escape(t("seg.title"))}</h3>{blocks}'
    )


_CLOUD_COLS = (
    ("pains", "cloud.pains", "#ff8787"),   # Боли
    ("jobs", "cloud.jobs", "#74c0fc"),     # Цели
    ("gains", "cloud.gains", "#69db7c"),   # Выгоды
)


def _needs_cloud_html(clouds: dict | None) -> str:
    """Three-column need cloud (Боли · Цели · Выгоды), AI-grouped, most frequent first, each
    entity with a weight bar. Empty until the nightly aggregation has run for the branch."""
    if not clouds:
        return ""
    cols = ""
    for kind, title_key, color in _CLOUD_COLS:
        entries = clouds.get(kind) or []
        rows = ""
        for e in entries:
            pct = max(6, round(e.weight * 100))  # keep a sliver visible even for the rarest
            rows += (
                f'<div class="ncl-row" title="{_h.escape(e.label)} · {e.count}">'
                f'<div class="ncl-bar" style="width:{pct}%;background:{color}"></div>'
                f'<span class="ncl-lbl">{_h.escape(e.label)}</span>'
                f'<span class="ncl-n">{e.count}</span></div>'
            )
        if not rows:
            rows = f'<div class="ncl-empty">{_h.escape(t("cloud.empty"))}</div>'
        cols += (
            f'<div class="ncl-col">'
            f'<div class="ncl-hd" style="color:{color}">{_h.escape(t(title_key))}</div>'
            f'{rows}</div>'
        )
    return (
        f'<div class="ncl-wrap">'
        f'<h3 class="ncl-title">{_h.escape(t("cloud.title"))}</h3>'
        f'<div class="ncl-cols">{cols}</div></div>'
    )


def reports_panel_html(
    stage_counts: dict[str, int],
    hour_in: dict[int, int],
    hour_out: dict[int, int],
    ad_funnel: list | None = None,
    discovery: dict | None = None,
    fb_business_id: str = "",
    fb_account_id: str = "",
    date_from: str = "",
    date_to: str = "",
    active_range: str = "",
    ad_mappings: dict[str, str] | None = None,
    ad_suggestions: dict[str, str] | None = None,
    products: list[tuple[str, str]] | None = None,
    segments: list | None = None,
    segment_stages: dict | None = None,
    stage_flow: list | None = None,
    stage_reach: dict[str, int] | None = None,
    total_leads: int = 0,
    needs_cloud: dict | None = None,
) -> str:
    _pipeline = ("new", "nurturing", "qualifying", "presenting", "objection")
    _won = ("ready", "handed_off")
    total = sum(stage_counts.values())
    pipeline = sum(stage_counts.get(s, 0) for s in _pipeline)
    won = sum(stage_counts.get(s, 0) for s in _won)
    dormant = stage_counts.get("dormant", 0)
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
    if discovery is not None:
        kpis += (
            _kpi("rep.discovered", f"{discovery.get('pct', 0):g}%", "#4da6ff")
            + _kpi("rep.disc_len", f"{discovery.get('avg_msgs', 0):g}", "#4da6ff")
        )

    # message totals for the period (drives the "N messages" headline over the chart)
    total_in = sum(hour_in.values())
    total_out = sum(hour_out.values())
    kpis += _kpi(
        "rep.msgs_tile",
        f"{total_out}↑ / {total_in}↓",
        "#63c5ff",
    )

    # compact hourly-activity mini-chart placed high in the panel — grouped in/out bars per
    # hour-of-day, scaled to the busiest in/out count so the two directions compare directly;
    # the header carries the period totals + the peak hour so bar heights have a magnitude.
    max_val = max(max(hour_in.values(), default=0), max(hour_out.values(), default=0), 1)
    hour_totals = {h: hour_in.get(h, 0) + hour_out.get(h, 0) for h in range(24)}
    peak_h = max(hour_totals, key=lambda h: hour_totals[h])
    peak_val = hour_totals[peak_h]
    in_lbl = _h.escape(t("rep.msgs_in"))
    out_lbl = _h.escape(t("rep.msgs_out"))
    hour_bars = ""
    for h in range(24):
        n_in = hour_in.get(h, 0)
        n_out = hour_out.get(h, 0)
        h_in = round(n_in / max_val * 100)
        h_out = round(n_out / max_val * 100)
        hour_bars += (
            f'<div class="hbar" title="{h:02d}:00 · {in_lbl} {n_in} · {out_lbl} {n_out}">'
            f'<div class="hbar-g">'
            f'<div class="hbar-in" style="height:{h_in}%"></div>'
            f'<div class="hbar-out" style="height:{h_out}%"></div>'
            f'</div>'
            f'<div class="hbar-l">{f"{h:02d}" if h % 6 == 0 else ""}</div>'
            f'</div>'
        )
    peak_txt = _h.escape(t("rep.peak", n=peak_val, h=f"{peak_h:02d}"))
    mini_act = (
        f'<div class="mini-act">'
        f'<div class="mini-act-hd">'
        f'<span class="mini-act-t">{_h.escape(t("rep.activity"))} · '
        f'{_h.escape(t("rep.by_hour"))}</span>'
        f'<span class="mini-act-s"><b style="color:#4da6ff">{total_in}</b> {in_lbl} · '
        f'<b style="color:#51cf66">{total_out}</b> {out_lbl} · {peak_txt}</span></div>'
        f'<div class="hchart hchart-mini">{hour_bars}</div>'
        f'</div>'
    )

    title_lbl = _h.escape(t("rep.title"))
    return (
        f'<div class="ch"><span class="ch-n">{title_lbl}</span></div>'
        f'<div class="pnl-body">'
        f'{_date_range_form_html(date_from, date_to, active_range)}'
        f'<div class="kpi-row">{kpis}</div>'
        f'{_segment_tree_html(segments or [], segment_stages)}'
        f'<div class="rep-fc">'
        f'<div class="rep-fc-funnel">{_funnel_flow_html(stage_flow or [], stage_reach, total_leads) or _funnel_line_html(stage_counts)}</div>'  # noqa: E501
        f'{_needs_cloud_html(needs_cloud)}'
        f'</div>'
        f'{mini_act}'
        f'{_ad_funnel_html(ad_funnel or [], fb_business_id, fb_account_id, mappings=ad_mappings, suggestions=ad_suggestions, products=products)}'  # noqa: E501
        f'</div>'
    )


# ─── broker log page ──────────────────────────────────────────────────────────

_LOG_KIND = {
    "reply": {"ru": "ответ", "en": "reply"},
    "followup": {"ru": "follow-up", "en": "follow-up"},
    "translate": {"ru": "перевод", "en": "translate"},
    "alert": {"ru": "саммари алерта", "en": "alert summary"},
    "embed": {"ru": "эмбеддинг", "en": "embedding"},
    "embed:query": {"ru": "эмбед: поиск по базе (ответ боту)",
                    "en": "embed: KB search (per reply)"},
    "embed:index": {"ru": "эмбед: индексация базы", "en": "embed: KB reindex"},
    "coach": {"ru": "правка базы (Coach)", "en": "KB edit (Coach)"},
    "suggest": {"ru": "черновик менеджеру", "en": "draft for manager"},
    "chat": {"ru": "chat", "en": "chat"},
}


def _log_kind_label(kind: str | None) -> str:
    if not kind:
        return "—"
    row = _LOG_KIND.get(kind)
    return row.get(current_lang(), row.get("en", kind)) if row else kind


def _group_broker_rows(rows: list) -> list[list]:
    """Cluster consecutive broker calls of the SAME thread within a short window into one
    'turn' (one reply/followup = an embed + the chat + guard verify + any regens). Rows are
    newest-first; a same-thread gap over the window (or a rows-without-thread call) starts a
    new cluster. Threads interleave in time, so a cluster pulls its calls together visually."""
    clusters: list[list] = []
    seen: dict[int, tuple[int, object]] = {}  # thread_id -> (cluster index, its last dt)
    window = timedelta(seconds=300)
    for r in rows:
        tid, dt = r.thread_id, _as_dt(r.created_at)
        prev = seen.get(tid) if tid is not None else None
        if prev is not None and dt is not None and prev[1] is not None \
                and timedelta() <= (prev[1] - dt) <= window:
            clusters[prev[0]].append(r)
        else:
            clusters.append([r])
            prev = (len(clusters) - 1, dt)
        if tid is not None:
            seen[tid] = (prev[0], dt)
    return clusters


def _log_group_header(cluster: list, tz_by_branch: dict[int, int]) -> str:
    """Summary band above a multi-call turn: thread, call count, END-TO-END wall-clock across
    all the calls, total tokens/cost, and a fail count — the per-reply view the flat rows lack."""
    tid = cluster[0].thread_id
    dts = [d for d in (_as_dt(r.created_at) for r in cluster) if d is not None]
    ends = [d + timedelta(milliseconds=int(r.latency_ms or 0))
            for r, d in zip(cluster, dts, strict=False)]
    span = (max(ends) - min(dts)).total_seconds() if dts else 0.0
    tok = sum(int(r.tokens_in or 0) + int(r.tokens_out or 0) for r in cluster)
    cost = sum(float(r.cost_usd or 0) for r in cluster)
    fails = sum(1 for r in cluster if not r.ok)
    cost_s = "free" if not cost else f"${cost:.4f}"
    fail_s = (f' · <span class="st-pill s-fail">{fails} fail</span>') if fails else ""
    chat = (f'<a class="oq-chat" hx-get="/ui/chat/{tid}" hx-target="#main" hx-push-url="true"'
            f' href="/ui/inbox" onclick="setOpenThread({tid})">#{tid}</a>')
    return (
        f'<tr style="background:rgba(120,140,170,.10)">'
        f'<td colspan="9" style="font-size:.72rem;color:#6b7685;padding:.25rem .5rem">'
        f'🧵 {chat} · {len(cluster)} calls · end-to-end '
        f'<b style="color:#3a4657">{span:.1f}s</b> · {tok} tok · {_h.escape(cost_s)}{fail_s}'
        f'</td></tr>'
    )


def _log_row(r: object, tz_by_branch: dict[int, int], grouped: bool = False) -> str:
    req, tid, kind, cap = r.request_id, r.thread_id, r.kind, r.capability
    model, ti, to, cost = r.model, r.tokens_in, r.tokens_out, r.cost_usd
    lat, ok, err, created = r.latency_ms, r.ok, r.error, r.created_at
    dt = _as_dt(created)
    if dt is not None:
        dt += timedelta(hours=tz_by_branch.get(r.branch_id, 0))
    when = dt.strftime("%m-%d %H:%M:%S") if dt else "—"  # MM-DD HH:MM:SS, branch-local
    rid = f'#{_h.escape(str(req))}' if req else "—"
    chat = (f'<a class="oq-chat" hx-get="/ui/chat/{tid}" hx-target="#main"'
            f' hx-push-url="true" href="/ui/inbox" onclick="setOpenThread({tid})">#{tid}</a>'
            if tid else '<span style="color:#4a5568">—</span>')
    tok = int(ti or 0) + int(to or 0)
    cost_s = "free" if not cost else f"${float(cost):.4f}"
    lat_s = f"{int(lat) / 1000:.1f}s" if lat else "—"
    model_s = _h.escape((model or "—").split("/")[-1])
    fail = "" if ok else ' <span class="st-pill s-fail">fail</span>'
    title = f' title="{_h.escape(str(err)[:300])}"' if err else ""
    styles = "" if ok else "opacity:.6;"
    # a member of a multi-call turn gets a left accent so the group reads as one block
    if grouped:
        styles += "border-left:3px solid rgba(120,140,170,.5);"
    dim = f' style="{styles}"' if styles else ""
    return (
        f'<tr{dim}{title}>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.68rem">{rid}</td>'
        f'<td style="color:#6b7685;font-size:.7rem;white-space:nowrap">{_h.escape(when)}</td>'
        f'<td style="font-size:.74rem">{_h.escape(_log_kind_label(kind))}{fail}</td>'
        f'<td style="font-size:.74rem">{chat}</td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.68rem;color:#8899aa">'
        f'{_h.escape(cap or "—")}</td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.68rem">{model_s}</td>'
        f'<td style="text-align:right;font-size:.7rem;color:#8899aa">{tok}</td>'
        f'<td style="text-align:right;font-size:.7rem">{_h.escape(cost_s)}</td>'
        f'<td style="text-align:right;font-size:.7rem;color:#6b7685">{_h.escape(lat_s)}</td>'
        f'</tr>'
    )


def _log_pager(page: int, size: int, total: int) -> str:
    pages = max(1, (total + size - 1) // size)
    cur = page + 1
    prev = (f'<button class="btn-sm" hx-get="/ui/settings/log?page={page - 1}"'
            f' hx-target="#main">← {_h.escape(t("log.prev"))}</button>' if page > 0
            else f'<span class="btn-sm" style="opacity:.35">← {_h.escape(t("log.prev"))}</span>')
    nxt = (f'<button class="btn-sm" hx-get="/ui/settings/log?page={page + 1}"'
           f' hx-target="#main">{_h.escape(t("log.next"))} →</button>' if cur < pages
           else f'<span class="btn-sm" style="opacity:.35">{_h.escape(t("log.next"))} →</span>')
    return (
        f'<div style="display:flex;gap:.6rem;align-items:center;margin:.6rem 0">'
        f'{prev}<span style="color:#6b7685;font-size:.72rem">{_h.escape(t("log.page"))} '
        f'{cur} / {pages} · {total} {_h.escape(t("log.total"))}</span>{nxt}</div>'
    )


def _log_histogram_html(
    buckets: list[float], turns: int, window: str, windows: list[str],
) -> str:
    """Micro period-buttons (1h/4h/12h/24h/7d) + a mini bar histogram of total end-to-end
    seconds per time bucket over the chosen window — a load/slowness sparkline for the log."""
    btns = "".join(
        (f'<span class="btn-sm" style="background:#3a4657;color:#fff;cursor:default">{w}</span>'
         if w == window else
         f'<button class="btn-sm" hx-get="/ui/settings/log?page=0&window={w}"'
         f' hx-target="#main">{w}</button>')
        for w in windows
    )
    peak = max(buckets) if buckets else 0.0
    total = sum(buckets)
    if peak <= 0:
        bars = '<span style="color:#6b7685;font-size:.72rem">нет данных за период</span>'
    else:
        def _bar(v: float) -> str:
            h = max(2, v / peak * 34)
            color = "#c0563a" if v >= peak * 0.66 else "#5b7fa6" if v >= peak * 0.33 else "#8aa0b8"
            return (f'<div title="{v:.0f}s" style="flex:1;min-width:2px;height:{h:.0f}px'
                    f';background:{color};border-radius:1px 1px 0 0"></div>')
        bars = (f'<div style="display:flex;align-items:flex-end;gap:1px;height:38px;flex:1">'
                f'{"".join(_bar(v) for v in buckets)}</div>')
    summary = (f'<span style="color:#6b7685;font-size:.72rem;white-space:nowrap">'
               f'Σ end-to-end <b style="color:#3a4657">{total:.0f}s</b> · {turns} ходов · '
               f'пик {peak:.0f}s</span>')
    return (
        f'<div style="display:flex;align-items:center;gap:.5rem;margin:.4rem 0 .7rem">'
        f'<div style="display:flex;gap:.25rem">{btns}</div>'
        f'{bars}{summary}</div>'
    )


def broker_log_panel_html(
    rows: list, page: int, size: int, total: int, tz_by_branch: dict[int, int] | None = None,
    hist: tuple[list[float], int, str, list[str]] | None = None,
) -> str:
    title = _h.escape(t("log.title"))
    intro = _h.escape(t("log.intro"))
    tz = tz_by_branch or {}
    parts: list[str] = []
    for cluster in _group_broker_rows(rows):
        if len(cluster) > 1:
            parts.append(_log_group_header(cluster, tz))
            parts.extend(_log_row(r, tz, grouped=True) for r in cluster)
        else:
            parts.append(_log_row(cluster[0], tz))
    body = "".join(parts) or (
        f'<tr><td colspan="9" style="color:#4a5568">{_h.escape(t("log.empty"))}</td></tr>')
    head = (
        f'<tr><th>ID</th><th>{_h.escape(t("log.when"))}</th>'
        f'<th>{_h.escape(t("log.kind"))}</th><th>{_h.escape(t("log.chat"))}</th>'
        f'<th>cap</th><th>{_h.escape(t("log.model"))}</th>'
        f'<th style="text-align:right">tok</th>'
        f'<th style="text-align:right">{_h.escape(t("log.cost"))}</th>'
        f'<th style="text-align:right">{_h.escape(t("log.dur"))}</th></tr>'
    )
    hist_html = _log_histogram_html(*hist) if hist is not None else ""
    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{intro}</div>'
        f'{hist_html}'
        f'{_log_pager(page, size, total)}'
        f'<table class="tbl"><thead>{head}</thead><tbody>{body}</tbody></table>'
        f'{_log_pager(page, size, total)}'
        f'</div>'
    )
