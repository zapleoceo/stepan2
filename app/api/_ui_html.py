"""HTML generators for the 3-column manager UI (sidebar + thread list + panel)."""
from __future__ import annotations

import html as _h
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.conversation.needs import NeedsProfile
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from ._i18n import t

# Branch tz offset (hours) for the current render — chat timestamps show branch-local time.
_render_tz_h: ContextVar[int] = ContextVar("render_tz_h", default=0)


def set_render_tz(offset_h: int) -> None:
    """Set the branch tz offset for timestamp rendering in this request/task."""
    _render_tz_h.set(int(offset_h or 0))

_HTMX = "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"
_FA = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"

_CSS = (
    "*{box-sizing:border-box;margin:0;padding:0}"
    # Firefox scrollbar styling (Chromium/Safari use ::-webkit-scrollbar below); keeps every
    # scroll region slim instead of a full-width OS bar in Firefox.
    "*{scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.18) transparent}"
    # overflow-x hidden (no sideways scroll) but overflow-y left OPEN on the root so the
    # browser's OWN pull-to-refresh can fire: the columns are flex/position:fixed at 100%
    # height with inner overflow:auto scrollers, so the document itself never actually
    # scrolls on desktop — but a downward overscroll at the top of a region chains to the
    # document and triggers the native refresh (works in desktop-mode-on-phone too). No JS
    # emulation. Chats open at the newest message (scrollAllBot/pinBot), so the feed is
    # never sitting at the top where an accidental pull could fire.
    "html,body{height:100%;overflow-x:hidden}"
    "body{display:flex;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "background:#0f1117;color:#d0d7de;font-size:14px}"
    ".sid{width:210px;flex-shrink:0;background:#141925;border-right:1px solid #2d3748;"
    "display:flex;flex-direction:column;transition:width .15s}"
    ".sid-top{padding:.7rem .9rem .45rem;border-bottom:1px solid #2d3748;"
    "display:flex;align-items:center;justify-content:space-between}"
    ".logo{font-size:1.05rem;font-weight:800;color:#fff}"
    ".sid-nav{flex:1;padding:.4rem 0;overflow-y:auto;overflow-x:hidden}"
    ".na{display:flex;align-items:center;gap:.5rem;padding:.4rem .75rem;color:#8899aa;"
    "font-size:.81rem;text-decoration:none;border-radius:6px;margin:.05rem .45rem;"
    "transition:background .12s,color .12s;cursor:pointer;white-space:nowrap;overflow:hidden}"
    ".na:hover{background:rgba(255,255,255,.08);color:#d0d7de}"
    ".na.on{background:rgba(32,107,196,.22);color:#4da6ff}"
    ".na i{width:14px;text-align:center;flex-shrink:0}"
    ".na-lbl{overflow:hidden;text-overflow:ellipsis}"
    ".na-badge{margin-left:auto;padding:.05rem .45rem;border-radius:8px;font-size:.7rem;"
    "font-weight:700;background:#206bc4;color:#fff;flex-shrink:0}"
    ".na-badge:empty{display:none}"
    ".sid.collapsed .na-badge{display:none}"
    ".nav-sep{height:1px;background:#2d3748;margin:.4rem .9rem}"
    ".sid-ft{padding:.55rem .7rem .7rem;border-top:1px solid #2d3748}"
    ".lrow{display:flex;gap:.22rem;margin-top:.3rem}"
    ".lb{flex:1;padding:.22rem .1rem;background:rgba(255,255,255,.07);"
    "border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#8899aa;"
    "font-size:.68rem;font-weight:600;text-align:center;text-decoration:none}"
    ".lb.on,.lb:hover{background:rgba(32,107,196,.28);color:#4da6ff;border-color:#206bc4}"
    ".bft-lbl{font-size:.63rem;color:#4a5568;margin-bottom:.2rem}"
    ".bft-sel{width:100%;background:#1a1f2e;border:1px solid #2d3748;border-radius:4px;"
    "color:#d0d7de;font-size:.72rem;padding:.22rem .35rem;cursor:pointer}"
    # sidebar collapse
    ".sb-col{background:none;border:none;color:#4a5568;cursor:pointer;font-size:.9rem;"
    "padding:.1rem .25rem;line-height:1;flex-shrink:0}"
    ".sb-col:hover{color:#d0d7de}"
    ".sid.collapsed{width:48px!important;min-width:48px!important}"
    ".sid.collapsed .logo{display:none}"
    ".sid.collapsed .na-lbl{display:none}"
    ".sid.collapsed .nav-sep,.sid.collapsed .sid-ft{display:none}"
    ".sid.collapsed .na{justify-content:center;padding:.4rem 0;margin:.05rem .2rem}"
    ".sid.collapsed .sid-top{justify-content:center;padding:.7rem .3rem .45rem}"
    # sidebar + thread resize handles
    ".sbrz,.thrz{width:4px;flex-shrink:0;background:#1e2636;cursor:col-resize;"
    "transition:background .15s;z-index:10}"
    ".sbrz:hover,.sbrz.drag,.thrz:hover,.thrz.drag{background:#206bc4}"
    ".thr{width:305px;flex-shrink:0;background:#0f1117;border-right:1px solid #2d3748;"
    "display:flex;flex-direction:column;overflow:hidden}"
    ".thr-h{padding:.56rem .8rem;border-bottom:1px solid #2d3748;font-size:.82rem;"
    "font-weight:600;color:#e8eef4;flex-shrink:0}"
    ".ti-q{width:calc(100% - 1rem);margin:.4rem .5rem;padding:.34rem .55rem;flex-shrink:0;"
    "background:#0f1621;border:1px solid #2d3748;border-radius:6px;color:#e8eef4;"
    "font-size:.76rem;outline:none}.ti-q:focus{border-color:#206bc4}"
    "#tl{flex:1;overflow-y:auto}"
    "#tl::-webkit-scrollbar{width:4px}"
    "#tl::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}"
    ".ti{display:flex;align-items:flex-start;gap:.5rem;padding:.52rem .8rem;"
    "border-bottom:1px solid rgba(255,255,255,.04);"
    "text-decoration:none;color:inherit;cursor:pointer;transition:background .1s;"
    "border-left:2px solid transparent}"
    ".ti:hover{background:rgba(255,255,255,.05)}"
    ".ti.on{background:rgba(32,107,196,.14);border-left-color:#206bc4}"
    ".ti-av{width:32px;height:32px;border-radius:50%;flex-shrink:0;"
    "background-size:cover;background-position:center;"
    "background-color:#2a3550;color:#6b7685;font-size:.78rem;font-weight:700;"
    "display:flex;align-items:center;justify-content:center;margin-top:.06rem}"
    ".ch-av{width:36px;height:36px;border-radius:50%;flex-shrink:0;"
    "background-size:cover;background-position:center;"
    "background-color:#2a3550;color:#6b7685;font-size:.88rem;font-weight:700;"
    "display:flex;align-items:center;justify-content:center}"
    ".ti-body{flex:1;min-width:0;overflow:hidden}"
    ".ti-t{display:flex;align-items:baseline;gap:.35rem;margin-bottom:.1rem}"
    ".ti-n{font-weight:600;color:#e8eef4;font-size:.84rem;flex:1;overflow:hidden;"
    "text-overflow:ellipsis;white-space:nowrap}"
    ".ti-ts{font-size:.68rem;color:#4a5568;flex-shrink:0}"
    ".ti-p{font-size:.74rem;color:#6b7685;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap;margin-top:.05rem}"
    ".ti-handle{font-size:.67rem;color:#4a5568;margin-top:.02rem}"
    ".ti-sub{font-size:.62rem;color:#4a5568;display:flex;gap:.35rem;margin-top:.06rem;"
    "white-space:nowrap;overflow:hidden;align-items:center}"
    ".ti-cnt{opacity:.7}"
    # source bar (lead origin — ad / story / direct)
    ".srcbar{display:flex;align-items:center;gap:.45rem;padding:.22rem .9rem;"
    "background:#0d1017;border-bottom:1px solid #1e2636;font-size:.71rem;flex-shrink:0}"
    ".srclbl{display:inline-flex;align-items:center;gap:.25rem;color:#6b7685}"
    ".src-paid{color:#d6a96f}.src-story{color:#4da6ff}.src-direct{color:#4a5568}"
    ".srcid{font-family:ui-monospace,monospace;font-size:.65rem;color:#4a5568;cursor:pointer;"
    "padding:.1rem .3rem;background:#1a1f2e;border-radius:3px;text-decoration:none}"
    ".srcid:hover{color:#8899aa}"
    ".srcthumb{width:30px;height:30px;border-radius:4px;object-fit:cover;"
    "border:1px solid #2d3748;flex-shrink:0}"
    ".ch-meta{width:100%;font-size:.67rem;color:#4a5568;display:flex;gap:.55rem;"
    "align-items:center;margin-top:.18rem;padding-top:.22rem;"
    "border-top:1px solid #1e2636;flex-wrap:wrap}"
    ".ch-meta a{color:#4a5568;text-decoration:none}"
    ".ch-meta a:hover{color:#8899aa}"
    ".bg{display:inline-block;padding:.07rem .3rem;border-radius:5px;font-size:.6rem;"
    "font-weight:700;text-transform:uppercase;margin-right:.18rem}"
    ".sn{background:#1e3a5f;color:#4da6ff}.snu{background:#3a2a10;color:#d6a96f}"
    ".sq{background:#2a1f5f;color:#9b7aff}"
    ".sp{background:#1f3a2a;color:#4adb7a}.so{background:#3a2a1f;color:#ffa94d}"
    ".sr{background:#1f3a2a;color:#51cf66}.sh{background:#163030;color:#22b8cf}"
    ".sd{background:#2a2a2a;color:#868e96}.sm{background:#3a1f1f;color:#ff6b6b}"
    # two-row funnel: row 1 = headline metrics (total/bot-on/in-funnel, one is non-clickable
    # via .info), row 2 = per-stage + blocked chips. Each row is a compact icon+count step,
    # full label on hover (title).
    ".fnl{display:flex;gap:.15rem;padding:.3rem .5rem;border-bottom:1px solid #1e2636;"
    "flex-shrink:0;align-items:stretch}"
    ".fnl:first-of-type{border-bottom-color:#161b26}"
    ".fstep{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;"
    "gap:.05rem;padding:.2rem .1rem;border-radius:6px;text-decoration:none;cursor:pointer;"
    "border:1px solid transparent;border-top:2px solid transparent;transition:background .1s}"
    ".fstep:hover{background:#1c2230}"
    ".fstep.on{background:#1e2b40;border-color:#31527a}"
    ".fstep.info{cursor:default}"
    ".fstep.info:hover{background:transparent}"
    ".fst-i{font-size:.82rem;line-height:1}"
    ".fst-n{font-size:.72rem;font-weight:700;color:#c8d6e5}"
    ".fstep.on .fst-n{color:#fff}"
    "._legacy-fnl{font-size:.62rem;color:#4a5568;cursor:pointer;text-decoration:none;"
    "padding:.12rem .3rem;border-radius:3px}"
    # reports panel
    ".kpi-row{display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:.65rem}"
    # one-line sales funnel
    ".fnl-line{display:flex;gap:.3rem;align-items:stretch}"
    ".fnl-step{flex:1;min-width:0;background:#141925;border:1px solid #2d3748;"
    "border-radius:6px;padding:.35rem .3rem;text-align:center;position:relative;"
    "overflow:hidden;cursor:help}"
    ".fnl-bar{position:absolute;top:0;left:0;right:0;height:3px}"
    ".fnl-num{font-size:1.05rem;font-weight:800;color:#e8eef4;line-height:1.1;margin-top:.15rem}"
    ".fnl-nm{font-size:.6rem;color:#8899aa;text-transform:capitalize;white-space:nowrap;"
    "overflow:hidden;text-overflow:ellipsis}"
    ".fnl-pct{font-size:.62rem;color:#5a6472;font-weight:700}"
    ".fnl-side-row{display:flex;gap:.35rem;flex-wrap:wrap;margin:.4rem 0 .2rem}"
    ".fnl-side{font-size:.63rem;color:#c3cede;border:1px solid;border-radius:10px;"
    "padding:.05rem .5rem;cursor:help}"
    # report date-range form
    ".rep-dates{display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;margin-bottom:.7rem}"
    ".rep-dates label{display:flex;flex-direction:column;gap:.15rem;font-size:.63rem;color:#8899aa}"
    ".rep-dates input[type=date]{background:#0f1117;border:1px solid #2d3748;color:#d0d7de;"
    "border-radius:5px;padding:.25rem .4rem;font-size:.72rem;color-scheme:dark}"
    ".rep-dates button{background:#206bc4;color:#fff;border:none;border-radius:5px;"
    "padding:.32rem .7rem;font-size:.72rem;font-weight:600;cursor:pointer}"
    ".rep-dhint{font-size:.6rem;color:#5a6472;font-style:italic;align-self:center}"
    ".kpi{background:#141925;border:1px solid #2d3748;border-radius:6px;"
    "padding:.5rem .75rem;min-width:90px}"
    ".kpi-n{font-size:1.5rem;font-weight:800;line-height:1.15}"
    ".kpi-l{font-size:.64rem;color:#6b7685;margin-top:.1rem}"
    ".rep-tbl{width:100%;border-collapse:collapse;font-size:.77rem;margin-bottom:.7rem}"
    ".rep-tbl th{text-align:left;color:#6b7685;font-weight:600;font-size:.65rem;"
    "padding:.25rem .4rem;border-bottom:1px solid #2d3748}"
    ".rep-tbl td{padding:.25rem .4rem;border-bottom:1px solid #1a2033}"
    ".rep-n{text-align:right;font-weight:700;color:#e8eef4}"
    ".hchart{display:flex;align-items:flex-end;gap:2px;height:64px;margin-top:.3rem}"
    ".hbar{display:flex;flex-direction:column;align-items:center;flex:1;height:100%}"
    ".hbar-l{font-size:.52rem;color:#4a5568;margin-top:1px;line-height:1}"
    "#main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}"
    ".ch{padding:.56rem .9rem;border-bottom:1px solid #2d3748;background:#141925;"
    "display:flex;align-items:center;gap:.55rem;flex-shrink:0;flex-wrap:wrap}"
    ".ch-n{font-weight:600;color:#e8eef4;font-size:.9rem}"
    ".ch-sub{font-size:.67rem;color:#4a5568;font-family:monospace}"
    ".msgs{flex:1;overflow-y:auto;padding:.72rem .95rem;display:flex;"
    "flex-direction:column;gap:.3rem}"
    # pending bubbles live inside their own #pend-<tid> wrapper, not directly in .msgs, so
    # their align-self:flex-end needs THIS wrapper to be a flex column too — else they fall
    # back to block layout and render left instead of on the right like a real outgoing msg.
    "[id^='pend-']{display:flex;flex-direction:column;gap:.3rem}"
    ".msgs::-webkit-scrollbar{width:4px}"
    ".msgs::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:2px}"
    ".bb{display:flex;flex-direction:column;max-width:72%}"
    ".bb-i{align-self:flex-start}.bb-o{align-self:flex-end}.bb-p{opacity:.6;align-self:flex-end}"
    ".bb-ex{opacity:.4;filter:grayscale(.7)}"  # cleared: greyed, out of Stepan's context
    ".bb-ex .bt{background:#20242c!important;border:1px dashed #3a4453}"
    ".sys-log{align-self:center;max-width:90%;font-size:.72rem;color:#5a6472;"
    "text-align:center;margin:.3rem 0;font-style:italic}"
    ".delx-pop{display:inline-flex;gap:.2rem;align-items:center;margin-left:.2rem}"
    ".delx-pop button{border:none;border-radius:3px;cursor:pointer;font-size:.7rem;"
    "padding:.02rem .3rem;line-height:1.3}"
    ".delx-y{background:#862e2e;color:#fff}.delx-n{background:#2d3748;color:#c3cede}"
    ".emo-bar{display:flex;flex-wrap:wrap;gap:.15rem;padding:.25rem .1rem 0}"
    ".emo-bar button{background:none;border:none;cursor:pointer;font-size:1.05rem;"
    "line-height:1;padding:.1rem .18rem;border-radius:4px}"
    ".emo-bar button:hover{background:#222836}"
    ".bt{padding:.4rem .56rem;border-radius:9px;font-size:.8rem;"
    "white-space:pre-wrap;word-break:break-word}"
    ".bb-i .bt{background:#232a3b;border:1px solid #2d3748}"
    ".bb-o .bt{background:#1e3a5f}.bb-o.mgr .bt{background:#2a1f3a}"
    ".bb-p .bt{border:1px dashed #3a5578}"  # queued (unsent) look
    ".b-tr{font-size:.75rem;color:#5aa2ff;margin-top:.12rem}.bb-o .b-tr{text-align:right}"
    ".bt.trview{color:#5aa2ff}"
    ".bm{font-size:.63rem;color:#4a5568;margin-bottom:.1rem;display:flex;"
    "align-items:center;gap:.2rem}"
    ".bb-o .bm{justify-content:flex-end}"
    ".b-llm{font-size:.66rem;color:#93a1b3;opacity:.95;margin-top:.2rem;max-width:100%;"
    "font-family:ui-monospace,monospace;letter-spacing:.1px;"
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
    ".bb-o .b-llm{text-align:right}"
    # per-message translate and delete buttons — hidden until bubble hover
    ".trx,.delx{background:none;border:none;color:#4a5568;cursor:pointer;"
    "font-size:.68rem;padding:0 .1rem;line-height:1;opacity:0;"
    "transition:opacity .12s,color .1s}"
    ".bb:hover .trx,.bb:hover .delx{opacity:.5}"
    ".trx:hover{opacity:1!important;color:#4da6ff}"
    ".delx:hover{opacity:1!important;color:#ff6b6b}"
    ".fin{padding:.45rem .8rem .52rem;border-top:1px solid #2d3748;"
    "background:#141925;flex-shrink:0}"
    ".fin-tools{display:flex;align-items:center;gap:.3rem;margin-bottom:.3rem;overflow-x:auto}"
    ".fin-tools .act-btn{flex-shrink:0}"
    ".fin-tools .emo-bar{flex-wrap:nowrap;flex-shrink:0}"
    ".fin-row{display:flex;gap:.4rem;align-items:flex-end}"
    ".fin textarea{flex:1;background:#1a1f2e;border:1px solid #2d3748;border-radius:6px;"
    "color:#d0d7de;padding:.36rem .52rem;font-size:.8rem;resize:none;"
    "font-family:inherit;line-height:1.4;max-height:9.5rem;overflow-y:hidden}"
    ".fin textarea:focus{outline:none;border-color:#206bc4}"
    ".bsn{background:#206bc4;color:#fff;border:none;border-radius:6px;"
    "padding:0 .88rem;font-size:.78rem;font-weight:600;cursor:pointer;height:2.1rem}"
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
    ".pnl-body{flex:1;overflow-y:auto;overflow-x:auto;padding:.55rem .8rem}"
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
    ".htmx-indicator{display:none}"
    ".htmx-request .htmx-indicator,.htmx-request.htmx-indicator{display:inline-block}"
    ".msg-prev{max-width:220px;max-height:280px;border-radius:8px;display:block;margin-top:.25rem}"
    ".rcpt{opacity:.55;font-size:.7rem}.rcpt.seen{color:#4da3ff;opacity:.95}"
    ".pres.on{color:#51cf66}.ti-fl{color:#6b7685;font-size:.62rem}"
    ".ti-br{color:#8b98a5;font-size:.6rem;margin-left:.3rem;"
    "background:rgba(255,255,255,.07);border-radius:3px;padding:0 .28rem}"
    ".ti-off{font-size:.62rem;margin-left:.3rem;opacity:.85;filter:grayscale(.3)}"
    ".oq-chat{color:#4da3ff;text-decoration:none;font-family:ui-monospace,monospace;"
    "font-size:.74rem}.oq-chat:hover{text-decoration:underline}"
    ".kdoc{background:#1a1f2e;border:1px solid #2d3748;border-radius:7px;"
    "padding:.5rem .7rem;margin-bottom:.3rem;cursor:pointer}"
    ".kdoc:hover{border-color:#4a5568}"
    ".kdoc-slug{font-family:ui-monospace,monospace;font-size:.7rem;color:#4da6ff;"
    "margin-bottom:.1rem}"
    ".kdoc-title{font-weight:600;color:#e8eef4;font-size:.82rem;margin-bottom:.1rem}"
    ".kdoc-preview{font-size:.74rem;color:#6b7685;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap}"
    ".hint{font-size:.74rem;color:#4a5568;padding:.32rem 0 .45rem;line-height:1.45}"
    # ── knowledge-base tree / editor / history ──
    ".kb-tabs{display:flex;gap:.3rem;align-items:center;padding:.4rem .5rem;"
    "border-bottom:1px solid #222836}"
    ".kb-tab{flex:1;background:#161b26;border:1px solid #2d3748;color:#93a1b3;"
    "padding:.3rem;border-radius:5px;font-size:.76rem;cursor:pointer}"
    ".kb-tab.on{background:#206bc4;border-color:#206bc4;color:#fff}"
    ".kb-reix{background:#161b26;border:1px solid #2d3748;color:#93a1b3;border-radius:5px;"
    "padding:.3rem .5rem;cursor:pointer;font-size:.9rem}"
    ".kb-reix:hover{border-color:#4da6ff;color:#4da6ff}"
    ".kb-reix-msg{display:block;font-size:.72rem;color:#5aa2ff;padding:.25rem .6rem}"
    ".kb-grp{margin:.15rem 0}"
    ".kb-grp>summary{cursor:pointer;font-size:.72rem;text-transform:uppercase;"
    "letter-spacing:.04em;color:#6b7685;padding:.3rem .55rem;user-select:none}"
    ".ch-slug{font-family:ui-monospace,monospace;font-size:.72rem;color:#4da6ff;"
    "margin-left:.4rem}"
    ".kb-by{font-size:.7rem;color:#6b7685}"
    ".kb-sec{margin-bottom:.5rem}"
    ".kb-sec-h{display:block;font-size:.74rem;font-weight:600;color:#9db3c9;"
    "margin-bottom:.15rem}"
    ".kb-rev{border:1px solid #2d3748;border-radius:6px;margin-bottom:.5rem;overflow:hidden}"
    ".kb-rev-h{display:flex;gap:.6rem;align-items:center;padding:.35rem .55rem;"
    "background:#161b26;font-size:.74rem}"
    ".kb-diff{font-family:ui-monospace,monospace;font-size:.68rem;padding:.3rem .55rem;"
    "max-height:14rem;overflow-y:auto}"
    ".d-add{color:#7ee2a8}.d-del{color:#ff9b9b}.d-ctx{color:#5a6675}"
    # ── captured needs (VPC) box in chat header ──
    ".nd-box{padding:.3rem .7rem;border-top:1px solid #222836;display:flex;"
    "flex-direction:column;gap:.2rem}"
    ".nd-row{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center}"
    ".nd-lbl{font-size:.66rem;color:#6b7685;min-width:3.6rem}"
    ".nd-chip{font-size:.68rem;padding:.08rem .4rem;border-radius:10px;"
    "border:1px solid #2d3748}"
    ".nd-job{background:#12233b;color:#7db8ff}"
    ".nd-pain{background:#301a1a;color:#ffb0a3}"
    ".nd-gain{background:#132a1e;color:#8fe3ac}"
    # ── Stepan on/off switches (platform + branch) ──
    ".tgl-row{margin:.2rem 0}"
    ".tgl-hint{margin:.25rem 0;font-size:.63rem;line-height:1.3;color:#6b7688;font-style:italic}"
    ".tgl-btn{width:100%;display:flex;align-items:center;gap:.4rem;background:#161b26;"
    "border:1px solid #2d3748;border-radius:6px;padding:.32rem .5rem;cursor:pointer}"
    ".tgl-btn:hover{border-color:#3a5578}"
    ".tgl-lbl{flex:1;text-align:left;font-size:.72rem;color:#c3cede}"
    ".tgl-status{font-size:.64rem;font-weight:700;letter-spacing:.03em}"
    ".tgl-track{width:2rem;height:.95rem;border-radius:1rem;position:relative;"
    "flex-shrink:0;transition:background .15s}"
    ".tgl-knob{position:absolute;top:.1rem;left:.1rem;width:.75rem;height:.75rem;"
    "border-radius:50%;background:#fff;transition:transform .15s}"
    ".help-btn{width:1.45rem;height:1.45rem;border-radius:50%;background:#206bc4;"
    "color:#fff;border:none;font-size:.72rem;font-weight:700;cursor:pointer;"
    "flex-shrink:0;line-height:1}"
    # help mode: the ? toggle lights up, every annotated element gets a dashed outline
    # ('hover me'), and one fixed-position tip floats ABOVE the page so card/table
    # overflow can never clip it.
    "body.help-mode .help-btn{background:#e2b33d;color:#1a1f2e}"
    "body.help-mode [data-help]{outline:1px dashed #4da6ff;outline-offset:2px;"
    "cursor:help}"
    "#help-tip{position:fixed;z-index:900;max-width:300px;background:#1b2432;"
    "border:1px solid #3a5578;border-radius:8px;padding:.5rem .7rem;font-size:.78rem;"
    "color:#cfe0f4;line-height:1.5;box-shadow:0 6px 24px rgba(0,0,0,.55);display:none;"
    "pointer-events:none}"
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
    # mobile floating bar (hidden on desktop)
    ".mbar{display:none}"
    ".mbtn{position:fixed;top:.5rem;z-index:70;width:2.2rem;height:2.2rem;border:none;"
    "border-radius:50%;background:#206bc4;color:#fff;font-size:1.2rem;line-height:1;"
    "box-shadow:0 2px 8px rgba(0,0,0,.4);cursor:pointer}"
    ".mnav{left:.5rem}.mback{left:.5rem;display:none}"
    # ── responsive: phones/tablets (all desktop CSS above is untouched) ──
    "@media (max-width:760px){"
    "body{padding-top:0}"
    ".mbar{display:block}"
    # sidebar becomes a slide-in overlay, opened by the ☰ button
    ".sid{position:fixed;left:0;top:0;bottom:0;z-index:80;transform:translateX(-100%);"
    "transition:transform .2s ease;box-shadow:2px 0 12px rgba(0,0,0,.5)}"
    "body.nav-open .sid{transform:translateX(0)}"
    ".sbrz,.thrz{display:none}"
    # thread list is the default full-width mobile view
    ".thr{width:auto;flex:1;padding-top:2.9rem}"
    # #main (a chat) slides in over the thread list; back button reveals the list again
    "#main{position:fixed;inset:0;z-index:60;background:#0f1117;transform:translateX(100%);"
    "transition:transform .2s ease}"
    "body.chat-open #main{transform:translateX(0)}"
    "body.chat-open .mnav{display:none}body.chat-open .mback{display:block}"
    # the fixed ‹ back button sits top-left over #main — pad the header so it does not
    # cover the lead avatar
    "body.chat-open .ch{padding-left:2.9rem}"
    # touch has no hover: keep the per-message translate/delete buttons visible
    ".trx,.delx{opacity:.5}"
    # give tables room to scroll horizontally instead of overflowing the viewport
    ".tbl,.rep-tbl{min-width:520px}"
    "}"
)

