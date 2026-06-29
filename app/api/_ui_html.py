"""HTML page builders for the /ui/ manager interface (inline CSS, no template files)."""
from __future__ import annotations

import html as h
from datetime import UTC, datetime, timedelta

_CSS = """
:root{--bg:#1a1f2e;--bg2:#232a3b;--bg3:#141925;--brd:#2d3748;--tx:#d0d7de;--tx2:#8899aa;
  --acc:#206bc4;--acc2:#4da6ff;--red:#f03e3e;--green:#51cf66;--yel:#ffa94d}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);
  color:var(--tx);font-size:14px;line-height:1.5}
a{color:var(--acc2);text-decoration:none} a:hover{text-decoration:underline}
nav{background:var(--bg3);padding:.55rem 1.2rem;display:flex;align-items:center;
  gap:1.2rem;border-bottom:1px solid var(--brd)}
.brand{font-weight:700;color:#fff;font-size:.95rem;margin-right:.3rem}
nav a{color:var(--tx2);font-size:.82rem} nav a.on,nav a:hover{color:#fff}
.wrap{max-width:1000px;margin:0 auto;padding:1.2rem 1rem}
h2{font-size:1.05rem;font-weight:600;color:#fff;margin-bottom:.9rem}
.card{background:var(--bg2);border:1px solid var(--brd);border-radius:8px;padding:.8rem 1rem}
.badge{display:inline-block;padding:.15rem .45rem;border-radius:10px;
  font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.s-new{background:#1e3a5f;color:#4da6ff}.s-qualifying{background:#2a1f5f;color:#9b7aff}
.s-presenting{background:#1f3a2a;color:#4adb7a}.s-objection{background:#3a2a1f;color:#ffa94d}
.s-ready{background:#1f3a2a;color:#51cf66}.s-handed_off{background:#1f3a2a;color:#22b8cf}
.s-dormant{background:#2a2a2a;color:#868e96}.s-manager{background:#3a1f1f;color:#ff6b6b}
.btn{display:inline-block;padding:.32rem .75rem;border-radius:5px;font-size:.8rem;
  font-weight:600;cursor:pointer;border:none;line-height:1.4}
.btn-p{background:var(--acc);color:#fff}.btn-p:hover{background:#1a5aaa}
.btn-d{background:#862e2e;color:#fff}.btn-d:hover{background:#c92a2a}
.btn-s{padding:.18rem .5rem;font-size:.75rem}
.tc{display:flex;align-items:baseline;gap:.5rem;margin-bottom:.2rem}
.lname{font-weight:600;color:#e8eef4;font-size:.9rem}
.lmsg{color:var(--tx2);font-size:.8rem;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:620px}
.ttime{color:var(--tx2);font-size:.73rem;margin-left:auto;flex-shrink:0}
.msgs{display:flex;flex-direction:column;gap:.4rem;margin-bottom:1rem}
.bbl{display:flex;flex-direction:column;max-width:78%}
.bbl-in{align-self:flex-start}.bbl-out{align-self:flex-end}
.btext{padding:.42rem .62rem;border-radius:10px;font-size:.84rem;
  white-space:pre-wrap;word-break:break-word}
.bbl-in .btext{background:#232a3b;border:1px solid #2d3748}
.bbl-out .btext{background:#1e3a5f}.bbl-out.mgr .btext{background:#2a1f3a}
.bbl-pend .btext{background:#1f3a1f;opacity:.65}
.bmeta{font-size:.68rem;color:var(--tx2);margin-top:.12rem}
.bbl-out .bmeta{text-align:right}
.send-form{display:flex;gap:.5rem;margin-top:.5rem}
.send-form textarea{flex:1;background:#232a3b;border:1px solid #2d3748;border-radius:6px;
  color:var(--tx);padding:.45rem .55rem;font-size:.84rem;resize:vertical;min-height:2.8rem}
.send-form textarea:focus{outline:none;border-color:var(--acc)}
.req-form{display:flex;flex-direction:column;gap:.5rem}
.req-form textarea{width:100%;background:#232a3b;border:1px solid #2d3748;border-radius:6px;
  color:var(--tx);padding:.55rem;font-size:.84rem;resize:vertical;min-height:3.5rem}
.req-form textarea:focus{outline:none;border-color:var(--acc)}
.diff-old{background:#3a1f1f;border-left:3px solid var(--red);padding:.35rem .55rem;
  font-size:.78rem;border-radius:3px;font-family:monospace;
  white-space:pre-wrap;word-break:break-all;margin-top:.35rem}
.diff-new{background:#1f3a1f;border-left:3px solid var(--green);padding:.35rem .55rem;
  font-size:.78rem;border-radius:3px;font-family:monospace;
  white-space:pre-wrap;word-break:break-all;margin-top:.2rem}
.edit-row{display:flex;gap:.4rem;margin-top:.4rem;align-items:center}
.st-applied{color:var(--green)}.st-cancelled,.st-failed{color:var(--tx2)}
.st-proposed{color:var(--yel)}.st-clarify{color:var(--acc2)}
#coach-result{margin-top:.8rem}
"""

