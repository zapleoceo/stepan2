"""HTML generators for the 3-column manager UI (sidebar + thread list + panel)."""
from __future__ import annotations

import html as _h
from datetime import UTC, datetime

from ._i18n import t

_HTMX = "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"
_FA = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"

_CSS = (
    "*{box-sizing:border-box;margin:0;padding:0}"
    "html,body{height:100%;overflow:hidden}"
    "body{display:flex;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "background:#0f1117;color:#d0d7de;font-size:14px}"
    ".sid{width:210px;flex-shrink:0;background:#141925;border-right:1px solid #2d3748;"
    "display:flex;flex-direction:column}"
    ".sid-top{padding:.7rem .9rem .45rem;border-bottom:1px solid #2d3748}"
    ".logo{font-size:1.05rem;font-weight:800;color:#fff}"
    ".sid-nav{flex:1;padding:.4rem 0;overflow-y:auto}"
    ".na{display:flex;align-items:center;gap:.5rem;padding:.4rem .75rem;color:#8899aa;"
    "font-size:.81rem;text-decoration:none;border-radius:6px;margin:.05rem .45rem;"
    "transition:background .12s,color .12s;cursor:pointer}"
    ".na:hover{background:rgba(255,255,255,.08);color:#d0d7de}"
    ".na.on{background:rgba(32,107,196,.22);color:#4da6ff}"
    ".na i{width:14px;text-align:center}"
    ".nav-sep{height:1px;background:#2d3748;margin:.4rem .9rem}"
    ".sid-ft{padding:.55rem .7rem .7rem;border-top:1px solid #2d3748}"
    ".lrow{display:flex;gap:.22rem;margin-top:.3rem}"
    ".lb{flex:1;padding:.22rem .1rem;background:rgba(255,255,255,.07);"
    "border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#8899aa;"
    "font-size:.68rem;font-weight:600;text-align:center;text-decoration:none}"
    ".lb.on,.lb:hover{background:rgba(32,107,196,.28);color:#4da6ff;border-color:#206bc4}"
    ".thr{width:305px;flex-shrink:0;background:#0f1117;border-right:1px solid #2d3748;"
    "display:flex;flex-direction:column;overflow:hidden}"
    ".thr-h{padding:.56rem .8rem;border-bottom:1px solid #2d3748;font-size:.82rem;"
    "font-weight:600;color:#e8eef4;flex-shrink:0}"
    "#tl{flex:1;overflow-y:auto}"
    "#tl::-webkit-scrollbar{width:4px}"
    "#tl::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}"
    ".ti{display:block;padding:.56rem .8rem;border-bottom:1px solid rgba(255,255,255,.04);"
    "text-decoration:none;color:inherit;cursor:pointer;transition:background .1s;"
    "border-left:2px solid transparent}"
    ".ti:hover{background:rgba(255,255,255,.05)}"
    ".ti.on{background:rgba(32,107,196,.14);border-left-color:#206bc4}"
    ".ti-t{display:flex;align-items:baseline;gap:.35rem;margin-bottom:.1rem}"
    ".ti-n{font-weight:600;color:#e8eef4;font-size:.84rem;flex:1;overflow:hidden;"
    "text-overflow:ellipsis;white-space:nowrap}"
    ".ti-ts{font-size:.68rem;color:#4a5568;flex-shrink:0}"
    ".ti-p{font-size:.74rem;color:#6b7685;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap;margin-top:.05rem}"
    ".bg{display:inline-block;padding:.07rem .3rem;border-radius:5px;font-size:.6rem;"
    "font-weight:700;text-transform:uppercase;margin-right:.18rem}"
    ".sn{background:#1e3a5f;color:#4da6ff}.sq{background:#2a1f5f;color:#9b7aff}"
    ".sp{background:#1f3a2a;color:#4adb7a}.so{background:#3a2a1f;color:#ffa94d}"
    ".sr{background:#1f3a2a;color:#51cf66}.sh{background:#163030;color:#22b8cf}"
    ".sd{background:#2a2a2a;color:#868e96}.sm{background:#3a1f1f;color:#ff6b6b}"
    "#main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}"
    ".ch{padding:.56rem .9rem;border-bottom:1px solid #2d3748;background:#141925;"
    "display:flex;align-items:center;gap:.55rem;flex-shrink:0}"
    ".ch-n{font-weight:600;color:#e8eef4;font-size:.9rem}"
    ".msgs{flex:1;overflow-y:auto;padding:.72rem .95rem;display:flex;"
    "flex-direction:column;gap:.3rem}"
    ".msgs::-webkit-scrollbar{width:4px}"
    ".msgs::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}"
    ".bb{display:flex;flex-direction:column;max-width:72%}"
    ".bb-i{align-self:flex-start}.bb-o{align-self:flex-end}.bb-p{opacity:.6;align-self:flex-end}"
    ".bt{padding:.4rem .56rem;border-radius:9px;font-size:.8rem;"
    "white-space:pre-wrap;word-break:break-word}"
    ".bb-i .bt{background:#232a3b;border:1px solid #2d3748}"
    ".bb-o .bt{background:#1e3a5f}.bb-o.mgr .bt{background:#2a1f3a}"
    ".bm{font-size:.63rem;color:#4a5568;margin-top:.08rem}"
    ".bb-o .bm{text-align:right}"
    ".fin{padding:.52rem .8rem;border-top:1px solid #2d3748;display:flex;gap:.4rem;"
    "background:#141925;flex-shrink:0}"
    ".fin textarea{flex:1;background:#1a1f2e;border:1px solid #2d3748;border-radius:6px;"
    "color:#d0d7de;padding:.36rem .52rem;font-size:.8rem;resize:none;"
    "font-family:inherit;line-height:1.4}"
    ".fin textarea:focus{outline:none;border-color:#206bc4}"
    ".bsn{background:#206bc4;color:#fff;border:none;border-radius:6px;"
    "padding:0 .88rem;font-size:.78rem;font-weight:600;cursor:pointer}"
    ".bsn:hover{background:#1a5aaa}"
    ".emp{display:flex;align-items:center;justify-content:center;height:100%;"
    "color:#4a5568;font-size:.86rem}"
    ".df{background:#3a1f1f;border-left:2px solid #f03e3e;padding:.25rem .4rem;"
    "font-family:monospace;font-size:.73rem;white-space:pre-wrap;word-break:break-all;"
    "border-radius:3px;margin-top:.25rem}"
    ".dn{background:#1f3a1f;border-left:2px solid #51cf66;padding:.25rem .4rem;"
    "font-family:monospace;font-size:.73rem;white-space:pre-wrap;word-break:break-all;"
    "border-radius:3px;margin-top:.18rem}"
    ".bx{padding:.17rem .4rem;font-size:.7rem;border:none;border-radius:4px;"
    "cursor:pointer;font-weight:600;margin-right:.2rem;margin-top:.28rem}"
    ".bx-a{background:#206bc4;color:#fff}.bx-c{background:#862e2e;color:#fff}"
    ".pnl-body{flex:1;overflow-y:auto;padding:.55rem .8rem}"
    ".pnl-body::-webkit-scrollbar{width:4px}"
    ".pnl-body::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}"
    ".tbl{width:100%;border-collapse:collapse;font-size:.81rem}"
    ".tbl th{text-align:left;padding:.3rem .52rem;color:#6b7685;font-weight:600;"
    "font-size:.7rem;text-transform:uppercase;border-bottom:1px solid #2d3748}"
    ".tbl td{padding:.34rem .52rem;border-bottom:1px solid rgba(255,255,255,.04);"
    "color:#d0d7de;vertical-align:middle}"
    ".tbl tr:hover td{background:rgba(255,255,255,.04)}"
    ".pill{display:inline-block;padding:.08rem .36rem;border-radius:8px;"
    "font-size:.64rem;font-weight:700;text-transform:uppercase}"
    ".p-ok{background:#1f3a1f;color:#51cf66}.p-off{background:#2a2a2a;color:#868e96}"
    ".p-mgr{background:#1e3a5f;color:#4da6ff}.p-adm{background:#2a1f5f;color:#9b7aff}"
    ".set-key{font-family:ui-monospace,monospace;font-size:.79rem;color:#4da6ff}"
    ".set-desc{font-size:.71rem;color:#6b7685;margin-top:.1rem}"
    ".set-val{background:#0f1117;border:1px solid #2d3748;border-radius:4px;"
    "color:#d0d7de;padding:.26rem .42rem;font-size:.79rem;font-family:inherit;width:100%}"
    ".set-val:focus{outline:none;border-color:#206bc4}"
    ".frm-ta{width:100%;background:#1a1f2e;border:1px solid #2d3748;border-radius:6px;"
    "color:#d0d7de;padding:.36rem .52rem;font-size:.8rem;resize:vertical;"
    "min-height:7rem;font-family:inherit}"
    ".frm-ta:focus{outline:none;border-color:#206bc4}"
    ".frm-inp{width:100%;background:#1a1f2e;border:1px solid #2d3748;border-radius:6px;"
    "color:#d0d7de;padding:.36rem .52rem;font-size:.8rem;font-family:inherit}"
    ".frm-inp:focus{outline:none;border-color:#206bc4}"
    ".frm-lbl{font-size:.72rem;color:#6b7685;margin-bottom:.15rem;display:block}"
    ".frm-grp{margin-bottom:.45rem}"
    ".btn-sm{padding:.22rem .55rem;font-size:.75rem;border:none;border-radius:4px;"
    "cursor:pointer;font-weight:600}"
    ".btn-p{background:#206bc4;color:#fff}.btn-p:hover{background:#1a5aaa}"
    ".btn-g{background:rgba(255,255,255,.08);color:#d0d7de;border:none;"
    "border-radius:4px;text-decoration:none;font-size:.75rem;padding:.22rem .55rem;"
    "font-weight:600;cursor:pointer}"
    ".btn-g:hover{background:rgba(255,255,255,.14)}"
    ".kdoc{background:#1a1f2e;border:1px solid #2d3748;border-radius:7px;"
    "padding:.5rem .7rem;margin-bottom:.3rem;cursor:pointer}"
    ".kdoc:hover{border-color:#4a5568}"
    ".kdoc-slug{font-family:ui-monospace,monospace;font-size:.7rem;color:#4da6ff;"
    "margin-bottom:.1rem}"
    ".kdoc-title{font-weight:600;color:#e8eef4;font-size:.82rem;margin-bottom:.1rem}"
    ".kdoc-preview{font-size:.74rem;color:#6b7685;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap}"
    ".hint{font-size:.74rem;color:#4a5568;padding:.32rem 0 .45rem;line-height:1.45}"
    ".help-btn{position:fixed;bottom:1.1rem;right:1.1rem;width:2rem;height:2rem;"
    "border-radius:50%;background:#206bc4;color:#fff;border:none;font-size:.85rem;"
    "font-weight:700;cursor:pointer;z-index:400;box-shadow:0 2px 8px rgba(0,0,0,.5)}"
    ".hov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);"
    "z-index:500;align-items:center;justify-content:center}"
    ".hov.on{display:flex}"
    ".hov-box{background:#1a1f2e;border:1px solid #2d3748;border-radius:10px;"
    "padding:1.3rem 1.5rem;max-width:480px;width:90%;max-height:80vh;overflow-y:auto}"
    ".hov-box h3{color:#e8eef4;font-size:.95rem;margin-bottom:.6rem;font-weight:700}"
    ".hov-box p{color:#8899aa;font-size:.8rem;line-height:1.6;margin-bottom:.5rem}"
    ".hov-close{float:right;background:none;border:none;color:#6b7685;"
    "font-size:1.1rem;cursor:pointer;padding:.1rem .3rem;margin-left:.5rem}"
    # chat actions
    ".ch-acts{display:flex;align-items:center;gap:.4rem;margin-left:auto}"
    ".act-sel{background:#1a1f2e;border:1px solid #2d3748;border-radius:5px;"
    "color:#d0d7de;font-size:.74rem;padding:.18rem .35rem;cursor:pointer}"
    ".act-sel:focus{outline:none;border-color:#206bc4}"
    ".act-btn{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);"
    "border-radius:5px;color:#8899aa;font-size:.72rem;padding:.18rem .45rem;"
    "cursor:pointer;white-space:nowrap}"
    ".act-btn:hover{background:rgba(32,107,196,.28);color:#4da6ff;border-color:#206bc4}"
    ".act-btn.primary{background:#206bc4;color:#fff;border-color:#206bc4}"
    ".act-btn.primary:hover{background:#1a5aaa}"
    # suggest box
    ".sug-box{padding:.45rem .75rem;background:#1a1f2e;border-top:1px solid #2d3748;"
    "display:flex;flex-direction:column;gap:.3rem;flex-shrink:0}"
    ".sug-ta{width:100%;background:#0f1117;border:1px solid #2d3748;border-radius:5px;"
    "color:#d0d7de;padding:.3rem .45rem;font-size:.79rem;resize:vertical;"
    "min-height:3.5rem;font-family:inherit;line-height:1.4}"
    ".sug-ta:focus{outline:none;border-color:#206bc4}"
    ".sug-acts{display:flex;gap:.4rem}"
    # lead/outbox tables
    ".st-pill{display:inline-block;padding:.06rem .28rem;border-radius:5px;"
    "font-size:.62rem;font-weight:700;text-transform:uppercase}"
    ".s-pend{background:#2a2218;color:#ffd43b}"
    ".s-sent{background:#1f3a1f;color:#51cf66}"
    ".s-fail{background:#3a1f1f;color:#ff6b6b}"
)