_STC: dict[str, str] = {
    "new": "sn", "nurturing": "snu", "qualifying": "sq", "presenting": "sp",
    "objection": "so", "ready": "sr", "handed_off": "sh", "dormant": "sd",
    "manager": "sm",
}

_PIPELINE = ("new", "nurturing", "qualifying", "presenting", "objection", "ready", "handed_off")
_SIDE_STAGES = ("dormant", "manager")
_STAGE_COLOR: dict[str, str] = {
    "new": "#4da6ff", "nurturing": "#d6a96f", "qualifying": "#9b7aff",
    "presenting": "#4adb7a", "objection": "#ffa94d", "ready": "#51cf66",
    "handed_off": "#22b8cf", "dormant": "#868e96", "manager": "#ff6b6b",
}
_STAGE_ICON = {
    "new": "✨", "nurturing": "🌱", "qualifying": "🔍", "presenting": "📊",
    "objection": "💬", "ready": "✅", "handed_off": "🤝", "dormant": "😴", "manager": "👤",
}
# Leads actively moving through the funnel (not won, not parked/handed to a human).
_IN_FUNNEL_STAGES = ("new", "nurturing", "qualifying", "presenting", "objection")


def funnel_html(
    counts: dict[str, int], active_stage: str = "", bot_on: int = 0, blocked: int = 0,
) -> str:
    """Two-row inbox funnel: row 1 = headline metrics (total / bot-on / in-funnel), row 2 =
    per-stage counts (each filters the thread list) plus a 🚫 blocked chip — is_blocked is a
    lead flag, not a funnel stage, so without this chip a blocked lead is unfindable."""
    total = sum(counts.values())
    in_funnel = sum(counts.get(s, 0) for s in _IN_FUNNEL_STAGES)

    def step(stage: str | None, label: str, n: int, icon: str, color: str | None) -> str:
        url = f"/ui/threads?stage={stage}" if stage else "/ui/threads"
        push = f"/ui/inbox?stage={stage}" if stage else "/ui/inbox"
        on = " on" if (stage or "") == (active_stage or "") else ""
        bar = f"border-top-color:{color}" if color else ""
        return (
            f'<a class="fstep{on}" style="{bar}" hx-get="{url}" hx-push-url="{push}"'
            f' hx-target="#tl" hx-swap="innerHTML" data-help="{label}"'
            f' onclick="setFnl(this)" title="{label}">'
            f'<span class="fst-i">{icon}</span><span class="fst-n">{n}</span></a>'
        )

    def metric(label: str, n: int, icon: str) -> str:
        """A headline number with no filter behind it (bot-on / in-funnel are aggregates,
        not a single stage) — same box look as a step, but inert."""
        return (
            f'<span class="fstep info" data-help="{label}" title="{label}">'
            f'<span class="fst-i">{icon}</span><span class="fst-n">{n}</span></span>'
        )

    row1 = [
        step(None, _h.escape(t("fnl.all")), total, "📥", None),
        metric(_h.escape(t("fnl.bot_on")), bot_on, "🤖"),
        metric(_h.escape(t("fnl.in_funnel")), in_funnel, "🎯"),
    ]
    row2 = [
        step(s, _h.escape(t(f"stage.{s}")), counts.get(s, 0),
            _STAGE_ICON.get(s, "•"), _STAGE_COLOR[s])
        for s in (*_PIPELINE, *_SIDE_STAGES)
    ]
    row2.append(step("blocked", _h.escape(t("fnl.blocked")), blocked, "🚫", "#ff6b6b"))
    return f'<div class="fnl">{"".join(row1)}</div><div class="fnl">{"".join(row2)}</div>'