_HTMX = "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"

_STAGE_CSS = {
    "new": "s-new", "qualifying": "s-qualifying", "presenting": "s-presenting",
    "objection": "s-objection", "ready": "s-ready", "handed_off": "s-handed_off",
    "dormant": "s-dormant", "manager": "s-manager",
}

_NAV_LINKS = [
    ("inbox", "/ui/inbox", "Inbox"),
    ("coach", "/ui/coach", "Coach"),
    ("admin", "/admin/", "Admin"),
]


def _nav(active: str) -> str:
    parts = ['<span class="brand">Stepan2</span>']
    for key, href, label in _NAV_LINKS:
        cls = ' class="on"' if key == active else ""
        parts.append(f'<a href="{href}"{cls}>{label}</a>')
    return f'<nav>{"".join(parts)}</nav>'


def _page(title: str, active: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="ru"><head>'
        f'<meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{h.escape(title)} — Stepan2</title>'
        f'<script src="{_HTMX}"></script>'
        f'<style>{_CSS}</style></head><body>'
        f'{_nav(active)}<div class="wrap">{body}</div></body></html>'
    )


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    delta = datetime.now(UTC).replace(tzinfo=None) - dt
    if delta.total_seconds() < 60:
        return "сейчас"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}м"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}ч"
    return f"{delta.days}д"


def _badge(stage: str) -> str:
    cls = _STAGE_CSS.get(stage, "s-dormant")
    return f'<span class="badge {cls}">{h.escape(stage)}</span>'


# ── pages ─────────────────────────────────────────────────────────────────────

def inbox_html(threads: list) -> str:
    if not threads:
        items = '<p style="color:var(--tx2);padding:1.5rem 0">Нет чатов</p>'
    else:
        cards = []
        for tid, name, stage, last_in, last_msg, last_dir in threads:
            arrow = "←" if last_dir == "in" else "→"
            preview = f"{arrow} {h.escape(str(last_msg)[:90])}" if last_msg else ""
            name_s = h.escape(str(name or f"Lead #{tid}"))
            badge = _badge(str(stage or "new"))
            ago = _ago(last_in)
            cards.append(
                f'<a href="/ui/chat/{tid}" class="card"'
                f' style="display:block;margin-bottom:.4rem;color:inherit">'
                f'<div class="tc"><span class="lname">{name_s}</span>'
                f'{badge}<span class="ttime">{ago}</span></div>'
                f'<div class="lmsg">{preview}</div></a>'
            )
        items = "".join(cards)
    return _page("Inbox", "inbox", f'<h2>Inbox ({len(threads)})</h2>{items}')


