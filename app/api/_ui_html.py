"""HTML generators for the 3-column manager UI (sidebar + thread list + panel)."""
from __future__ import annotations

import html as _h
import json as _json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.conversation.needs import NeedsProfile
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

from ._i18n import t

# Viewer-local time moved to _ui_fmt on 2026-07-28. Imported back so every existing
# `from ._ui_html import fmt_dt` keeps working: the move changed no call site, which is
# what keeps it a relocation rather than a rewrite. The noqa is load-bearing — without it
# the linter reads these as unused and deletes the bridge.
from ._ui_fmt import (  # noqa: F401,E402 — re-export, see above
    VIEWER_TZ_COOKIE,
    _ago,
    _as_dt,
    _fmt_dt_short,
    _fmt_time,
    _render_tz_h,
    fmt_dt,
    set_render_tz,
    viewer_local,
    viewer_tz_offset,
)


def _js_str(s: str) -> str:
    """A safe JS string literal for inlining a (localized) message into the shell script."""
    return _json.dumps(s, ensure_ascii=False)


# Tz offset (hours, may be fractional for +5:30 etc.) applied to timestamps in the current
# render. Fed the VIEWER's own tz (from the browser, via the `tzoff` cookie) so every admin
# sees times in their own zone — NOT the branch's. The one deliberate exception is the Reports
# "activity by hour" histogram, which stays branch-local (see _HOUR_Q) on purpose.
_HTMX = "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"
_FA = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"

# The stylesheet moved to _ui_css on 2026-07-28 — 625 lines of data in the middle of the
# module that draws the page. Imported rather than re-exported blind: app_shell below is
# its only consumer, and the tests that assert a rule reaches the page read it from here.
from ._ui_css import _CSS  # noqa: E402

_STC: dict[str, str] = {
    "new": "sn", "nurturing": "snu", "qualifying": "sq", "presenting": "sp",
    "objection": "so", "ready": "sr", "handed_off": "sh", "dormant": "sd",
    "manager": "sm",
}

# The correct funnel order (verified against 7 days of live bot-driven transitions,
# 2026-07-23): a fresh lead goes straight to discovery 657 times against 8 that land in
# nurturing first — nurturing is not a step a lead passes through, it's a state entered
# (and left) from ANY active stage when they go quiet mid-conversation (84% of entries into
# it came from qualifying/presenting/objection, not from new), exactly like dormant already
# is. It moved out of the linear pipeline into the side-track for that reason.
# The line every won lead walks once, in order. `objection` and `nurturing` are NOT steps —
# both are entered from and returned to any active stage (see domain/enums.Stage), so in the
# line they made the funnel read as if doubt and silence were places on the way to a sale.
_PIPELINE = ("new", "qualifying", "presenting", "ready", "handed_off")
_SIDE_STAGES = ("objection", "nurturing", "dormant", "manager")
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
    "mcp": "help.mcp",
}