_STC: dict[str, str] = {
    "new": "sn", "qualifying": "sq", "presenting": "sp", "objection": "so",
    "ready": "sr", "handed_off": "sh", "dormant": "sd", "manager": "sm",
}

_HELP_KEYS: dict[str, str] = {
    "inbox": "help.inbox",
    "coach": "help.coach",
    "know": "help.know",
    "products": "help.products",
    "members": "help.members",
    "settings": "help.settings",
    "leads": "help.leads",
    "outbox": "help.outbox",
}


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    secs = max(0, int((datetime.now(UTC).replace(tzinfo=None) - dt).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}м"
    if secs < 86400:
        return f"{secs // 3600}ч"
    return f"{secs // 86400}д"


def _badge(stage: str) -> str:
    return f'<span class="bg {_STC.get(stage, "sd")}">{_h.escape(t(f"stage.{stage}"))}</span>'


def _thread_item(row: object, active_tid: int | None) -> str:
    tid, name, stage, ts, last_msg, last_dir = row  # type: ignore[misc]
    on = " on" if tid == active_tid else ""
    arr = "→ " if last_dir == "out" else ("← " if last_dir == "in" else "")
    preview = _h.escape((arr + (last_msg or ""))[:80])
    return (
        f'<a class="ti{on}"'
        f' hx-get="/ui/chat/{tid}/panel" hx-target="#main" hx-push-url="true"'
        f' onclick="setOn(this)"'
        f' href="/ui/inbox">'
        f'<div class="ti-t"><span class="ti-n">{_h.escape(str(name or "Lead"))}</span>'
        f'<span class="ti-ts">{_ago(ts)}</span></div>'
        f'<div class="ti-p">{_badge(str(stage or "new"))} {preview}</div></a>'
    )


def thread_list_html(threads: list, active_tid: int | None = None) -> str:
    if not threads:
        return f'<div class="emp">{_h.escape(t("inbox.empty"))}</div>'
    return "".join(_thread_item(r, active_tid) for r in threads)


def _bubble(row: object) -> str:
    _, direction, sent_by, text, ts = row  # type: ignore[misc]
    who_key = f"who.{sent_by}" if sent_by in ("agent", "manager", "lead") else ""
    who = _h.escape(t(who_key) if who_key else str(sent_by or ""))
    txt = _h.escape(str(text or ""))
    if direction == "in":
        return (
            f'<div class="bb bb-i"><div class="bt">{txt}</div>'
            f'<div class="bm">{who} · {_ago(ts)}</div></div>'
        )
    mgr = " mgr" if sent_by == "manager" else ""
    return (
        f'<div class="bb bb-o{mgr}"><div class="bt">{txt}</div>'
        f'<div class="bm">{who} · {_ago(ts)}</div></div>'
    )


def messages_html(msgs: list, pending: list, tid: int) -> str:  # noqa: ARG001
    parts = [_bubble(r) for r in msgs]
    pend_label = _h.escape(t("chat.pending"))
    for row in pending:
        _, ptxt, _ = row  # type: ignore[misc]
        parts.append(
            f'<div class="bb bb-p"><div class="bt">{_h.escape(str(ptxt or ""))}</div>'
            f'<div class="bm">{pend_label}</div></div>'
        )
    return "".join(parts)


_STAGES = ("new", "qualifying", "presenting", "objection",
           "ready", "handed_off", "dormant", "manager")


def chat_header_html(tid: int, name: str, stage: str) -> str:
    """Renders just the chat header div (for hx-swap=outerHTML on stage change)."""
    opts = "".join(
        f'<option value="{s}" {"selected" if s == stage else ""}>'
        f'{_h.escape(t(f"stage.{s}"))}</option>'
        for s in _STAGES
    )
    stage_sel = (
        f'<form style="display:inline;margin:0"'
        f' hx-post="/ui/chat/{tid}/stage"'
        f' hx-target="#chat-hdr-{tid}"'
        f' hx-swap="outerHTML">'
        f'<select class="act-sel" name="stage"'
        f' onchange="this.form.requestSubmit()">{opts}</select>'
        f'</form>'
    )
    sug_lbl = _h.escape(t("chat.suggest"))
    suggest_btn = (
        f'<button class="act-btn"'
        f' hx-post="/ui/chat/{tid}/suggest"'
        f' hx-target="#sug-{tid}"'
        f' hx-swap="innerHTML">{sug_lbl}</button>'
    )
    return (
        f'<div class="ch" id="chat-hdr-{tid}">'
        f'<span class="ch-n">{_h.escape(name)}</span>'
        f'<div class="ch-acts">{stage_sel}{suggest_btn}</div></div>'
    )


def chat_panel_html(
    tid: int, name: str, stage: str, msgs: list, pending: list,
    lead_id: int | None = None,
) -> str:
    ph = _h.escape(t("chat.ph"))
    send_lbl = _h.escape(t("chat.send"))
    sug_lbl = _h.escape(t("chat.suggest"))
    # Stage selector
    opts = "".join(
        f'<option value="{s}" {"selected" if s == stage else ""}>'
        f'{_h.escape(t(f"stage.{s}"))}</option>'
        for s in _STAGES
    )
    stage_sel = (
        f'<form style="display:inline;margin:0"'
        f' hx-post="/ui/chat/{tid}/stage"'
        f' hx-target="#chat-hdr-{tid}"'
        f' hx-swap="outerHTML">'
        f'<select class="act-sel" name="stage" onchange="this.form.requestSubmit()">{opts}</select>'
        f'</form>'
    )
    suggest_btn = (
        f'<button class="act-btn"'
        f' hx-post="/ui/chat/{tid}/suggest"'
        f' hx-target="#sug-{tid}"'
        f' hx-swap="innerHTML">{sug_lbl}</button>'
    )
    return (
        f'<div class="ch" id="chat-hdr-{tid}">'
        f'<span class="ch-n">{_h.escape(name)}</span>'
        f'<div class="ch-acts">{stage_sel}{suggest_btn}</div></div>'
        f'<div class="msgs" id="msgs-{tid}">{messages_html(msgs, pending, tid)}</div>'
        f'<div id="sug-{tid}"></div>'
        f'<form class="fin"'
        f' hx-post="/ui/chat/{tid}/send"'
        f' hx-target="#msgs-{tid}"'
        f' hx-swap="innerHTML"'
        f' hx-on::after-request="this.reset();scrollMsgs({tid})">'
        f'<textarea name="text" rows="2" placeholder="{ph}"></textarea>'
        f'<button class="bsn">{send_lbl}</button></form>'
    )


def suggest_box_html(tid: int, draft: str) -> str:
    """HTML for the suggest box that appears below messages after clicking Suggest."""
    send_lbl = _h.escape(t("chat.send_stepan"))
    discard_lbl = _h.escape(t("chat.discard"))
    ph = _h.escape(t("chat.suggest_ph"))
    return (
        f'<div class="sug-box">'
        f'<textarea class="sug-ta" id="sug-ta-{tid}"'
        f' placeholder="{ph}">{_h.escape(draft)}</textarea>'
        f'<div class="sug-acts">'
        f'<button class="act-btn primary"'
        f' onclick="sendSuggest({tid})">{send_lbl}</button>'
        f'<button class="act-btn"'
        f' onclick="document.getElementById(\'sug-{tid}\').innerHTML=\'\'">'
        f'{discard_lbl}</button>'
        f'</div></div>'
    )


def app_shell(lang: str, main_html: str, active_nav: str = "inbox") -> str:
    def _na(key: str, href: str, icon: str, nav_id: str, extra: str = "") -> str:
        cls = "na on" if nav_id == active_nav else "na"
        lbl = _h.escape(t(key))
        return f'<a class="{cls}" href="{href}"{extra}><i class="{icon}"></i> {lbl}</a>'

    def _hna(key: str, panel: str, icon: str, nav_id: str) -> str:
        extra = (
            f' hx-get="{panel}" hx-target="#main" hx-push-url="{panel}"'
            f' onclick="setOn(this,\'na\')"'
        )
        return _na(key, panel, icon, nav_id, extra)

    coach_extra = (
        ' hx-get="/ui/coach/panel" hx-target="#main" hx-push-url="/ui/coach"'
        " onclick=\"setOn(this,'na')\""
    )
    nav = (
        _na("nav.inbox", "/ui/inbox", "fa-solid fa-inbox", "inbox")
        + _na("nav.coach", "#", "fa-solid fa-pencil", "coach", coach_extra)
        + '<div class="nav-sep"></div>'
        + _hna("nav.leads", "/ui/leads/panel", "fa-solid fa-user-tag", "leads")
        + _hna("nav.know", "/ui/knowledge/panel", "fa-solid fa-book", "know")
        + _hna("nav.products", "/ui/products/panel", "fa-solid fa-box", "products")
        + _hna("nav.members", "/ui/members/panel", "fa-solid fa-users", "members")
        + _hna("nav.settings", "/ui/settings/panel", "fa-solid fa-gear", "settings")
        + '<div class="nav-sep"></div>'
        + _hna("nav.outbox", "/ui/outbox/panel", "fa-solid fa-paper-plane", "outbox")
        + _na("nav.tables", "/admin/", "fa-solid fa-table", "tables")
    )

    def _lb(code: str) -> str:
        cls = "lb on" if code == lang else "lb"
        return f'<a class="{cls}" href="/ui/lang/{code}">{code.upper()}</a>'

    help_key = _HELP_KEYS.get(active_nav, "")
    help_title = _h.escape(t(f"nav.{active_nav}") if active_nav in _HELP_KEYS else t("help.title"))
    help_body = _h.escape(t(help_key)) if help_key else ""

    script = (
        "function setOn(el,cls){"
        "cls=cls||'ti';"
        "document.querySelectorAll('.'+cls+'.on').forEach(e=>e.classList.remove('on'));"
        "el.classList.add('on');}"
        "function scrollMsgs(tid){"
        "var m=document.getElementById('msgs-'+tid);if(m)m.scrollTop=m.scrollHeight;}"
        "document.addEventListener('htmx:afterSettle',function(e){"
        "var m=e.target&&e.target.classList&&e.target.classList.contains('msgs')?e.target"
        ":e.target.querySelector&&e.target.querySelector('.msgs');"
        "if(m)m.scrollTop=m.scrollHeight;});"
        "function toggleHelp(){"
        "document.getElementById('hov').classList.toggle('on');}"
        # sendSuggest: post draft textarea as manager message
        "function sendSuggest(tid){"
        "var ta=document.getElementById('sug-ta-'+tid);"
        "if(!ta||!ta.value.trim())return;"
        "var fd=new FormData();fd.append('text',ta.value);"
        "htmx.ajax('POST','/ui/chat/'+tid+'/send',{"
        "target:'#msgs-'+tid,swap:'innerHTML',values:fd});"
        "document.getElementById('sug-'+tid).innerHTML='';}"
    )
    inbox_lbl = _h.escape(t("nav.inbox"))
    help_lbl = _h.escape(t("help.title"))
    help_overlay = (
        f'<button class="help-btn" onclick="toggleHelp()" title="{help_lbl}">?</button>'
        f'<div class="hov" id="hov" onclick="if(event.target===this)toggleHelp()">'
        f'<div class="hov-box">'
        f'<button class="hov-close" onclick="toggleHelp()">✕</button>'
        f'<h3>{help_title}</h3>'
        f'<p>{help_body}</p>'
        f'</div></div>'
    )
    return (
        f'<!doctype html><html lang="{lang}"><head>'
        f'<meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>Stepan 2</title>'
        f'<link rel="stylesheet" href="{_FA}">'
        f'<script src="{_HTMX}" defer></script>'
        f'<style>{_CSS}</style></head><body>'
        f'<aside class="sid">'
        f'<div class="sid-top"><span class="logo">Stepan 2</span></div>'
        f'<nav class="sid-nav">{nav}</nav>'
        f'<div class="sid-ft">'
        f'<div style="font-size:.63rem;color:#4a5568">lang</div>'
        f'<div class="lrow">{_lb("ru")}{_lb("en")}{_lb("id")}</div>'
        f'</div></aside>'
        f'<div class="thr">'
        f'<div class="thr-h">{inbox_lbl}</div>'
        f'<div id="tl" hx-get="/ui/threads" hx-trigger="load, every 30s" hx-swap="innerHTML"></div>'
        f'</div>'
        f'<div id="main">{main_html}</div>'
        f'{help_overlay}'
        f'<script>{script}</script>'
        f'</body></html>'
    )
