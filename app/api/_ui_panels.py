"""HTML generators for data panels: coach chat, knowledge, products, members, settings."""
from __future__ import annotations

import html as _h
import json as _json
from datetime import UTC, datetime, timedelta

from app.connectors.registry import all_specs, spec_for

from ._i18n import current_lang, t
from ._ui_html import (
    _ago,
    _as_dt,
    fmt_dt,
)

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
def _sbadge(stage: str) -> str:
    return (
        f'<span class="bg {_STC.get(stage, "sd")}">'
        f'{_h.escape(t(f"stage.{stage}"))}</span>'
    )


# ─── leads panel ──────────────────────────────────────────────────────────────

def leads_panel_html(rows: list, tz_by_branch: dict[int, int] | None = None) -> str:  # noqa: ARG001
    """List of leads with stage badge, phone, and creation date (viewer-local)."""
    title = _h.escape(t("nav.leads"))
    name_h = _h.escape(t("lead.name"))
    phone_h = _h.escape(t("lead.phone"))
    stage_h = _h.escape(t("lead.stage"))
    created_h = _h.escape(t("lead.created"))
    hint = _h.escape(t("help.leads"))

    def _created(v: object, branch_id: object) -> str:  # noqa: ARG001 (viewer tz, not branch)
        return fmt_dt(v, "%Y-%m-%d", empty="—")

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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.leads"))}">'
        f'{title}</span></div>'
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