# Section-level help texts (help.*) now ride on the nav links as data-help tips —
# see _na in app_shell. No out-of-band machinery needed: the tips live in the shell
# markup itself and the delegated hover handler reads them straight off the DOM.


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
            # ad_id comes from the IG ad payload (attacker-influenceable). Keep it in a
            # data-* attribute and copy via this.dataset — never interpolate it into an
            # inline JS string, where html.escape's &#x27; decodes back to ' and breaks out.
            safe_id = _h.escape(ad_id)
            parts.append(
                f'<span class="srcid" title="Copy ad ID" data-clip="{safe_id}"'
                f' onclick="navigator.clipboard&&navigator.clipboard.writeText(this.dataset.clip)">'
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


_CHANNEL_ICON = {
    "instagram": ("fa-brands fa-instagram", "#e1306c"),
    "meta_business": ("fa-brands fa-facebook", "#1877f2"),
    "whatsapp": ("fa-brands fa-whatsapp", "#25d366"),
}


def _channel_badge(kind: str | None) -> str:
    icon, color = _CHANNEL_ICON.get(str(kind), ("fa-solid fa-comment", "#8a94a6"))
    return f'<i class="{icon}" style="color:{color}" title="{_h.escape(str(kind or ""))}"></i>'


def _thread_item(row: object, active_tid: int | None, show_branch: bool = False,
                 filter_qs: str = "") -> str:
    (tid, name, stage, last_act, phone, product_slug,
     ig_username, avatar_url, follower_count, following_count, agent_enabled,
     last_msg, last_dir, cnt_in, cnt_out, branch_name, tz_offset_h,
     channel_kind) = row  # type: ignore[misc]
    dt = _as_dt(last_act)  # raw UTC; _fmt_dt_short applies the viewer offset once (was a
    # per-row branch shift here + the contextvar shift there — now a single viewer shift)
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
    # Preserve the active inbox filter in the pushed URL, so a full reload (F5 / background
    # nav) rebuilds the shell with the same filtered thread list, not the whole inbox.
    _chat_url = f"/ui/chat/{tid}?{filter_qs}" if filter_qs else f"/ui/chat/{tid}"
    _back_url = f"/ui/inbox?{filter_qs}" if filter_qs else "/ui/inbox"
    _kind_attr = _h.escape(str(channel_kind or ""))
    _name_esc = _h.escape(str(name or "Lead"))
    return (
        f'<a class="ti{on}" data-channel-kind="{_kind_attr}"'
        f' hx-get="/ui/chat/{tid}" hx-target="#main" hx-push-url="{_h.escape(_chat_url)}"'
        f' onclick="setOn(this);setOpenThread({tid})"'
        f' href="{_h.escape(_back_url)}">'
        f'{_avatar(str(name or "?"), avatar_url)}'
        f'<div class="ti-body">'
        f'<div class="ti-t">{_channel_badge(channel_kind)}'
        f' <span class="ti-n">{_name_esc}</span>'
        f'{bot_off}{br_badge}'
        f'<span class="ti-ts">{_fmt_dt_short(dt)}</span></div>'
        f'<div class="ti-p">{_badge(str(stage or "new"))}{prod_badge}</div>'
        f'{handle_row}'
        f'{sub_row}</div></a>'
    )


def thread_list_html(
    threads: list, active_tid: int | None = None, show_branch: bool = False,
    filter_qs: str = "",
) -> str:
    if not threads:
        return f'<div class="emp">{_h.escape(t("inbox.empty"))}</div>'
    return "".join(_thread_item(r, active_tid, show_branch, filter_qs) for r in threads)


_LINK_RE = re.compile(r"(https?://[^\s<]+)")
_MEDIA_PH = {"🖼 media", "🎤 voice", "GIF", "🖼 медиа", "🎤 голосовое"}
# Text the backfill leaves when it gives up recognizing — still "not recognized" for the
# manual button (offer a fresh attempt, not a re-run).
_MEDIA_FAILED = {"🎤 (voice — no transcript)", "🖼 (image — tidak bisa dibaca)"}


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


def _recognize_btn_html(mid: int, media_kind: str | None, recognized: bool) -> str:
    """Send this attachment to the broker for recognition on demand. Shown on voice/images
    whose bytes we hold: the backfill can give up (broker down, an old failure it never
    retried) and leave a bare placeholder, and only a human can decide it's worth another
    call. The transcript/caption lands in the message text, so Stepan reads it next turn."""
    if media_kind not in ("audio", "image"):
        return ""
    label = t("chat.recognize_again") if recognized else t("chat.recognize")
    icon = "fa-rotate" if recognized else "fa-wand-magic-sparkles"
    hint = _h.escape(t("chat.recognize_hint"))
    return (
        f'<button class="mrec" hx-post="/ui/chat/media/{mid}/recognize"'
        f' hx-target="#bb-{mid}" hx-swap="outerHTML" hx-disabled-elt="this"'
        f' title="{hint}">'
        f'<i class="fa-solid {icon}"></i> {_h.escape(label)}</button>'
    )


def _media_pending_html(mid: int) -> str:
    """Placeholder for an attachment whose bytes are still backfilling. It polls its own
    bubble (/ui/chat/bubble/{mid}) every few seconds and swaps itself for the real media
    once ready — the append-only message poll never revisits an already-rendered bubble, so
    without this a late download stays invisible until a manual reload."""
    return (
        f'<div class="media-load" hx-get="/ui/chat/bubble/{mid}"'
        f' hx-trigger="load delay:4s" hx-target="#bb-{mid}" hx-swap="outerHTML"'
        f' hx-push-url="false"><i class="fa-solid fa-spinner fa-spin"></i></div>'
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
    (mid, direction, sent_by, text, ts, llm_info, link_url, preview_url,
     media_id, media_kind, media_ready, media_pending) = row[:12]  # type: ignore[misc]
    # Optional trailing columns, in _MSG_COLS order: sent_by_name, then the excluded flag.
    # Positional pops keep older 12-column callers (tests, legacy fixtures) working unchanged.
    rest = list(row[12:])  # type: ignore[index]
    sent_by_name = rest.pop(0) if rest else None
    excluded = bool(rest.pop(0)) if rest else False  # greyed, out of Stepan's context
    ex = " bb-ex" if excluded else ""
    who_key = f"who.{sent_by}" if sent_by in ("agent", "manager", "lead") else ""
    who = _h.escape(t(who_key) if who_key else str(sent_by or ""))
    if sent_by == "manager" and (sent_by_name or "").strip():
        # WHICH manager wrote this — the dashboard stamps the session user's name at send
        # time; IG-app replies carry no identity and keep the generic label.
        who = _h.escape(str(sent_by_name).strip())
    time_str = _fmt_time(ts)
    ready = bool(media_id and media_ready)
    raw = str(text or "").strip()
    caption = "" if (ready and raw in _MEDIA_PH) else _linkify(text)
    att = ""
    if ready:
        att += _media_html(int(media_id), media_kind)
        # Un-recognized media still shows its placeholder text (or the 'gave up' fallback).
        att += _recognize_btn_html(
            int(mid), media_kind, recognized=raw not in _MEDIA_PH and raw not in _MEDIA_FAILED)
    elif media_id and media_pending:
        att += _media_pending_html(int(mid))
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
                 "product_changed": "chat.product",
                 "manager_note_set": "chat.manager_note_set",
                 "manager_note_cleared": "chat.manager_note_cleared",
                 "stage_reason": "chat.stage_reason",
                 "crm_pushed": "chat.crm_pushed",
                 "crm_push_failed": "chat.crm_push_failed"}


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


# A stage/alert event is written the instant the decision is made — BEFORE the reply that
# triggered it is actually sent (the "humanize" anti-ban delay adds up to ~30-60s, longer on
# a soft-block retry). Anchoring a 'stage' event to its own created_at would show the system
# line ABOVE the bot reply a human reader would expect it to follow. Look for the next
# outgoing bubble within this window and display the event right after it instead.
_EVENT_ANCHOR_WINDOW = timedelta(minutes=5)


def _anchor_event_ts(event_ts: datetime, out_ts: list[datetime]) -> datetime:
    """The display timestamp for a 'stage' event: the next outgoing message at/after it
    within the anchor window (so it reads in the order a human saw it happen), else its own
    timestamp unchanged (e.g. a manual stage change with no reply attached)."""
    for ts in out_ts:  # out_ts is sorted ascending
        if ts >= event_ts:
            return ts if ts - event_ts <= _EVENT_ANCHOR_WINDOW else event_ts
    return event_ts


def _merge_feed(msgs: list, events: list, tid: int, lead_seen_at: datetime | None) -> str:
    """Message bubbles + system-log lines, interleaved in DISPLAY order (not raw write
    order — see _anchor_event_ts)."""
    out_ts = sorted(ts for m in msgs if m[1] == "out" and (ts := _as_dt(m[4])) is not None)
    items = [(_as_dt(m[4]) or datetime.min, 0, _bubble(m, tid, lead_seen_at)) for m in msgs]
    items += [
        (_anchor_event_ts(_as_dt(e[5]) or datetime.min, out_ts), 1, _event_bubble(e))
        for e in events
    ]
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


def _failed_bubble(oid: int, tid: int, ptxt: str, error: str) -> str:
    """A send that never reached the lead — the manager sees WHY (e.g. Meta's 24h window
    closed) instead of the queued line silently vanishing, and can retry or dismiss it."""
    err = _h.escape(t("chat.send_failed"))
    reason = _h.escape(error) if error else ""
    reason_html = f' · {reason}' if reason else ""
    retry_btn = (
        f'<button class="trx" title="{_h.escape(t("chat.retry"))}" tabindex="-1"'
        f' hx-post="/ui/chat/{tid}/pending/{oid}/retry"'
        f' hx-target="#pend-{tid}" hx-swap="outerHTML">↻</button>'
    )
    dismiss_btn = (
        f'<button class="delx" title="{_h.escape(t("chat.dismiss"))}" tabindex="-1"'
        f' hx-post="/ui/chat/{tid}/pending/{oid}/delete"'
        f' hx-target="#ppb-{oid}" hx-swap="outerHTML" hx-confirm="">×</button>'
    )
    return (
        f'<div class="bb bb-o bb-f" id="ppb-{oid}">'
        f'<div class="bm" style="color:#ff6b6b">'
        f'✗ {err}{reason_html} {retry_btn} {dismiss_btn}</div>'
        f'<div class="bt">{_h.escape(str(ptxt or ""))}</div></div>'
    )


def _pending_bubble(row: object, tid: int, idx: int) -> str:
    # (outbox id, text, scheduled_at, llm_info, tr, status, error)
    oid, ptxt, sched, llm_info, tr_text, status, error = row
    if status == "failed":
        return _failed_bubble(oid, tid, str(ptxt or ""), str(error or ""))
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


# Funnel order first, then the side states — objection and nurturing are events that can
# happen at any step, not steps themselves (see domain/enums.Stage).
_STAGES = (
    "new", "qualifying", "presenting", "ready", "handed_off",
    "objection", "nurturing", "dormant", "manager",
)


def chat_bot_pill_html(tid: int, enabled: bool) -> str:
    """Per-lead bot toggle in the chat header — a two-position switch (OFF | ON) whose knob
    slides to the active side. Submitting flips agent_enabled (hx-swap=outerHTML)."""
    state = "on" if enabled else "off"
    off_lbl = _h.escape(t("bot.off"))
    on_lbl = _h.escape(t("bot.on"))
    title = _h.escape(t("bot.on" if enabled else "bot.off"))
    return (
        f'<form id="bot-pill-{tid}" style="display:inline;margin:0"'
        f' data-help="{_h.escape(t("hint.bot_chat"))}"'
        f' hx-post="/ui/chat/{tid}/bot-toggle"'
        f' hx-target="#bot-pill-{tid}" hx-swap="outerHTML">'
        f'<button type="submit" class="bot-tog {state}" title="{title}"'
        f' aria-label="{title}">'
        f'<span class="knob"></span>'
        f'<span class="seg off">{off_lbl}</span>'
        f'<span class="seg on">{on_lbl}</span>'
        f'</button>'
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
    needs_pending: bool = False,
    products: list | None = None,
    manager_note: str | None = None,
    channel_kind: str | None = None,
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
    # fmt_dt coerces the shape raw text() SQL returns (str on sqlite, datetime on pg) and
    # applies the viewer's offset — the last hand-rolled copy of those two steps.
    if created := fmt_dt(created_at, "%d %b %Y"):
        meta_parts.append(f"<span>📅 с {created}</span>")
    if last_in_at:
        meta_parts.append(f'<span>⬇ {_fmt_time(last_in_at)}</span>')
    meta_row = (
        f'<div class="ch-meta">{"  ·  ".join(meta_parts)}</div>'
        if meta_parts else ""
    )
    src_chip = (
        f'<span class="ch-src">{_channel_badge(channel_kind)}</span>'
        if channel_kind else ""
    )
    src_bar = _source_bar(lead_source, ad_id, ad_media_id, ad_preview_url)
    bot_pill = chat_bot_pill_html(tid, agent_enabled)
    block_pill = chat_block_pill_html(tid, is_blocked)
    clear_btn = _clear_ctx_btn(tid)
    tr_all_btn = (
        f'<button class="act-btn" onclick="trAll({tid},this)" tabindex="-1"'
        f' data-help="{_h.escape(t("chat.translate_all"))}">🌐</button>'
    )
    return (
        f'<div id="chat-hdr-{tid}">'
        f'<div class="ch">'
        f'{av_html}'
        f'<span class="ch-n">{name_html}{handle_html}</span>'
        f'{src_chip}{product_badge}{ig_chip}'
        f'<div class="ch-acts">{tr_all_btn}{bot_pill}{block_pill}{clear_btn}{stage_sel}</div>'
        f'{meta_row}'
        f'</div>'
        f'{src_bar}'
        f'{needs_block_html(needs, tid, needs_pending)}'
        f'</div>'
    )


def note_popup_slot_html(tid: int) -> str:
    """Empty placeholder the stage-change route OOB-fills with stage_reason_popup_html —
    must exist in the initial panel render so the swap target is there on first load."""
    return f'<div id="note-popup-{tid}"></div>'


def stage_reason_popup_html(tid: int, oob: bool = False) -> str:
    """Mini popup asking WHY the stage just changed, shown only right after a MANUAL move
    (chat_stage is a manager-only UI route — Stepan's own stage transitions never render
    HTML, so this never fires for a bot-driven change). The answer becomes the per-lead
    manager_note Stepan reads every turn (see prompt.manager_note_block) and is logged to
    ThreadLog for chronology (manager_note_html's old always-on box is gone — this popup is
    now the only way to set/see it change)."""
    oob_attr = ' hx-swap-oob="true"' if oob else ""
    return (
        f'<div id="note-popup-{tid}"{oob_attr}>'
        f'<div class="note-pop-bg" onclick="closeNotePopup({tid})"></div>'
        f'<form class="note-pop" hx-post="/ui/chat/{tid}/manager-note"'
        f' hx-target="#note-popup-{tid}" hx-swap="innerHTML">'
        f'<div class="note-pop-h">{_h.escape(t("chat.stage_reason_title"))}</div>'
        f'<textarea name="note" rows="2" autofocus'
        f' placeholder="{_h.escape(t("chat.manager_note_ph"))}"'
        f' style="width:100%;font-size:.78rem;background:#1a1f2b;color:#c9d1d9;'
        f'border:1px solid #2d3748;border-radius:6px;padding:.4rem;resize:vertical"></textarea>'
        f'<div style="margin-top:.4rem;text-align:right">'
        f'<button type="button" class="act-btn" style="margin-right:.3rem"'
        f' onclick="closeNotePopup({tid})">{_h.escape(t("chat.skip"))}</button>'
        f'<button type="submit" class="act-sel">{_h.escape(t("chat.save"))}</button>'
        f'</div></form></div>'
    )


def needs_block_html(
    needs: NeedsProfile | None, tid: int, pending: bool = False,
) -> str:
    """Render the captured Value-Proposition-Canvas profile (jobs/pains/gains) so the
    manager sees what Stepan discovered. `needs` is pre-translated by the caller from
    cache only (see needs_translate.cached_needs) — no broker call in this render path.

    When `pending` is True, some phrase isn't cached for the viewer's language yet: the
    box still shows (with untranslated fallback text) but also carries an hx-get that
    lazily fetches the real translation from /chat/{tid}/needs and swaps itself out once
    the broker responds, so the initial page load never blocks on LLM latency."""
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
    lazy_attrs = (
        f' hx-get="/ui/chat/{tid}/needs" hx-trigger="load" hx-swap="outerHTML"'
        if pending else ""
    )
    return (
        f'<div class="nd-box" id="nd-{tid}" data-help="{_h.escape(t("hint.needs"))}"'
        f'{lazy_attrs}>{"".join(rows)}</div>'
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
    needs_pending: bool = False,
    events: list | None = None,
    products: list | None = None,
    manager_note: str | None = None,
    channel_kind: str | None = None,
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
        last_active_at=last_active_at, needs=needs, needs_pending=needs_pending,
        products=products, manager_note=manager_note, channel_kind=channel_kind,
    )
    return (
        f'{header}'
        f'{note_popup_slot_html(tid)}'
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
    stage: str = "", ad_id: str = "", grp: str = "", is_super: bool = True,
    lead_type: str = "", audience: str = "", awaiting: str = "", kind: str = "",
    q: str = "", no_ad: str = "",
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
    # Inbox badge = chats awaiting a Stepan reply (the queue). The nav LINK opens the full
    # inbox; the badge NUMBER opens only the awaiting chats (stopPropagation so the click
    # doesn't also trigger the parent link). Empty when zero → hidden by .na-badge:empty.
    # One-number badge: the endpoint fills it with the count Stepan owes a reply to, or with
    # nothing at all when that is zero. The outer span just polls and holds it (no own onclick);
    # the number itself swaps the thread list via htmx, so the open chat survives the click.
    inbox_badge = (
        '<span class="na-badge2" id="inbox-badge"'
        ' hx-get="/ui/inbox/awaiting-count" hx-trigger="load, every 15s"'
        ' hx-swap="innerHTML" hx-target="this" hx-push-url="false"></span>'
    )

    coach_extra = (
        ' hx-get="/ui/coach/panel" hx-target="#main" hx-push-url="/ui/coach"'
        " onclick=\"setOn(this,'na');showThr(false)\""
    )
    nav = (
        _na("nav.inbox", "/ui/inbox", "fa-solid fa-inbox", "inbox", badge=inbox_badge)
        + _hna("nav.outbox", "/ui/outbox/panel", "fa-solid fa-paper-plane", "outbox", outbox_badge)
        + _na("nav.coach", "#", "fa-solid fa-pencil", "coach", coach_extra)
        + _na("nav.know", "/ui/knowledge", "fa-solid fa-book", "know")
        # nav.personas is hidden: the persona library never wired into the reply path — the
        # prompt reads the persona_core KB doc. Routes stay for direct URLs until the
        # library either lands for real or is removed.
        + _hna("nav.products", "/ui/products/panel", "fa-solid fa-box", "products")
        + _hna("nav.reports", "/ui/reports/panel", "fa-solid fa-chart-bar", "reports")
        + _hna("nav.strategy", "/ui/strategy/panel", "fa-solid fa-sitemap", "strategy")
        + _hna("nav.comments", "/ui/comments/panel", "fa-solid fa-comments", "comments")
        + _hna("nav.leads", "/ui/leads/panel", "fa-solid fa-user-tag", "leads")
        + '<div class="nav-sep"></div>'
        + (_hna("nav.members", "/ui/members/panel", "fa-solid fa-users", "members")
           if is_super else "")
        + _hna("nav.settings", "/ui/settings/panel", "fa-solid fa-gear", "settings")
        + '<div class="nav-sep"></div>'
        + (_hna("nav.branches", "/ui/branches/panel", "fa-solid fa-building", "branches")
           if is_super else "")
        + (_hna("nav.mcp", "/ui/mcp/panel", "fa-solid fa-plug", "mcp")
           if is_super else "")
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
        # transient toast for a failed action (e.g. a translate that 504'd at the gateway)
        f'var _TRERR={_js_str(t("chat.tr_retry"))};'
        "var _toastT=null;"
        "function toast(msg){var el=document.getElementById('toast');"
        "if(!el){el=document.createElement('div');el.id='toast';el.className='toast';"
        "document.body.appendChild(el);}el.textContent=msg;"
        "el.classList.add('on');clearTimeout(_toastT);"
        "_toastT=setTimeout(function(){el.classList.remove('on');},3200);}"
        # translate fetch guard: fetch() does NOT reject on a 5xx, so an nginx/Cloudflare 504
        # arrives as an ok:false response whose BODY is the gateway's HTML error page. Injecting
        # r.text() then dumped that whole page into the bubble. Only accept a 2xx; on anything
        # else, restore and ask to retry via a toast.
        "function trFetch(url,opts){return fetch(url,opts).then(function(r){"
        "if(!r.ok)throw new Error('http '+r.status);return r.text();});}"
        # coach: append the manager's own message instantly (optimistic), then cycle a
        # detailed 'what Stepan is doing' status while the deep call runs.
        "function coachSend(f){var ta=f.querySelector('textarea[name=request]');"
        "var v=(ta?ta.value:'').trim();var box=document.getElementById('coach-msgs');"
        "if(v&&box){var d=document.createElement('div');d.className='bb bb-o mgr';"
        "d.innerHTML='<div class=\\\"bt\\\"></div><div class=\\\"bm\\\">'+"
        "((f.getAttribute('data-mgr')||'')+' · now')+'</div>';"
        "d.querySelector('.bt').textContent=v;box.appendChild(d);}"
        "if(ta)ta.value='';scrollMsgs('coach');coachThinkStart();}"
        "var _coachTimer=null;"
        "function coachThinkStart(){var el=document.getElementById('coach-think-txt');"
        "var wrap=document.getElementById('coach-thinking');if(!el||!wrap)return;"
        "var msgs;try{msgs=JSON.parse(wrap.getAttribute('data-msgs')||'[]');}catch(e){msgs=[];}"
        "if(!msgs.length)return;var i=0;el.textContent=msgs[0];coachThinkStop();"
        "_coachTimer=setInterval(function(){i=(i+1)%msgs.length;el.textContent=msgs[i];},2500);}"
        "function coachThinkStop(){if(_coachTimer){clearInterval(_coachTimer);_coachTimer=null;}}"
        "function setOpenThread(tid){"
        "document.cookie='stepan2_open_thread='+tid+';path=/;max-age=86400;samesite=lax';"
        # mobile: opening a chat slides the #main overlay in over the thread list
        "document.body.classList.add('chat-open');document.body.classList.remove('nav-open');}"
        "function backToList(){document.body.classList.remove('chat-open');}"
        "function toggleNav(){document.body.classList.toggle('nav-open');}"
        # Live search hits the SERVER (debounced): the list is capped at 100 rows, so the old
        # client-side show/hide could only match chats already rendered and silently missed
        # every older one. Reloading only #tl keeps the input (a sibling, not inside #tl)
        # focused mid-typing; the address bar is kept in sync so the 30s #tl poll and a full
        # reload request the same query.
        # Call this ONLY from real user input. It issues a request that re-renders #tl, so
        # calling it from an htmx swap hook (an afterSettle on #tl once did) makes #tl reload
        # itself forever — a flickering list, and every pass rewrote the address bar back to
        # /ui/inbox, dropping the open chat out of the URL.
        "var _tiT=null;"
        "function filterTi(){clearTimeout(_tiT);_tiT=setTimeout(doFilterTi,250);}"
        "function doFilterTi(){var i=document.getElementById('ti-q');"
        "var q=i?i.value.trim():'';"
        "var p=new URLSearchParams(window.location.search);"
        "if(q)p.set('q',q);else p.delete('q');var qs=p.toString();"
        # source:i — without it htmx treats <body> as the requesting element and marks it
        # htmx-request, which used to switch on every indicator on the page.
        "htmx.ajax('GET','/ui/threads'+(qs?'?'+qs:''),{target:'#tl',source:i});"
        "history.replaceState(null,'','/ui/inbox'+(qs?'?'+qs:''));}"
        # Instant connector-chip state: clicking the active chip clears the filter (all neutral);
        # clicking another makes it 'on' and struck-through 'off' on the rest. The hx-get still
        # reloads #tl server-side; this only fixes the chip bar's visual state, which #tl won't.
        # Multi-toggle connector facet: flip THIS chip, recompute the ON set from the DOM, and
        # reload only #tl with kind=<on-list> ('' all on, 'none' all off). The chip bar isn't in
        # #tl, so its state is the client-side source of truth; the address bar is kept in sync so
        # the 30s #tl poll (which mirrors window.location.search) requests the same set.
        "function kindChip(btn){var nowOn=btn.classList.toggle('on');"
        "btn.classList.toggle('off',!nowOn);"
        "var all=[].slice.call(btn.parentNode.querySelectorAll('.chk-kind'));"
        "var on=all.filter(function(b){return b.classList.contains('on');})"
        ".map(function(b){return b.getAttribute('data-kind');});"
        "var kind=on.length===all.length?'':(on.length===0?'none':on.join(','));"
        "var p=new URLSearchParams(window.location.search);"
        "if(kind)p.set('kind',kind);else p.delete('kind');var qs=p.toString();"
        "htmx.ajax('GET','/ui/threads'+(qs?'?'+qs:''),{target:'#tl',source:btn});"
        "history.replaceState(null,'','/ui/inbox'+(qs?'?'+qs:''));}"
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
        "});"
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
        # No dead zones: hovering somewhere without its own hint falls back to the section
        # hint (the panel title's data-help), so any spot in a section still explains itself.
        "if(!el&&e.target.closest){var pn=e.target.closest('#main,.thr,aside');"
        "if(pn)el=pn.querySelector('[data-help]');}"
        "if(!el){tp.style.display='none';return;}"
        "tp.textContent=el.getAttribute('data-help');"
        "tp.style.display='block';tp.style.top='0px';tp.style.left='0px';"
        "var r=el.getBoundingClientRect();"
        "var top=r.top-tp.offsetHeight-8;if(top<4)top=r.bottom+8;"
        "var left=Math.max(4,Math.min(r.left,window.innerWidth-tp.offsetWidth-4));"
        "tp.style.top=top+'px';tp.style.left=left+'px';});"
        # ad-creative hover: a floating <img> proxied same-origin from /ui/ig-preview/<mid>
        # (one delegated pair of listeners, survives htmx re-renders; hides on load error).
        "var _igPop;"
        "function _igShow(el){var mid=el.getAttribute('data-ig');if(!mid)return;"
        "if(!_igPop){_igPop=document.createElement('div');_igPop.className='ig-pop';"
        "var im=document.createElement('img');"
        "im.onerror=function(){_igPop.style.display='none';};"
        "_igPop.appendChild(im);document.body.appendChild(_igPop);}"
        "var im2=_igPop.querySelector('img');"
        "if(im2.getAttribute('data-mid')!==mid){"
        "im2.setAttribute('data-mid',mid);im2.src='/ui/ig-preview/'+mid;}"
        "var r=el.getBoundingClientRect();_igPop.style.display='block';"
        "_igPop.style.top=(r.bottom+6)+'px';"
        "_igPop.style.left=Math.max(4,Math.min(r.left,window.innerWidth-220))+'px';}"
        "document.addEventListener('mouseover',function(e){"
        "var el=e.target.closest?e.target.closest('.ad-ig[data-ig]'):null;if(el)_igShow(el);});"
        "document.addEventListener('mouseout',function(e){"
        "var el=e.target.closest?e.target.closest('.ad-ig[data-ig]'):null;"
        "if(el&&_igPop)_igPop.style.display='none';});"
        "function setFnl(el){"
        "document.querySelectorAll('.fseg.on,.fchip.on,.fnl-all.on')"
        ".forEach(e=>e.classList.remove('on'));"
        "el.classList.add('on');}"
        # #tl (the thread list) polls itself every 30s (hx-trigger="load, every 30s") using
        # whatever hx-get it was born with — a stage-pill click only swaps its INNERHTML, so
        # a stale/mismatched hx-get would silently re-fetch the WRONG filter on the next poll
        # (previously only synced on a pill click, and only for `stage` — any OTHER active
        # filter, ad_id/lead_type/audience/grp, still got dropped). Force every one of #tl's
        # own requests to mirror the address bar instead — correct no matter how the current
        # filter got there (pill click, direct URL, back/forward).
        "document.body.addEventListener('htmx:configRequest',function(e){"
        "var el=e.detail.elt;"
        "if(el&&el.id==='tl'){e.detail.path='/ui/threads'+window.location.search;}});"
        # The 30s background poll above replaces #tl's innerHTML, which resets its
        # scrollTop to 0 — the manager loses their place in a long thread list every poll.
        # Save/restore scroll position around the swap.
        "var _tlScroll=0;"
        "document.body.addEventListener('htmx:beforeSwap',function(e){"
        "if(e.detail.target&&e.detail.target.id==='tl'){_tlScroll=e.detail.target.scrollTop;}});"
        "document.body.addEventListener('htmx:afterSwap',function(e){"
        "if(e.detail.target&&e.detail.target.id==='tl'){e.detail.target.scrollTop=_tlScroll;}});"
        # values must be a PLAIN object: htmx.ajax merges `values` by for-in iteration, and a
        # FormData has no own enumerable entries — the POST went out with NO parameters, the
        # server saw an empty `text` and silently dropped the send (chat 2872: "send as
        # Stepan" cleared the box but nothing was ever queued).
        "function sendSuggest(tid){"
        "var ta=document.getElementById('sug-ta-'+tid);"
        "if(!ta||!ta.value.trim())return;"
        "htmx.ajax('POST','/ui/chat/'+tid+'/send',{"
        "target:'#msgs-'+tid,swap:'innerHTML',"
        "values:{text:ta.value,source:'agent'}});"
        "document.getElementById('sug-'+tid).innerHTML='';}"
        # per-message translate toggle with LLM fetch + client-side cache
        # Resolves true when the bubble ends up translated. `quiet` suppresses the per-bubble
        # toast so the batch below can report once instead of once per failure.
        "function trMsg(mid,tid,quiet){"
        "var el=document.getElementById('bt-'+mid);"
        "if(!el)return Promise.resolve(true);"
        "if(el.dataset.state==='tr'){"
        "el.innerHTML=el.dataset.orig;el.dataset.state='';"
        "el.classList.remove('trview');return Promise.resolve(true);}"
        "if(el.dataset.tr){"
        "el.dataset.orig=el.innerHTML;"
        "el.innerHTML=el.dataset.tr;el.dataset.state='tr';"
        "el.classList.add('trview');return Promise.resolve(true);}"
        "el.style.opacity='.45';"
        "el.dataset.orig=el.innerHTML;"
        "return trFetch('/ui/chat/'+tid+'/msg/'+mid+'/tr',{headers:{'HX-Request':'true'}})"
        ".then(function(html){"
        "el.style.opacity='';"
        "if(html.trim()){"
        "el.dataset.tr=html;el.innerHTML=html;"
        "el.dataset.state='tr';el.classList.add('trview');return true;}"
        "if(!quiet)toast(_TRERR);return false;})"  # empty = the app-level failure path
        ".catch(function(){el.style.opacity='';if(!quiet)toast(_TRERR);return false;});}"
        # Translate every bubble in the thread — ONE AT A TIME.
        #
        # This used to be a forEach over trMsg with nothing awaited, so a long thread fired
        # thirty-odd broker calls in the same instant and a share of them never came back. The
        # KB translate-all ten lines below has been sequential from the start for exactly this
        # reason; the chat version never got the same treatment.
        #
        # A bubble that fails is retried once after a short pause — that is the difference
        # between "most of them came back" and all of them. Anything still missing is counted
        # and reported once at the end, not as one toast per bubble. The button carries the
        # progress and stays disabled meanwhile, so a second click cannot start a second queue
        # over the same bubbles.
        "function trAll(tid,btn){"
        "var els=[].slice.call(document.querySelectorAll('[id^=\"bt-\"]'))"
        ".filter(function(el){return el.dataset.state!=='tr';});"
        "if(!els.length)return;"
        "var lbl=btn?btn.textContent:'';if(btn)btn.disabled=true;"
        "var i=0,failed=0;"
        "function done(){if(btn){btn.disabled=false;btn.textContent=lbl;}"
        "if(failed)toast(_TRERR+' ('+failed+')');}"
        "function step(){"
        "if(i>=els.length){done();return;}"
        "var mid=els[i++].id.slice(3);"
        "if(btn)btn.textContent=i+'/'+els.length;"
        "trMsg(mid,tid,true).then(function(ok){"
        "if(ok){step();return;}"
        "setTimeout(function(){trMsg(mid,tid,true).then(function(ok2){"
        "if(!ok2)failed++;step();});},700);});}"
        "step();}"
        # KB editor: translate every section (+title) into the UI language, in place, for
        # READING. Reversible toggle; while translated the fields go read-only and Save is
        # locked so a translation can't be saved over the source. Sequential, not parallel, so
        # a multi-section doc doesn't fire a burst of broker calls.
        "function kbTrAll(btn){var doc=btn.dataset.doc;"
        "var form=document.getElementById('kb-form-'+doc);if(!form)return;"
        "var fields=[].slice.call(form.querySelectorAll('.frm-ta,.kb-tr-f'));"
        "var note=document.getElementById('kb-tr-note-'+doc);"
        "var save=document.getElementById('kb-save-'+doc);"
        "function restore(){fields.forEach(function(f){"
        "if(f.dataset.orig!==undefined){f.value=f.dataset.orig;delete f.dataset.orig;}"
        "f.readOnly=false;});if(note)note.style.display='none';if(save)save.disabled=false;"
        "btn.textContent='\\uD83C\\uDF10 '+btn.dataset.lbl;btn.dataset.state='';}"
        "if(btn.dataset.state==='tr'){restore();return;}"
        "btn.disabled=true;btn.textContent='\\u2026';"
        "var list=fields.filter(function(f){return f.value.trim();});var i=0;"
        "function done(ok){btn.disabled=false;"
        "if(!ok){restore();toast(_TRERR);return;}"
        "if(note)note.style.display='';if(save)save.disabled=true;"
        "btn.textContent='\\u21A9 '+btn.dataset.lbl2;btn.dataset.state='tr';}"
        "function step(){if(i>=list.length){done(true);return;}var f=list[i++];"
        "var fd=new FormData();fd.append('text',f.value);"
        "trFetch('/ui/knowledge/'+doc+'/tr',{method:'POST',headers:{'HX-Request':'true'},body:fd})"
        ".then(function(tr){if(tr.trim()){f.dataset.orig=f.value;f.value=tr;f.readOnly=true;}"
        "step();}).catch(function(){done(false);});}"
        "step();}"
        # close the stage-reason popup without saving (Skip, or click the backdrop)
        "function closeNotePopup(tid){var el=document.getElementById('note-popup-'+tid);"
        "if(el)el.innerHTML='';}"
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
        "trFetch('/ui/chat/'+tid+'/translate',{method:'POST',headers:{'HX-Request':'true'}})"
        ".then(function(h){if(h.trim()){out.innerHTML=h;}else{out.innerHTML='';toast(_TRERR);}})"
        ".catch(function(){out.innerHTML='';toast(_TRERR);});}"
        "function trClose(tid){var o=document.getElementById('tr-'+tid);if(o)o.innerHTML='';}"
        # translate the Suggest draft (shows the manager what Stepan's reply says)
        "function trDraft(tid){var ta=document.getElementById('sug-ta-'+tid);"
        "var out=document.getElementById('sug-tr-'+tid);if(!ta||!ta.value.trim())return;"
        "out.textContent='...';var fd=new FormData();fd.append('text',ta.value);"
        "trFetch('/ui/chat/'+tid+'/tr-draft',{method:'POST',headers:{'HX-Request':'true'},"
        "body:fd}).then(function(h){if(h.trim()){out.innerHTML=h;}else{out.textContent='';toast(_TRERR);}})"
        ".catch(function(){out.textContent='';toast(_TRERR);});}"
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
        # ad_id (+ optional grp: pipeline|won|dormant, from a clicked funnel count) narrows
        # only the thread list (funnel stays branch-wide); an active filter shows a
        # dismissable chip linking back to the unfiltered inbox.
        # safe="," keeps the connector facet's comma-list readable (kind=instagram,whatsapp);
        # everything else (notably a search term with spaces or &) is properly encoded.
        _thr_params = "&".join(
            f"{k}={quote_plus(v, safe=',')}"
            for k, v in (("stage", stage), ("ad_id", ad_id), ("no_ad", no_ad), ("grp", grp),
                         ("lead_type", lead_type), ("audience", audience),
                         ("awaiting", awaiting), ("kind", kind), ("q", q)) if v)
        _thr_qs = f"?{_thr_params}" if _thr_params else ""
        _grp_lbl = {"pipeline": t("rep.pipeline"), "won": t("rep.won"),
                    "deal": t("rep.deal"), "dormant": t("rep.dormant")}.get(grp, "")
        _grp_html = (
            f' · <span class="ad-filter-id">{_h.escape(_grp_lbl)}</span>' if _grp_lbl else "")
        # The organic filter reuses the ad chip — same shape, same dismiss, different label.
        _ad_lbl = t("rep.ads_organic") if no_ad else ad_id
        _ad_chip = (
            f'<div class="ad-filter">{_h.escape(t("inbox.ad_filter"))} '
            f'<span class="ad-filter-id">{_h.escape(_ad_lbl)}</span>{_grp_html}'
            f'<a class="ad-filter-x" href="/ui/inbox{_qs}" title="{_h.escape(t("inbox.ad_clear"))}"'
            f'>✕</a></div>'
        ) if (ad_id or no_ad) else ""
        # segment chip (from a clicked segment-tree leaf) — same dismissable chip, back to all.
        # Shows the audience + intent it was opened from (e.g. "Школьники · тёплые") so the
        # filtered count matches the exact leaf that was clicked.
        _seg_bits = " · ".join(
            _h.escape(t(f"{pfx}.{val}"))
            for pfx, val in (("aud", audience), ("seg", lead_type)) if val)
        _seg_chip = (
            f'<div class="ad-filter">{_h.escape(t("inbox.seg_filter"))} '
            f'<span class="ad-filter-id">{_seg_bits}</span>'
            f'<a class="ad-filter-x" href="/ui/inbox{_qs}"'
            f' title="{_h.escape(t("inbox.ad_clear"))}">✕</a></div>'
        ) if (lead_type or audience) else ""
        # awaiting filter (opened from an inbox badge number) — dismissable back to full inbox.
        _await_lbl = {"queue": "inbox.await_queue", "off": "inbox.await_off",
                      "settled": "inbox.await_settled"}.get(awaiting, "inbox.awaiting_filter")
        _await_chip = (
            f'<div class="ad-filter">'
            f'<span class="ad-filter-id">{_h.escape(t(_await_lbl))}</span>'
            f'<a class="ad-filter-x" href="/ui/inbox{_qs}"'
            f' title="{_h.escape(t("inbox.ad_clear"))}">✕</a></div>'
        ) if awaiting else ""
        # Connector filter chips — SERVER-SIDE: the thread list is LIMIT-capped by recency, so a
        # client-side hide would only reveal the connector's chats that made the global top-N
        # (an older Meta chat stays hidden behind newer Instagram ones), so the filter is
        # server-side and reloads #tl.

        # MULTI-TOGGLE facet: each chip is an independent on/off switch; the visible thread list
        # is the UNION of the ON channels. `kind` is a comma-list of ON channels (''=all on,
        # 'none'=all off). kindChip() (client JS) toggles the chip, recomputes the list from the
        # DOM, and reloads only #tl — the server filters by the set.
        _all_kinds = ("instagram", "meta_business", "whatsapp")
        if kind == "none":
            _sel_kinds: set[str] = set()
        elif kind:
            _picked = {x for x in kind.split(",") if x in _all_kinds}
            _sel_kinds = _picked or set(_all_kinds)  # garbage → all on
        else:
            _sel_kinds = set(_all_kinds)

        def _kind_chip(k: str, icon: str, color: str, lbl: str) -> str:
            cls = "chk-kind on" if k in _sel_kinds else "chk-kind off"
            return (
                f'<button type="button" class="{cls}" title="{_h.escape(lbl)}"'
                f' data-kind="{k}" onclick="kindChip(this)"'
                f' data-help="{_h.escape(t("hint.kind_filter"))}">'
                f'<i class="{icon}" style="color:{color}"></i></button>'
            )

        _kind_chips = "".join(
            _kind_chip(k, *_CHANNEL_ICON[k], lbl) for k, lbl in (
                ("instagram", "Instagram"),
                ("meta_business", "Meta Business"),
                ("whatsapp", "WhatsApp"),
            )
        )
        _thr_inner = (
            f'<div id="fnl-wrap" data-help="{_h.escape(t("hint.funnel"))}"'
            f' hx-get="/ui/funnel{_qs}" hx-trigger="load, every 60s" hx-swap="innerHTML"></div>'
            f'<div class="thr-h">{inbox_lbl}<span class="chk-kinds">{_kind_chips}</span></div>'
            f'{_ad_chip}{_seg_chip}{_await_chip}'
            f'<input id="ti-q" class="ti-q" type="search" autocomplete="off"'
            f' data-help="{_h.escape(t("hint.search"))}" value="{_h.escape(q)}"'
            f' placeholder="{_h.escape(t("inbox.search"))}" oninput="filterTi()">'
            f'<div id="tl" hx-get="/ui/threads{_thr_qs}" hx-trigger="load, every 30s"'
            f' hx-swap="innerHTML"></div>'
        )
    else:
        _show_thr = False
        _thr_inner = ""
    _thr_style = "" if _show_thr else " style='display:none'"
    return (
        f'<!doctype html><html lang="{lang}"><head>'
        # Report the browser's own UTC offset (hours) into the `tzoff` cookie so every
        # timestamp renders in the VIEWER's zone, not the branch's. Runs before content; on
        # the very first visit (no cookie yet) it reloads once so the server re-renders in the
        # right zone. The sessionStorage guard makes that reload strictly one-shot even if
        # cookies are blocked, so there's no reload loop.
        f'<script>(function(){{try{{var o=(-new Date().getTimezoneOffset()/60);'
        f'var m=document.cookie.match(/(?:^|; )tzoff=([^;]+)/);var cur=m?m[1]:null;'
        f"if(String(o)!==cur){{document.cookie='tzoff='+o+';path=/;max-age=31536000;samesite=Lax';"
        f"if(cur===null&&!sessionStorage.getItem('tzr')){{sessionStorage.setItem('tzr','1');"
        f'location.reload();}}}}}}catch(e){{}}}})();</script>'
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
        f'<div id="sending-tog-wrap" style="margin-top:.35rem"'
        f' data-help="{_h.escape(t("hint.sending_global"))}"'
        f' hx-get="/ui/sending-status"'
        f' hx-trigger="load"'
        f' hx-swap="innerHTML"></div>'
        f'<div id="comment-tog-wrap" style="margin-top:.35rem"'
        f' data-help="{_h.escape(t("hint.comments_global"))}"'
        f' hx-get="/ui/comment-status"'
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