def chat_html(
    thread_id: int, lead_name: str, stage: str, messages: list, pending: list
) -> str:
    bubbles = []
    for _mid, direction, sent_by, text, occurred_at in messages:
        if direction == "out":
            mgr = " mgr" if sent_by == "manager" else ""
            cls = f"bbl-out{mgr}"
        else:
            cls = "bbl-in"
        label = {"lead": "лид", "agent": "Степан", "manager": "менеджер"}.get(
            sent_by, sent_by
        )
        bubbles.append(
            f'<div class="bbl {cls}">'
            f'<div class="btext">{h.escape(str(text or ""))}</div>'
            f'<div class="bmeta">{h.escape(label)} · {_ago(occurred_at)}</div></div>'
        )
    for _rid, text, sched in pending:
        bubbles.append(
            f'<div class="bbl bbl-out bbl-pend">'
            f'<div class="btext">{h.escape(str(text or ""))}</div>'
            f'<div class="bmeta">ожидает отправки · {_ago(sched)}</div></div>'
        )
    no_msg = "<p style=color:var(--tx2)>Нет сообщений</p>"
    content = "".join(bubbles) or no_msg
    header = (
        f'<div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.8rem">'
        f'<a href="/ui/inbox">← Inbox</a>'
        f'<strong style="color:#fff">{h.escape(lead_name)}</strong>'
        f'{_badge(stage)}</div>'
    )
    msgs = f'<div class="msgs">{content}</div>'
    form = (
        f'<form method="post" action="/ui/chat/{thread_id}/send" class="send-form">'
        f'<textarea name="text" placeholder="Ваше сообщение…" rows="2"></textarea>'
        f'<button class="btn btn-p" type="submit">Отправить</button></form>'
    )
    return _page(lead_name, "inbox", header + msgs + form)


def coach_html(branch_id: int, edits: list) -> str:
    ph = "Что изменить в базе знаний?"
    form = (
        f'<div class="card req-form" style="margin-bottom:.8rem">'
        f'<form hx-post="/ui/coach/say" hx-target="#coach-result" hx-swap="afterbegin">'
        f'<input type="hidden" name="branch_id" value="{branch_id}">'
        f'<textarea name="request" placeholder="{ph}" required rows="3"></textarea>'
        f'<div style="margin-top:.5rem">'
        f'<button class="btn btn-p" type="submit">Предложить правку</button></div>'
        f'</form></div>'
        f'<div id="coach-result"></div>'
    )
    header = '<h2 style="margin-top:.8rem">История</h2>' if edits else ""
    cards = [
        _edit_card(eid, req, status, slug, old_t, new_t, summary, created_at)
        for eid, req, status, slug, old_t, new_t, summary, created_at in edits
    ]
    return _page("Coach", "coach", form + header + "".join(cards))


def coach_partial_html(
    eid: int,
    req: str,
    status: str,
    slug: str | None,
    old_t: str | None,
    new_t: str | None,
    summary: str | None,
) -> str:
    return _edit_card(eid, req, status, slug, old_t, new_t, summary, None)


def _edit_card(
    eid: int,
    req: str,
    status: str,
    slug: str | None,
    old_t: str | None,
    new_t: str | None,
    summary: str | None,
    created_at: datetime | None,
) -> str:
    stat_cls = f"st-{status}"
    if created_at:
        time_str = f'<span class="ttime" style="margin-left:auto">{_ago(created_at)}</span>'
    else:
        time_str = ""
    doc_str = f' · <code style="font-size:.75rem">{h.escape(str(slug))}</code>' if slug else ""
    actions = ""
    if status == "proposed":
        apply_f = (
            f'<form method="post" action="/ui/coach/apply/{eid}" style="display:inline">'
            f'<button class="btn btn-p btn-s">✓ Применить</button></form>'
        )
        cancel_f = (
            f'<form method="post" action="/ui/coach/cancel/{eid}"'
            f' style="display:inline;margin-left:.4rem">'
            f'<button class="btn btn-d btn-s">✗ Отклонить</button></form>'
        )
        actions = f'<div class="edit-row">{apply_f}{cancel_f}</div>'
    diff = ""
    if old_t and new_t:
        diff = (
            f'<div class="diff-old">− {h.escape(str(old_t)[:400])}</div>'
            f'<div class="diff-new">+ {h.escape(str(new_t)[:400])}</div>'
        )
    summ = ""
    if summary:
        summ = (
            f'<div style="color:var(--tx2);font-size:.82rem;margin-top:.25rem">'
            f'{h.escape(str(summary)[:300])}</div>'
        )
    stat_style = 'font-size:.72rem;font-weight:700;text-transform:uppercase'
    req_style = 'color:#fff;font-size:.85rem;margin-left:.2rem'
    return (
        f'<div class="card" style="margin-bottom:.45rem">'
        f'<div style="display:flex;align-items:baseline;gap:.4rem">'
        f'<span class="{stat_cls}" style="{stat_style}">{status}</span>'
        f'{doc_str}<span style="{req_style}">{h.escape(str(req)[:180])}</span>'
        f'{time_str}</div>'
        f'{summ}{diff}{actions}</div>'
    )