_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def ig_post_url(media_id: str | None) -> str | None:
    """Convert a numeric Instagram media id to its /p/<shortcode>/ post URL."""
    if not media_id:
        return None
    digits = str(media_id).split("_", 1)[0]
    if not digits.isdigit():
        return None
    n = int(digits)
    if n == 0:
        code = _IG_ALPHABET[0]
    else:
        chars = []
        while n > 0:
            n, rem = divmod(n, 64)
            chars.append(_IG_ALPHABET[rem])
        code = "".join(reversed(chars))
    return f"https://www.instagram.com/p/{code}/"


_HELP_KEYS: dict[str, str] = {
    "inbox": "help.inbox",
    "coach": "help.coach",
    "know": "help.know",
    "products": "help.products",
    "members": "help.members",
    "settings": "help.settings",
    "leads": "help.leads",
    "outbox": "help.outbox",
    "branches": "help.branches",
    "reports": "help.reports",
}


# Section-level help texts (help.*) now ride on the nav links as data-help tips —
# see _na in app_shell. No out-of-band machinery needed: the tips live in the shell
# markup itself and the delegated hover handler reads them straight off the DOM.


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    secs = max(0, int((datetime.now(UTC).replace(tzinfo=None) - dt).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}{t('time.m')}"
    if secs < 86400:
        return f"{secs // 3600}{t('time.h')}"
    return f"{secs // 86400}{t('time.d')}"


def _as_dt(v: object) -> datetime | None:
    """Coerce a raw SQL value (datetime on Postgres, ISO str on SQLite) to naive datetime."""
    if v is None or isinstance(v, datetime):
        return v  # type: ignore[return-value]
    try:
        return datetime.fromisoformat(str(v).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def _fmt_time(dt: datetime | None) -> str:
    """Branch-local DD.MM HH:MM:SS — always includes the date, not just time-of-day, so a
    message/event timestamp is never ambiguous about which day it happened."""
    if dt is None:
        return ""
    local = dt + timedelta(hours=_render_tz_h.get())
    return local.strftime("%d.%m %H:%M:%S")


def _fmt_dt_short(dt: datetime | None) -> str:
    """Branch-local DD.MM HH:MM (no seconds) — for the compact sidebar thread list, where
    an explicit last-message date/time replaces the old vague '2h ago' style label."""
    if dt is None:
        return ""
    local = dt + timedelta(hours=_render_tz_h.get())
    return local.strftime("%d.%m %H:%M")


def _badge(stage: str) -> str:
    return f'<span class="bg {_STC.get(stage, "sd")}">{_h.escape(t(f"stage.{stage}"))}</span>'


def _compact(n: int | None) -> str:
    """Compact follower count: 1234 → 1.2k, 1_200_000 → 1.2M."""
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def _presence(last_active_at: datetime | None) -> str:
    """🟢 online (≤5 min) / ⚫ active Xh ago, or '' when unknown."""
    if last_active_at is None:
        return ""
    secs = (datetime.now(UTC).replace(tzinfo=None) - last_active_at).total_seconds()
    if secs < 300:
        return '<span class="pres on" title="online">🟢 online</span>'
    return f'<span class="pres" title="last active">⚫ {_ago(last_active_at)}</span>'


def _avatar(name: str | None, avatar_url: str | None, size_cls: str = "ti-av") -> str:
    initial = _h.escape(((name or "?")[0]).upper())
    if avatar_url and avatar_url.lower().startswith(("http://", "https://")):
        safe_url = _h.escape(avatar_url)
        return (
            f'<span class="{size_cls}" style="background-image:url(\'{safe_url}\')">'
            f'{initial}</span>'
        )
    return f'<span class="{size_cls}">{initial}</span>'


def _source_bar(
    lead_source: str | None,
    ad_id: str | None,
    ad_media_id: str | None,
    ad_preview_url: str | None,
) -> str:
    is_ad = bool(ad_id or ad_media_id or (lead_source or "").startswith("ad"))
    is_story = (lead_source or "") == "story"
    thumb = ""
    if is_ad and ad_preview_url and ad_preview_url.lower().startswith(("http://", "https://")):
        url = _h.escape(ad_preview_url)
        thumb = (
            f'<a href="{url}" target="_blank" rel="noreferrer">'
            f'<img class="srcthumb" src="{url}" alt="" referrerpolicy="no-referrer"'
            f' loading="lazy" onerror="this.style.display=\'none\'"></a>'
        )
    if is_ad:
        parts = []
        if ad_id:
            safe_id = _h.escape(ad_id)
            parts.append(
                f'<span class="srcid" title="Copy ad ID"'
                f' onclick="navigator.clipboard&&navigator.clipboard.writeText(\'{safe_id}\')">'
                f'{safe_id}</span>'
            )
        post_url = ig_post_url(ad_media_id)
        if post_url:
            ig_post = _h.escape(post_url)
            parts.append(
                f'<a class="srcid" href="{ig_post}" target="_blank" rel="noreferrer">📷 IG ↗</a>'
            )
        extra = (" · " + " · ".join(parts)) if parts else ""
        lbl = f'<span class="srclbl src-paid">📣 Ad{extra}</span>'
    elif is_story:
        lbl = '<span class="srclbl src-story">📖 Story</span>'
    else:
        lbl = '<span class="srclbl src-direct">💬 Direct</span>'
    return f'<div class="srcbar">{thumb}{lbl}</div>'


def _thread_item(row: object, active_tid: int | None, show_branch: bool = False) -> str:
    (tid, name, stage, last_act, phone, product_slug,
     ig_username, avatar_url, follower_count, following_count, agent_enabled,
     last_msg, last_dir, cnt_in, cnt_out, branch_name, tz_offset_h) = row  # type: ignore[misc]
    dt = _as_dt(last_act)
    if dt is not None:
        dt += timedelta(hours=int(tz_offset_h or 0))
    on = " on" if tid == active_tid else ""
    prod_badge = (
        f' <span class="bg sq" style="font-size:.57rem;text-transform:none">'
        f'{_h.escape(str(product_slug))}</span>'
        if product_slug else ""
    )
    handle_row = (
        f'<div class="ti-handle">@{_h.escape(str(ig_username))}</div>'
        if ig_username else ""
    )
    sub_parts = []
    if phone:
        sub_parts.append(f'<span>{_h.escape(str(phone))}</span>')
    total = (cnt_in or 0) + (cnt_out or 0)
    if total:
        sub_parts.append(f'<span class="ti-cnt">💬 {cnt_in or 0}/{cnt_out or 0}</span>')
    if follower_count is not None or following_count is not None:
        sub_parts.append(
            f'<span class="ti-fl">👥 {_compact(follower_count)}·{_compact(following_count)}</span>'
        )
    sub_row = f'<div class="ti-sub">{"  ·  ".join(sub_parts)}</div>' if sub_parts else ""
    br_badge = (
        f'<span class="ti-br" title="Branch">🏢 {_h.escape(str(branch_name))}</span>'
        if show_branch and branch_name else ""
    )
    bot_off = (
        f'<span class="ti-off" title="{_h.escape(t("chat.bot_off_hint"))}">🤖⛔</span>'
        if not agent_enabled else ""
    )
    search_idx = _h.escape(f"{name or ''} {ig_username or ''}".lower())
    return (
        f'<a class="ti{on}" data-search="{search_idx}"'
        f' hx-get="/ui/chat/{tid}" hx-target="#main" hx-push-url="true"'
        f' onclick="setOn(this);setOpenThread({tid})"'
        f' href="/ui/inbox">'
        f'{_avatar(str(name or "?"), avatar_url)}'
        f'<div class="ti-body">'
        f'<div class="ti-t"><span class="ti-n">{_h.escape(str(name or "Lead"))}</span>'
        f'{bot_off}{br_badge}'
        f'<span class="ti-ts">{_fmt_dt_short(dt)}</span></div>'
        f'<div class="ti-p">{_badge(str(stage or "new"))}{prod_badge}</div>'
        f'{handle_row}'
        f'{sub_row}</div></a>'
    )


def thread_list_html(
    threads: list, active_tid: int | None = None, show_branch: bool = False
) -> str:
    if not threads:
        return f'<div class="emp">{_h.escape(t("inbox.empty"))}</div>'
    return "".join(_thread_item(r, active_tid, show_branch) for r in threads)


_LINK_RE = re.compile(r"(https?://[^\s<]+)")
_MEDIA_PH = {"🖼 media", "🎤 voice", "GIF", "🖼 медиа", "🎤 голосовое"}


def _linkify(text: str) -> str:
    """Escape text, then turn bare URLs into clickable links."""
    esc = _h.escape(str(text or ""))
    return _LINK_RE.sub(
        lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noreferrer">{m.group(1)}</a>',
        esc,
    )


def _media_html(media_id: int, media_kind: str | None) -> str:
    src = f"/ui/media/{media_id}"
    if media_kind == "video":
        return f'<video class="msg-prev" src="{src}" controls preload="metadata"></video>'
    if media_kind == "audio":
        return f'<audio src="{src}" controls preload="none" style="max-width:220px"></audio>'
    return (
        f'<a href="{src}" target="_blank" rel="noreferrer">'
        f'<img class="msg-prev" src="{src}" loading="lazy" alt=""></a>'
    )


def _link_preview_html(link_url: str | None, preview_url: str | None) -> str:
    """Preview thumbnail for a shared post/link (fbcdn URL degrades gracefully)."""
    if not (preview_url and preview_url.lower().startswith(("http://", "https://"))):
        return ""
    href = _h.escape(link_url or preview_url)
    src = _h.escape(preview_url)
    return (
        f'<a href="{href}" target="_blank" rel="noreferrer">'
        f'<img class="msg-prev" src="{src}" referrerpolicy="no-referrer" loading="lazy"'
        f' alt="" onerror="this.closest(\'a\').remove()"></a>'
    )


def _receipt(occurred_at: datetime | None, lead_seen_at: datetime | None) -> str:
    """✓✓ if the lead has read up to this out-message, ✓ if merely sent."""
    if lead_seen_at is not None and occurred_at is not None and lead_seen_at >= occurred_at:
        return ' <span class="rcpt seen" title="Seen">✓✓</span>'
    return ' <span class="rcpt" title="Sent">✓</span>'


def _bubble(row: object, tid: int, lead_seen_at: datetime | None = None) -> str:
    mid, direction, sent_by, text, ts, llm_info, link_url, preview_url, media_id, media_kind = \
        row[:10]  # type: ignore[misc]
    excluded = bool(row[10]) if len(row) > 10 else False  # greyed, out of Stepan's context
    ex = " bb-ex" if excluded else ""
    who_key = f"who.{sent_by}" if sent_by in ("agent", "manager", "lead") else ""
    who = _h.escape(t(who_key) if who_key else str(sent_by or ""))
    time_str = _fmt_time(ts)
    caption = "" if (media_id and str(text or "").strip() in _MEDIA_PH) else _linkify(text)
    att = ""
    if media_id:
        att += _media_html(int(media_id), media_kind)
    att += _link_preview_html(link_url, preview_url)
    body = (
        f'<div class="bt" id="bt-{mid}">{caption}</div>' if caption else ""
    ) + att

    tr_btn = (
        f'<button class="trx" title="Translate" tabindex="-1"'
        f' onclick="trMsg({mid},{tid})">🌐</button>'
        if caption else ""  # nothing to translate on a media-only / no-caption bubble
    )
    if direction == "in":
        return (
            f'<div class="bb bb-i{ex}" id="bb-{mid}">'
            f'<div class="bm">{who} · {time_str} {tr_btn}</div>'
            f'{body}</div>'
        )
    mgr = " mgr" if sent_by == "manager" else ""
    del_btn = (
        f'<button class="delx" title="Delete" tabindex="-1"'
        f' onclick="delAsk(this,{tid},{mid})">×</button>'
    )
    llm_chip = (
        f'<div class="b-llm">🤖 {_h.escape(str(llm_info))}</div>'
        if llm_info else ""
    )
    return (
        f'<div class="bb bb-o{mgr}{ex}" id="bb-{mid}">'
        f'<div class="bm">{who} · {time_str}{_receipt(ts, lead_seen_at)} {tr_btn} {del_btn}</div>'
        f'{body}{llm_chip}</div>'
    )


def _last_msg_id(msgs: list) -> int:
    """Highest message id shown — the poll cursor. MUST be max(id), not the last row by
    occurred_at: a late-arriving message can carry a higher id but an earlier timestamp,
    and a last-by-time cursor would re-fetch already-shown rows and reorder the view."""
    return max((int(m[0]) for m in msgs), default=0)


_LOG_KIND_KEY = {"context_cleared": "chat.cleared", "context_loaded": "chat.loaded",
                 "product_changed": "chat.product"}


def _event_bubble(row: object) -> str:
    """A technical/system log line (stage change, context clear/load) — centered, muted,
    never mistaken for something a person said."""
    _id, src, kind, detail, actor, ts = row  # type: ignore[misc]
    if src == "stage":
        label = t(
            "log.stage_change",
            **{"from": t(f"stage.{detail}"), "to": t(f"stage.{kind}")},
        )
    else:
        label = t(_LOG_KIND_KEY.get(str(kind), str(kind)))
        if detail:  # e.g. product_changed carries "old→new"
            label = f"{label}: {detail}"
    who = _h.escape(t(f"who.{actor}") if actor in ("agent", "manager", "lead") else str(actor))
    return (
        f'<div class="sys-log">— {_h.escape(label)} · {who} · {_fmt_time(_as_dt(ts))} —</div>'
    )


def _merge_feed(msgs: list, events: list, tid: int, lead_seen_at: datetime | None) -> str:
    """Message bubbles + system-log lines, interleaved in timestamp order."""
    items = [(_as_dt(m[4]) or datetime.min, 0, _bubble(m, tid, lead_seen_at)) for m in msgs]
    items += [(_as_dt(e[5]) or datetime.min, 1, _event_bubble(e)) for e in events]
    items.sort(key=lambda x: (x[0], x[1]))
    return "".join(html for *_r, html in items)


def _last_event_ids(events: list) -> tuple[int, int]:
    """(max stage_event id seen, max thread_log id seen) — the two extra poll cursors."""
    stage_max = max((int(e[0]) for e in events if e[1] == "stage"), default=0)
    log_max = max((int(e[0]) for e in events if e[1] == "log"), default=0)
    return stage_max, log_max


def poll_sentinel_html(
    tid: int, after_id: int, after_stage_id: int = 0, after_log_id: int = 0,
) -> str:
    """Self-replacing 4s poller: fetches bubbles/events newer than the three cursors and
    reinserts itself. Three cursors, not one — messages, stage_event and thread_log each
    have their own independent autoincrement id, so a single counter can't track all three."""
    return (
        f'<div id="poll-{tid}"'
        f' hx-get="/ui/chat/{tid}/since/{after_id}/{after_stage_id}/{after_log_id}"'
        f' hx-trigger="every 4s" hx-swap="outerHTML" hx-sync="this:replace"></div>'
    )


def _pending_bubble(row: object, tid: int, idx: int) -> str:
    oid, ptxt, sched, llm_info, tr_text = row  # (outbox id, text, scheduled_at, llm_info, tr)
    when = _fmt_time(_as_dt(sched))  # branch-local HH:MM:SS (tolerates str or datetime)
    meta = f'⏳ {_h.escape(t("chat.pending"))} · №{idx + 1}' + (f' · ~{when}' if when else "")
    tr_btn = (
        f'<button class="trx" title="Translate" tabindex="-1"'
        f' hx-post="/ui/chat/{tid}/pending/{oid}/tr"'
        f' hx-target="#ptr-{oid}" hx-swap="innerHTML">🌐</button>'
    )
    del_btn = (
        f'<button class="delx" title="Cancel send" tabindex="-1"'
        f' hx-post="/ui/chat/{tid}/pending/{oid}/delete"'
        f' hx-target="#ppb-{oid}" hx-swap="outerHTML" hx-confirm="">×</button>'
    )
    tr_line = f'🌐 {_h.escape(tr_text)}' if tr_text else ""
    chip = f'<div class="b-llm">🤖 {_h.escape(str(llm_info))}</div>' if llm_info else ""
    return (
        f'<div class="bb bb-o bb-p" id="ppb-{oid}">'
        f'<div class="bm">{meta} {tr_btn} {del_btn}</div>'
        f'<div class="bt">{_h.escape(str(ptxt or ""))}</div>'
        f'<div class="b-tr" id="ptr-{oid}">{tr_line}</div>'
        f'{chip}</div>'
    )


def pending_block_html(pending: list, tid: int, oob: bool = False) -> str:
    """Queued (unsent) replies, pinned at the bottom, styled like outgoing (right side).
    Re-rendered via an OOB swap on each poll so a just-sent line drops out and the queue
    №/time stay fresh — and new real messages (inserted at the sentinel ABOVE this block)
    never shove pending bubbles up. Manager can translate or cancel a queued line."""
    oob_attr = ' hx-swap-oob="true"' if oob else ""
    inner = "".join(_pending_bubble(r, tid, i) for i, r in enumerate(pending))
    return f'<div id="pend-{tid}"{oob_attr}>{inner}</div>'


def since_bubbles_html(
    msgs: list, tid: int, after_id: int, lead_seen_at: datetime | None = None,
    pending: list | None = None, events: list | None = None,
    after_stage_id: int = 0, after_log_id: int = 0,
) -> str:
    """New bubbles/events + fresh sentinel, plus an OOB refresh of the pending block."""
    events = events or []
    feed = _merge_feed(msgs, events, tid, lead_seen_at)
    stage_max, log_max = _last_event_ids(events)
    out = feed + poll_sentinel_html(
        tid, _last_msg_id(msgs) or after_id,
        max(stage_max, after_stage_id), max(log_max, after_log_id),
    )
    if pending is not None:
        out += pending_block_html(pending, tid, oob=True)
    return out


def messages_html(
    msgs: list, pending: list, tid: int, lead_seen_at: datetime | None = None,
    events: list | None = None,
) -> str:
    events = events or []
    stage_max, log_max = _last_event_ids(events)
    parts = [_merge_feed(msgs, events, tid, lead_seen_at)]
    parts.append(poll_sentinel_html(tid, _last_msg_id(msgs), stage_max, log_max))
    parts.append(pending_block_html(pending, tid))  # queued replies pinned at the bottom
    return "".join(parts)


_STAGES = (
    "new", "nurturing", "qualifying", "presenting", "objection",
    "ready", "handed_off", "dormant", "manager",
)


def chat_bot_pill_html(tid: int, enabled: bool) -> str:
    """Per-lead bot ON/OFF pill shown in the chat header (hx-swap=outerHTML)."""
    lbl = _h.escape(t("bot.on" if enabled else "bot.off"))
    color = "#51cf66" if enabled else "#ff6b6b"
    bg = "rgba(31,58,31,.35)" if enabled else "rgba(58,31,31,.35)"
    return (
        f'<form id="bot-pill-{tid}" style="display:inline;margin:0"'
        f' data-help="{_h.escape(t("hint.bot_chat"))}"'
        f' hx-post="/ui/chat/{tid}/bot-toggle"'
        f' hx-target="#bot-pill-{tid}" hx-swap="outerHTML">'
        f'<button type="submit" class="act-btn"'
        f' style="background:{bg};border-color:{color};color:{color}"'
        f' title="{lbl}">🤖 {lbl}</button>'
        f'</form>'
    )


def chat_block_pill_html(tid: int, blocked: bool) -> str:
    """Per-lead block toggle (spam). Blocked → bot ignores the lead entirely."""
    if blocked:
        style = "background:rgba(58,31,31,.5);border-color:#ff6b6b;color:#ff6b6b"
        body = f'🚫 {_h.escape(t("chat.blocked"))}'
    else:
        style = ""
        body = "🚫"
    return (
        f'<form id="blk-{tid}" style="display:inline;margin:0"'
        f' data-help="{_h.escape(t("hint.block"))}"'
        f' hx-post="/ui/chat/{tid}/block" hx-target="#blk-{tid}" hx-swap="outerHTML">'
        f'<button type="submit" class="act-btn" style="{style}"'
        f' title="{_h.escape(t("chat.block"))}">{body}</button>'
        f'</form>'
    )


def _clear_ctx_btn(tid: int) -> str:
    """Clear (grey out, drop from Stepan's context) + Load (bring the full context back).
    Both re-render the message feed so the greyed state updates without a reload."""
    clear = (
        f'<button class="act-btn" hx-post="/ui/chat/{tid}/clear"'
        f' data-help="{_h.escape(t("hint.clear_ctx"))}"'
        f' hx-target="#msgs-{tid}" hx-swap="innerHTML"'
        f' hx-confirm="{_h.escape(t("chat.clear_confirm"))}"'
        f' title="{_h.escape(t("chat.clear"))}">🧹</button>'
    )
    load = (
        f'<button class="act-btn" hx-post="/ui/chat/{tid}/load-context"'
        f' data-help="{_h.escape(t("hint.load_ctx"))}"'
        f' hx-target="#msgs-{tid}" hx-swap="innerHTML"'
        f' title="{_h.escape(t("chat.load_ctx"))}">📥</button>'
    )
    return clear + load


def chat_header_html(
    tid: int,
    name: str,
    stage: str,
    product_slug: str | None = None,
    ig_id: str | None = None,
    phone: str | None = None,
    created_at: datetime | None = None,
    last_in_at: datetime | None = None,
    ig_username: str | None = None,
    avatar_url: str | None = None,
    lead_source: str | None = None,
    ad_id: str | None = None,
    ad_media_id: str | None = None,
    ad_preview_url: str | None = None,
    agent_enabled: bool = True,
    is_blocked: bool = False,
    follower_count: int | None = None,
    following_count: int | None = None,
    last_active_at: datetime | None = None,
    needs: NeedsProfile | None = None,
    products: list | None = None,
) -> str:
    """Renders chat header + source bar (for hx-swap=outerHTML on stage change)."""
    opts = "".join(
        f'<option value="{s}" {"selected" if s == stage else ""}>'
        f'{_h.escape(t(f"stage.{s}"))}</option>'
        for s in _STAGES
    )
    stage_sel = (
        f'<form style="display:inline;margin:0"'
        f' data-help="{_h.escape(t("hint.stage"))}"'
        f' hx-post="/ui/chat/{tid}/stage"'
        f' hx-target="#chat-hdr-{tid}"'
        f' hx-swap="outerHTML">'
        f'<select class="act-sel" name="stage"'
        f' onchange="this.form.requestSubmit()">{opts}</select>'
        f'</form>'
    )
    if products is not None:
        none_lbl = _h.escape(t("product.none"))
        p_opts = f'<option value="">{none_lbl}</option>' + "".join(
            f'<option value="{_h.escape(slug)}" '
            f'{"selected" if slug == product_slug else ""}>{_h.escape(title or slug)}</option>'
            for slug, title in products
        )
        product_badge = (
            f' <form style="display:inline;margin:0" hx-post="/ui/chat/{tid}/product"'
            f' data-help="{_h.escape(t("hint.product"))}"'
            f' hx-target="#chat-hdr-{tid}" hx-swap="outerHTML">'
            f'<select class="act-sel" name="product"'
            f' onchange="this.form.requestSubmit()">{p_opts}</select></form>'
        )
    elif product_slug:
        product_badge = (
            f' <span class="bg sq" style="font-size:.62rem;text-transform:none">'
            f'{_h.escape(product_slug)}</span>'
        )
    else:
        product_badge = ""
    # Avatar with optional IG profile link
    av_html = _avatar(name, avatar_url, size_cls="ch-av")
    if ig_username:
        ig_link = _h.escape(f"https://www.instagram.com/{ig_username}/")
        av_html = f'<a href="{ig_link}" target="_blank" rel="noreferrer">{av_html}</a>'
    # Name with optional @handle and IG link
    name_html = _h.escape(name)
    handle_html = ""
    if ig_username:
        ig_link = _h.escape(f"https://www.instagram.com/{ig_username}/")
        name_html = (
            f'<a href="{ig_link}" target="_blank" rel="noreferrer"'
            f' style="color:inherit;text-decoration:none">{name_html}</a>'
        )
        handle_html = (
            f' <span class="ch-sub">@{_h.escape(ig_username)}</span>'
        )
    # Thread ID chip (short)
    ig_chip = ""
    if ig_id and not ig_username:
        short = ig_id[:14] + "…" if len(ig_id) > 16 else ig_id
        ig_chip = f' <span class="ch-sub" title="{_h.escape(ig_id)}">{_h.escape(short)}</span>'
    meta_parts = []
    if follower_count is not None or following_count is not None:
        meta_parts.append(
            f'<span title="followers · following">👥 {_compact(follower_count)}'
            f' · {_compact(following_count)}</span>'
        )
    presence = _presence(last_active_at)
    if presence:
        meta_parts.append(presence)
    if phone:
        meta_parts.append(f'<a href="tel:{_h.escape(phone)}">📞 {_h.escape(phone)}</a>')
    created_dt = _as_dt(created_at)  # raw text() SQL returns a str on sqlite, datetime on pg
    if created_dt:
        meta_parts.append(f'<span>📅 с {created_dt.strftime("%d %b %Y")}</span>')
    if last_in_at:
        meta_parts.append(f'<span>⬇ {_fmt_time(last_in_at)}</span>')
    meta_row = (
        f'<div class="ch-meta">{"  ·  ".join(meta_parts)}</div>'
        if meta_parts else ""
    )
    src_bar = _source_bar(lead_source, ad_id, ad_media_id, ad_preview_url)
    bot_pill = chat_bot_pill_html(tid, agent_enabled)
    block_pill = chat_block_pill_html(tid, is_blocked)
    clear_btn = _clear_ctx_btn(tid)
    return (
        f'<div id="chat-hdr-{tid}">'
        f'<div class="ch">'
        f'{av_html}'
        f'<span class="ch-n">{name_html}{handle_html}</span>'
        f'{product_badge}{ig_chip}'
        f'<div class="ch-acts">{bot_pill}{block_pill}{clear_btn}{stage_sel}</div>'
        f'{meta_row}'
        f'</div>'
        f'{src_bar}'
        f'{_needs_block(needs)}'
        f'</div>'
    )


def _needs_block(needs: NeedsProfile | None) -> str:
    """Render the captured Value-Proposition-Canvas profile (jobs/pains/gains) so the
    manager sees what Stepan discovered — pre-translated by the caller into the current
    UI language (see needs_translate.translated_needs). Empty when nothing captured yet."""
    if needs is None:
        return ""
    p = needs
    rows = []
    for icon, label, items, cls in (
        ("🎯", t("needs.jobs"), p.jobs, "nd-job"),
        ("⚠️", t("needs.pains"), p.pains, "nd-pain"),
        ("✨", t("needs.gains"), p.gains, "nd-gain"),
    ):
        if items:
            chips = "".join(f'<span class="nd-chip {cls}">{_h.escape(i)}</span>' for i in items)
            rows.append(f'<div class="nd-row"><span class="nd-lbl">{icon} '
                        f'{_h.escape(label)}</span>{chips}</div>')
    if not rows:
        return ""
    return (
        f'<div class="nd-box" data-help="{_h.escape(t("hint.needs"))}">'
        f'{"".join(rows)}</div>'
    )


def chat_panel_html(
    tid: int,
    name: str,
    stage: str,
    msgs: list,
    pending: list,
    lead_id: int | None = None,  # noqa: ARG001 (reserved for future use)
    product_slug: str | None = None,
    ig_id: str | None = None,
    phone: str | None = None,
    created_at: datetime | None = None,
    last_in_at: datetime | None = None,
    ig_username: str | None = None,
    avatar_url: str | None = None,
    lead_source: str | None = None,
    ad_id: str | None = None,
    ad_media_id: str | None = None,
    ad_preview_url: str | None = None,
    agent_enabled: bool = True,
    is_blocked: bool = False,
    follower_count: int | None = None,
    following_count: int | None = None,
    last_active_at: datetime | None = None,
    lead_seen_at: datetime | None = None,
    needs: NeedsProfile | None = None,
    events: list | None = None,
    products: list | None = None,
) -> str:
    ph = _h.escape(t("chat.ph"))
    send_lbl = _h.escape(t("chat.send"))
    sug_lbl = _h.escape(t("chat.suggest"))
    tr_lbl = _h.escape(t("chat.translate"))
    header = chat_header_html(
        tid, name, stage,
        product_slug=product_slug, ig_id=ig_id,
        phone=phone, created_at=created_at, last_in_at=last_in_at,
        ig_username=ig_username, avatar_url=avatar_url,
        lead_source=lead_source, ad_id=ad_id,
        ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
        agent_enabled=agent_enabled, is_blocked=is_blocked,
        follower_count=follower_count, following_count=following_count,
        last_active_at=last_active_at, needs=needs, products=products,
    )
    return (
        f'{header}'
        f'<div class="msgs" id="msgs-{tid}">'
        f'{messages_html(msgs, pending, tid, lead_seen_at, events)}</div>'
        f'<div id="sug-{tid}"></div>'
        f'<div id="tr-{tid}"></div>'
        f'<div class="fin">'
        f'<div class="fin-tools">'
        f'<button class="act-btn"'
        f' data-help="{_h.escape(t("hint.suggest"))}"'
        f' hx-post="/ui/chat/{tid}/suggest"'
        f' hx-target="#sug-{tid}" hx-swap="innerHTML">{sug_lbl}</button>'
        f'<button class="act-btn" data-help="{_h.escape(t("hint.summary"))}"'
        f' onclick="trChat({tid})">{tr_lbl}</button>'
        f'{_emoji_bar(f"cmp-{tid}")}'
        f'</div>'
        f'<form class="fin-row"'
        f' hx-post="/ui/chat/{tid}/send"'
        f' hx-target="#msgs-{tid}"'
        f' hx-swap="innerHTML"'
        f" hx-on::after-request='this.reset();scrollMsgs({tid});resetGrow(\"cmp-{tid}\")'>"
        f'<textarea id="cmp-{tid}" name="text" rows="1" placeholder="{ph}"'
        f' data-help="{_h.escape(t("hint.composer"))}"'
        f' oninput="autoGrow(this)" onkeydown="entSend(event)"></textarea>'
        f'<button class="bsn">{send_lbl}</button></form>'
        f'</div>'
    )


_EMOJI = ("😊", "🙏", "👍", "🔥", "✅", "🎉", "😅", "🚀", "💡", "❤️", "😉", "🤝")


def _emoji_bar(target_id: str) -> str:
    """Emoji picker that inserts into the textarea `target_id` at the cursor."""
    btns = "".join(
        f'<button type="button" tabindex="-1" '
        f"onclick=\"insEmo(document.getElementById('{target_id}'),'{e}')\">{e}</button>"
        for e in _EMOJI
    )
    return f'<div class="emo-bar">{btns}</div>'


def suggest_box_html(tid: int, draft: str) -> str:
    """HTML for the suggest box that appears below messages after clicking Suggest."""
    send_lbl = _h.escape(t("chat.send_stepan"))
    discard_lbl = _h.escape(t("chat.discard"))
    tr_lbl = _h.escape(t("chat.translate"))
    ph = _h.escape(t("chat.suggest_ph"))
    return (
        f'<div class="sug-box">'
        f'<textarea class="sug-ta" id="sug-ta-{tid}"'
        f' placeholder="{ph}" onkeydown="entSend(event)">{_h.escape(draft)}</textarea>'
        f'{_emoji_bar(f"sug-ta-{tid}")}'
        f'<div class="b-tr" id="sug-tr-{tid}"></div>'
        f'<div class="sug-acts">'
        f'<button class="act-btn primary"'
        f' onclick="sendSuggest({tid})">{send_lbl}</button>'
        f'<button class="act-btn" onclick="trDraft({tid})">🌐 {tr_lbl}</button>'
        f'<button class="act-btn"'
        f' onclick="document.getElementById(\'sug-{tid}\').innerHTML=\'\'">'
        f'{discard_lbl}</button>'
        f'</div></div>'
    )


_FAVICON = (
    "<link rel='icon' href=\"data:image/svg+xml,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='0' y2='1'>"
    "<stop offset='0' stop-color='%234da6ff'/>"
    "<stop offset='1' stop-color='%231d63b8'/></linearGradient></defs>"
    "<rect x='2' y='3' width='28' height='21' rx='5.5' fill='url(%23g)'/>"
    "<path d='M9 22 l0 6 l7 -5 z' fill='%231d63b8'/>"
    "<text x='16' y='19.5' font-family='Arial,Helvetica,sans-serif' font-size='15'"
    " font-weight='800' fill='%23fff' text-anchor='middle'>S</text></svg>\">"
)


def app_shell(
    lang: str, main_html: str, active_nav: str = "inbox", thr_html: str | None = None,
    stage: str = "",
) -> str:
    def _na(key: str, href: str, icon: str, nav_id: str, extra: str = "", badge: str = "") -> str:
        cls = "na on" if nav_id == active_nav else "na"
        lbl = _h.escape(t(key))
        hk = _HELP_KEYS.get(nav_id)  # section description doubles as its help-mode tip
        tip = f' data-help="{_h.escape(t(hk))}"' if hk else ""
        return (
            f'<a class="{cls}" href="{href}"{tip}{extra}>'
            f'<i class="{icon}"></i>'
            f'<span class="na-lbl">{lbl}</span>{badge}</a>'
        )

    def _hna(key: str, panel: str, icon: str, nav_id: str, badge: str = "") -> str:
        extra = (
            f' hx-get="{panel}" hx-target="#main" hx-push-url="{panel}"'
            f' onclick="setOn(this,\'na\');showThr(false)"'
        )
        return _na(key, panel, icon, nav_id, extra, badge)

    outbox_badge = (
        '<span class="na-badge" id="outbox-badge" hx-get="/ui/outbox/count"'
        ' hx-trigger="load, every 15s" hx-swap="innerHTML" hx-target="this"'
        ' hx-push-url="false"></span>'  # poll must not rewrite the address bar
    )

    coach_extra = (
        ' hx-get="/ui/coach/panel" hx-target="#main" hx-push-url="/ui/coach"'
        " onclick=\"setOn(this,'na');showThr(false)\""
    )
    nav = (
        _na("nav.inbox", "/ui/inbox", "fa-solid fa-inbox", "inbox")
        + _hna("nav.outbox", "/ui/outbox/panel", "fa-solid fa-paper-plane", "outbox", outbox_badge)
        + _na("nav.coach", "#", "fa-solid fa-pencil", "coach", coach_extra)
        + _na("nav.know", "/ui/knowledge", "fa-solid fa-book", "know")
        + _hna("nav.products", "/ui/products/panel", "fa-solid fa-box", "products")
        + _hna("nav.reports", "/ui/reports/panel", "fa-solid fa-chart-bar", "reports")
        + _hna("nav.leads", "/ui/leads/panel", "fa-solid fa-user-tag", "leads")
        + '<div class="nav-sep"></div>'
        + _hna("nav.members", "/ui/members/panel", "fa-solid fa-users", "members")
        + _hna("nav.settings", "/ui/settings/panel", "fa-solid fa-gear", "settings")
        + '<div class="nav-sep"></div>'
        + _hna("nav.branches", "/ui/branches/panel", "fa-solid fa-building", "branches")
        + _hna("nav.log", "/ui/settings/log", "fa-solid fa-list", "log")
    )

    def _lb(code: str) -> str:
        cls = "lb on" if code == lang else "lb"
        return f'<a class="{cls}" href="/ui/lang/{code}">{code.upper()}</a>'

    script = (
        "function setOn(el,cls){"
        "cls=cls||'ti';"
        "document.querySelectorAll('.'+cls+'.on').forEach(e=>e.classList.remove('on'));"
        "el.classList.add('on');}"
        "function scrollMsgs(tid){"
        "var m=document.getElementById('msgs-'+tid);if(m)m.scrollTop=m.scrollHeight;}"
        "function setOpenThread(tid){"
        "document.cookie='stepan2_open_thread='+tid+';path=/;max-age=86400;samesite=lax';"
        # mobile: opening a chat slides the #main overlay in over the thread list
        "document.body.classList.add('chat-open');document.body.classList.remove('nav-open');}"
        "function backToList(){document.body.classList.remove('chat-open');}"
        "function toggleNav(){document.body.classList.toggle('nav-open');}"
        "function filterTi(){var i=document.getElementById('ti-q');var q=i?i.value:'';"
        "q=q.toLowerCase().trim();document.querySelectorAll('#tl .ti').forEach(function(e){"
        "var s=e.getAttribute('data-search')||'';"
        "e.style.display=(!q||s.indexOf(q)>=0)?'':'none';});}"
        "function scrollBot(m){if(m)m.scrollTop=m.scrollHeight;}"
        "function smartScroll(m){if(!m)return;"
        "var near=m.scrollHeight-m.scrollTop-m.clientHeight<150;"
        "if(near)m.scrollTop=m.scrollHeight;}"
        "function pinBot(m){scrollBot(m);m.querySelectorAll('img').forEach(function(g){"
        "if(!g.complete)g.addEventListener('load',function(){scrollBot(m);},{once:true});});}"
        "document.addEventListener('htmx:afterSettle',function(e){"
        "var t=e.target;"
        # a chat panel/msgs container FRESHLY swapped in (opening a chat) -> always jump to
        # the end; a poll bubble inserted INSIDE an existing .msgs -> smart-scroll only so we
        # don't yank a manager who scrolled up to read history.
        "var fresh=(t&&t.classList&&t.classList.contains('msgs'))?t"
        ":(t&&t.querySelector?t.querySelector('.msgs'):null);"
        "if(fresh){pinBot(fresh);}"
        # a background poll inserting a new bubble must NOT auto-scroll — the manager may be
        # reading history mid-chat; only opening a chat (fresh panel above) jumps to the end.
        # any content swapped into #main (panel or chat) slides the overlay in on mobile
        "if(t&&t.id==='main'&&window.innerWidth<=760){document.body.classList.add('chat-open');document.body.classList.remove('nav-open');}"
        "if(t&&t.id==='tl')filterTi();});"
        # F5 / direct load: afterSettle never fires, so pin every .msgs to the bottom on load
        "function scrollAllBot(){document.querySelectorAll('.msgs').forEach(function(m){"
        "scrollBot(m);m.querySelectorAll('img').forEach(function(g){"
        "if(!g.complete)g.addEventListener('load',function(){scrollBot(m);},{once:true});});});}"
        # direct load / F5 of a chat or a panel (middleware-wrapped): afterSettle never
        # fires, so reveal #main on mobile when it holds real content (not the empty
        # inbox/kb placeholder). Fixes /ui/settings/panel etc. being blank on a phone.
        "window.addEventListener('load',function(){scrollAllBot();"
        "if(window.innerWidth<=760){var m=document.getElementById('main');"
        "if(m&&!m.querySelector('.emp'))document.body.classList.add('chat-open');}});"
        "function showThr(v){if(window.innerWidth<=760)return;"
        "var el=document.querySelector('.thr');"
        "if(el)el.style.display=v?'':'none';}"
        # help mode: ? toggles it; ONE delegated hover handler on the document (survives
        # every htmx re-render); the tip is measured after being filled, then flips below
        # the element when there is no room above and clamps to the viewport width.
        "function toggleHelp(){document.body.classList.toggle('help-mode');"
        "var tp=document.getElementById('help-tip');if(tp)tp.style.display='none';}"
        "document.addEventListener('mouseover',function(e){"
        "if(!document.body.classList.contains('help-mode'))return;"
        "var tp=document.getElementById('help-tip');if(!tp)return;"
        "var el=e.target.closest?e.target.closest('[data-help]'):null;"
        "if(!el){tp.style.display='none';return;}"
        "tp.textContent=el.getAttribute('data-help');"
        "tp.style.display='block';tp.style.top='0px';tp.style.left='0px';"
        "var r=el.getBoundingClientRect();"
        "var top=r.top-tp.offsetHeight-8;if(top<4)top=r.bottom+8;"
        "var left=Math.max(4,Math.min(r.left,window.innerWidth-tp.offsetWidth-4));"
        "tp.style.top=top+'px';tp.style.left=left+'px';});"
        "function setFnl(el){"
        "document.querySelectorAll('.fseg.on,.fchip.on,.fnl-all.on')"
        ".forEach(e=>e.classList.remove('on'));"
        "el.classList.add('on');}"
        "function sendSuggest(tid){"
        "var ta=document.getElementById('sug-ta-'+tid);"
        "if(!ta||!ta.value.trim())return;"
        "var fd=new FormData();fd.append('text',ta.value);fd.append('source','agent');"
        "htmx.ajax('POST','/ui/chat/'+tid+'/send',{"
        "target:'#msgs-'+tid,swap:'innerHTML',values:fd});"
        "document.getElementById('sug-'+tid).innerHTML='';}"
        # per-message translate toggle with LLM fetch + client-side cache
        "function trMsg(mid,tid){"
        "var el=document.getElementById('bt-'+mid);"
        "if(!el)return;"
        "if(el.dataset.state==='tr'){"
        "el.innerHTML=el.dataset.orig;el.dataset.state='';"
        "el.classList.remove('trview');return;}"
        "if(el.dataset.tr){"
        "el.dataset.orig=el.innerHTML;"
        "el.innerHTML=el.dataset.tr;el.dataset.state='tr';"
        "el.classList.add('trview');return;}"
        "el.style.opacity='.45';"
        "el.dataset.orig=el.innerHTML;"
        "fetch('/ui/chat/'+tid+'/msg/'+mid+'/tr',{headers:{'HX-Request':'true'}})"
        ".then(function(r){return r.text();})"
        ".then(function(html){"
        "el.style.opacity='';"
        "if(html.trim()){"
        "el.dataset.tr=html;el.innerHTML=html;"
        "el.dataset.state='tr';el.classList.add('trview');}})"
        ".catch(function(){el.style.opacity='';});}"
        # delete: inline micro-confirm popup right at the × (no browser dialog)
        "function delAsk(btn,tid,mid){"
        "if(btn.dataset.armed)return;btn.dataset.armed=1;btn.style.display='none';"
        "var p=document.createElement('span');p.className='delx-pop';"
        "p.innerHTML='<button class=delx-y>\\u2713</button><button class=delx-n>\\u2717</button>';"
        "btn.after(p);"
        "p.querySelector('.delx-y').onclick=function(){htmx.ajax('POST','/ui/chat/'+tid+"
        "'/msg/'+mid+'/delete',{target:'#bb-'+mid,swap:'outerHTML'});};"
        "p.querySelector('.delx-n').onclick=function(){p.remove();btn.style.display='';"
        "delete btn.dataset.armed;};}"
        # Enter sends, Shift+Enter = newline
        "function entSend(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();"
        "var f=e.target.closest('form');if(f)f.requestSubmit();}}"
        # message textarea grows with content up to CSS max-height, then scrolls
        "function autoGrow(ta){ta.style.height='auto';ta.style.height=ta.scrollHeight+'px';}"
        "function resetGrow(id){var ta=document.getElementById(id);if(ta)ta.style.height='';}"
        # insert an emoji at the cursor in a chat composer / suggest textarea
        "function insEmo(ta,em){if(!ta)return;var s=ta.selectionStart;"
        "if(s==null)s=ta.value.length;var e=ta.selectionEnd!=null?ta.selectionEnd:s;"
        "ta.value=ta.value.slice(0,s)+em+ta.value.slice(e);ta.focus();"
        "ta.selectionStart=ta.selectionEnd=s+em.length;}"
        # chat-summary translate: toggle the popup — a second press hides it
        "function trChat(tid){var out=document.getElementById('tr-'+tid);if(!out)return;"
        "if(out.innerHTML.trim()){out.innerHTML='';return;}"
        "out.innerHTML='<div style=\"padding:.3rem .75rem;font-size:.75rem;"
        "color:#5a6472\">…</div>';"
        "fetch('/ui/chat/'+tid+'/translate',{method:'POST',headers:{'HX-Request':'true'}})"
        ".then(function(r){return r.text();}).then(function(h){out.innerHTML=h;})"
        ".catch(function(){out.innerHTML='';});}"
        "function trClose(tid){var o=document.getElementById('tr-'+tid);if(o)o.innerHTML='';}"
        # translate the Suggest draft (shows the manager what Stepan's reply says)
        "function trDraft(tid){var ta=document.getElementById('sug-ta-'+tid);"
        "var out=document.getElementById('sug-tr-'+tid);if(!ta||!ta.value.trim())return;"
        "out.textContent='...';var fd=new FormData();fd.append('text',ta.value);"
        "fetch('/ui/chat/'+tid+'/tr-draft',{method:'POST',headers:{'HX-Request':'true'},"
        "body:fd}).then(function(r){return r.text();}).then(function(h){out.innerHTML=h;})"
        ".catch(function(){out.textContent='';});}"
        # No pull-to-refresh emulation. The app-shell layout (html/body overflow:hidden,
        # inner scroll containers — same as any web chat) structurally disables the native
        # gesture in every mobile engine, and the hand-rolled touch handler proved flaky
        # across engines/regions (removed by owner request). Content live-updates via its
        # own poll cycle; a deliberate reload is the browser's reload button.
        # resize + collapse init (runs once after DOM ready)
        "(function(){"
        "var sb=document.querySelector('.sid');"
        "var sbcol=document.getElementById('sb-col');"
        "if(sb&&localStorage.getItem('sbCollapsed')==='1'){"
        "sb.classList.add('collapsed');"
        "if(sbcol)sbcol.textContent='▸';}"
        "if(sb&&sbcol){sbcol.addEventListener('click',function(){"
        "var c=sb.classList.contains('collapsed');"
        "if(c){sb.classList.remove('collapsed');"
        "sb.style.width=(localStorage.getItem('sbw')||'210')+'px';"
        "localStorage.setItem('sbCollapsed','0');sbcol.textContent='◂';}"
        "else{sb.classList.add('collapsed');"
        "localStorage.setItem('sbCollapsed','1');sbcol.textContent='▸';}});}"
        "var sbrz=document.getElementById('sbrz');"
        "if(sb&&sbrz){"
        "var sw=parseInt(localStorage.getItem('sbw')||'');"
        "if(sw>=140&&sw<=400)sb.style.width=sw+'px';"
        "sbrz.addEventListener('pointerdown',function(e){"
        "e.preventDefault();sbrz.setPointerCapture(e.pointerId);sbrz.classList.add('drag');"
        "function mv(ev){"
        "var x=Math.min(400,Math.max(140,ev.clientX));sb.style.width=x+'px';"
        "sb.classList.remove('collapsed');"
        "if(sbcol)sbcol.textContent='◂';"
        "localStorage.setItem('sbCollapsed','0');}"
        "function up(){sbrz.removeEventListener('pointermove',mv);"
        "sbrz.removeEventListener('pointerup',up);sbrz.classList.remove('drag');"
        "localStorage.setItem('sbw',parseInt(sb.style.width)||210);}"
        "sbrz.addEventListener('pointermove',mv);sbrz.addEventListener('pointerup',up);});}"
        "var thr=document.querySelector('.thr');"
        "var thrz=document.getElementById('thrz');"
        "if(thr&&thrz){"
        "var tw=parseInt(localStorage.getItem('thrw')||'');"
        "if(tw>=200&&tw<=600)thr.style.width=tw+'px';"
        "thrz.addEventListener('pointerdown',function(e){"
        "e.preventDefault();thrz.setPointerCapture(e.pointerId);thrz.classList.add('drag');"
        "var sx=e.clientX;var sw2=thr.offsetWidth;"
        "function mv2(ev){"
        "var x=Math.min(600,Math.max(200,sw2+(ev.clientX-sx)));thr.style.width=x+'px';}"
        "function up2(){thrz.removeEventListener('pointermove',mv2);"
        "thrz.removeEventListener('pointerup',up2);thrz.classList.remove('drag');"
        "localStorage.setItem('thrw',parseInt(thr.style.width)||305);}"
        "thrz.addEventListener('pointermove',mv2);thrz.addEventListener('pointerup',up2);});}"
        "})();"
    )
    inbox_lbl = _h.escape(t("nav.inbox"))
    help_lbl = _h.escape(t("help.title"))
    # .thr column: shown for inbox (thread list) or when caller passes custom thr_html
    if thr_html is not None:
        _show_thr = True
        _thr_inner = thr_html
    elif active_nav == "inbox":
        _show_thr = True
        _qs = f"?stage={stage}" if stage else ""
        _thr_inner = (
            f'<div id="fnl-wrap" data-help="{_h.escape(t("hint.funnel"))}"'
            f' hx-get="/ui/funnel{_qs}" hx-trigger="load, every 60s" hx-swap="innerHTML"></div>'
            f'<div class="thr-h">{inbox_lbl}</div>'
            f'<input id="ti-q" class="ti-q" type="search" autocomplete="off"'
            f' data-help="{_h.escape(t("hint.search"))}"'
            f' placeholder="{_h.escape(t("inbox.search"))}" oninput="filterTi()">'
            f'<div id="tl" hx-get="/ui/threads{_qs}" hx-trigger="load, every 30s"'
            f' hx-swap="innerHTML"></div>'
        )
    else:
        _show_thr = False
        _thr_inner = ""
    _thr_style = "" if _show_thr else " style='display:none'"
    return (
        f'<!doctype html><html lang="{lang}"><head>'
        f'<meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'{_FAVICON}'
        f'<title>Stepan 2</title>'
        f'<link rel="stylesheet" href="{_FA}">'
        f'<script src="{_HTMX}" defer></script>'
        f'<style>{_CSS}</style></head><body>'
        f'<aside class="sid">'
        f'<div class="sid-top">'
        f'<span class="logo">Stepan 2</span>'
        f'<div style="display:flex;gap:.25rem;align-items:center">'
        f'<button class="sb-col" id="sb-col" title="Toggle sidebar">◂</button>'
        f'<button class="help-btn" onclick="toggleHelp()" title="{help_lbl}">?</button>'
        f'</div>'
        f'</div>'
        f'<nav class="sid-nav">{nav}</nav>'
        f'<div class="sid-ft">'
        f'<div class="bft-lbl">{_h.escape(t("branch.filter"))}</div>'
        f'<div id="branch-sel" data-help="{_h.escape(t("hint.branch"))}"'
        f' hx-get="/ui/branches/widget"'
        f' hx-trigger="load"'
        f' hx-swap="innerHTML"></div>'
        f'<div id="bot-tog-wrap" data-help="{_h.escape(t("hint.bot_global"))}"'
        f' hx-get="/ui/agent-status"'
        f' hx-trigger="load"'
        f' hx-swap="innerHTML"></div>'
        f'<div style="margin-top:.45rem" data-help="{_h.escape(t("hint.lang"))}">'
        f'<div style="font-size:.63rem;color:#4a5568">lang</div>'
        f'<div class="lrow">{_lb("ru")}{_lb("en")}{_lb("id")}</div>'
        f'</div>'
        f'</div></aside>'
        f'<div class="sbrz" id="sbrz" title="⇆ Resize sidebar"></div>'
        f'<div class="thr"{_thr_style}>{_thr_inner}</div>'
        f'<div class="thrz" id="thrz" title="⇆ Resize threads"></div>'
        f'<div id="main">{main_html}</div>'
        # mobile-only bar: ☰ opens the sidebar overlay; ‹ (shown when a chat is open) slides
        # the chat back off to reveal the thread list. Hidden on desktop via @media.
        f'<div class="mbar">'
        f'<button class="mbtn mnav" onclick="toggleNav()" title="Menu">☰</button>'
        f'<button class="mbtn mback" onclick="backToList()" title="Back">‹</button>'
        f'</div>'
        f'<div id="help-tip"></div>'
        f'<script>{script}</script>'
        f'</body></html>'
    )