def inbox_awaiting_badge_html(in_queue: int) -> str:
    """One number: the chats Stepan is supposed to answer and hasn't. Nothing else.

    It used to show every unanswered chat split into buckets, and the big grey number was
    noise: dormant, ready, bot-off and handed-off leads are all "unanswered" and none of them
    is a problem. A badge that is never zero is a badge nobody reads — so the counts that need
    no action are gone, and this one hides itself at zero. Empty here means nothing is waiting
    on us, which is the only state worth reporting at a glance.

    The other buckets stay reachable by URL (?awaiting=off / settled) for when someone is
    looking for them on purpose; they just no longer ask to be looked at.

    Clicking swaps the thread list in place (htmx → #tl) instead of navigating: the badge polls
    every 15s, and a full page load would throw away the open chat next to it."""
    if in_queue <= 0:
        return ""
    return (
        f'<span class="iaw iaw-q" title="{_h.escape(t("inbox.await_queue"))}"'
        ' hx-get="/ui/threads?awaiting=queue" hx-target="#tl" hx-swap="innerHTML"'
        ' hx-push-url="/ui/inbox?awaiting=queue"'
        ' onclick="event.stopPropagation()">'
        f"{in_queue}</span>"
    )


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

    def _ts(v: object, branch_id: object) -> str:  # noqa: ARG001 (branch_id unused: viewer tz)
        return fmt_dt(v, "%H:%M:%S", empty="—")

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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.outbox"))}">'
        f'{title}</span>'
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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.coach"))}">'
        f'Coach KB</span></div>'
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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.products"))}">'
        f'{title}</span>'
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
        # A <form method=post> here was NESTED inside the outer edit form (invalid HTML — the
        # browser drops the inner form), so the button did nothing. Use an hx-post button with
        # hx-confirm (the warning) instead; the delete route 303s back to the product list,
        # which htmx swaps into #main. type=button so it never submits the outer edit form.
        delete_btn = (
            f'<button type="button" class="btn-sm" style="background:#862e2e;color:#fff"'
            f' hx-post="/ui/products/{prod_id}/delete" hx-target="#main" hx-swap="innerHTML"'
            f' hx-confirm="{del_lbl}?">{del_lbl}</button>'
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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.branches"))}">'
        f'{title}</span>'
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
        f'<div class="frm-grp" data-help="{_h.escape(t("br.name_h"))}">'
        f'<label class="frm-lbl">{name_lbl}</label>'
        f'<input class="frm-inp" name="name" value="{_h.escape(name)}" required></div>'
        f'<div class="frm-grp" data-help="{_h.escape(t("br.lang_h"))}">'
        f'<label class="frm-lbl">{lang_lbl}</label>'
        f'<select class="act-sel" name="lang"'
        f' style="width:100%;padding:.32rem .35rem">{lang_opts}</select></div>'
        f'<div class="frm-grp" data-help="{_h.escape(t("br.tz_h"))}">'
        f'<label class="frm-lbl">{tz_lbl}</label>'
        f'<select class="act-sel" name="tz_offset_h"'
        f' style="width:100%;padding:.32rem .35rem">{_tz_opts(tz)}</select></div>'
        f'<div class="frm-grp" data-help="{_h.escape(t("br.active_h"))}"'
        f' style="display:flex;align-items:center;gap:.5rem">'
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
        f'<div class="frm-grp" data-help="{_h.escape(t("br.kb_source_h"))}">'
        '<label class="frm-lbl">База знаний из филиала</label>'
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


def _kind_label(kind: str) -> str:
    """Display name of a connector — from its spec, so one connector has one name."""
    spec = spec_for(kind)
    return spec.label if spec is not None else kind


def channel_list_partial_html(channels: list, sessions: list, branch_id: int) -> str:
    """HTMX-loaded channel table for #ch-list inside branch edit."""
    session_map = {r[0]: r[1] for r in sessions}
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
            f'{_kind_label(kind)}</td>'
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
        f'<option value="{s.kind.value}">{_h.escape(t(s.label_key))}</option>'
        for s in all_specs()
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
    checked = "checked" if is_active else ""
    save_lbl = _h.escape(t("ch.save"))
    return (
        f'<div style="font-weight:600;color:#4da6ff;font-size:.8rem;margin-bottom:.55rem">'
        f'{_kind_label(kind)} #{ch_id}</div>'
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
    """The connector's own credential panel — looked up, not branched on.

    The panel lives with its connector (app/connectors/<kind>_ui.py) and is reached through
    ConnectorSpec.credential_panel, so adding one does not touch this file."""
    spec = spec_for(kind)
    if spec is None:
        return '<div class="emp">Unknown channel kind</div>'
    return spec.credential_panel(ch_id)


def _ch_connected(ch_id: int) -> str:
    """Post-login state: session is active — confirm it and offer a reconnect."""
    return (
        f'<div style="color:#51cf66;font-size:.8rem;margin-bottom:.55rem">'
        f'{_h.escape(t("ch.session_ok"))}</div>'
        f'<button class="btn-sm" hx-get="/ui/channels/{ch_id}/form"'
        f' hx-target="#ch-form" hx-swap="innerHTML">'
        f'{_h.escape(t("ch.reconnect"))}</button>'
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
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.settings"))}">'
        f'{title}</span></div>'
        f'<div class="pnl-body">'
        f'<table class="tbl">'
        f'<thead><tr><th>Key</th><th>Value</th></tr></thead>'
        f'<tbody>{rows or "<tr><td colspan=2 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table></div>'
    )


# Two relocations bridged from here, for the same reason both times: the move changed no call
# site and no test import in the commit that performed it, which is what keeps a bisect
# readable if something surfaces later. The noqa is load-bearing — without it the linter reads
# these as unused imports and deletes the bridge.
#   2026-08-04 — the per-kind credential panels went to app/connectors/, one per connector.
#   2026-07-28 — the reports page went to _ui_reports (1295 lines).
from app.connectors.instagram_ui import (  # noqa: F401,E402 — re-export, see above
    _IG_POLL_DELAYS,
    _ch_ig_form,
)
from app.connectors.meta_business_ui import _ch_meta_form  # noqa: F401,E402
from app.connectors.ui_bits import _ch_err  # noqa: F401,E402
from app.connectors.whatsapp_ui import _ch_wa_form  # noqa: F401,E402

from ._ui_reports import (  # noqa: F401,E402 — re-export, see above
    _FLOW_SPINE,
    _FUNNEL_PIPELINE,
    _FUNNEL_SIDE,
    _ad_tree_html,
    _ads_manager_url,
    _col_hint,
    _date_range_form_html,
    _funnel_flow_html,
    _sparkline,
    admap_cell_inner,
    broker_log_panel_html,
    reports_panel_html,
)
