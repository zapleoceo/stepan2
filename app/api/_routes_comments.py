"""Post-comments panel — see how the bot handles comments under our posts, like the chat
view does for DMs. Read-only: comments grouped by post, each showing the author's line, the
bot's public reply (or why it was skipped/hidden), and the status."""
from __future__ import annotations

import html as _h
import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request
from app.api._i18n import apply_lang, t
from app.domain.clock import utc_now
from app.modules.comments.translate import cached

logger = logging.getLogger(__name__)
router = APIRouter()

_Q = (
    "SELECT pc.id, pc.media_id, pc.media_permalink, pc.media_caption, pc.external_id,"
    " pc.author_username, pc.text, pc.status, pc.skip_reason, pc.reply_text,"
    " pc.text_tr, pc.reply_tr, pc.occurred_at, b.name AS branch_name"
    " FROM post_comment pc JOIN branch b ON b.id = pc.branch_id"
    " {where}"
    " ORDER BY pc.media_id, pc.occurred_at DESC LIMIT 300"
)

def _cached_translations(rows: list, lang: str) -> dict:
    """Whatever is already translated, and nothing more.

    The panel reads; it never translates. Filling the cache on render meant one model call per
    untranslated line before the first pixel - fine when everything was cached, and a visibly
    frozen page the morning after a busy night. The hourly ingest owns filling it (see
    app.modules.comments.translate.translate_pending); a line it has not reached yet shows in
    the original, which is still readable and arrives instantly."""
    if lang == "id":
        return {}
    return {r.id: {"text": cached(r.text_tr, lang),
                   "reply": cached(r.reply_tr, lang)} for r in rows}


_STATUS = {
    "replied":  ("💬", {"ru": "Отвечено", "en": "Replied", "id": "Dibalas"}),
    "dm_sent":  ("📩", {"ru": "Ответ + зов в директ", "en": "Reply + DM invite",
                        "id": "Balas + ajak DM"}),
    "skipped":  ("➖", {"ru": "Пропущено", "en": "Skipped", "id": "Dilewati"}),
    "hidden":   ("🚫", {"ru": "Скрыто (спам/оскорбл.)", "en": "Hidden (spam/abuse)",
                        "id": "Disembunyikan"}),
    "error":    ("⚠️", {"ru": "Ошибка отправки", "en": "Send error", "id": "Gagal kirim"}),
    "pending":  ("⏳", {"ru": "В очереди", "en": "Pending", "id": "Menunggu"}),
}


def _lbl(d: dict, lang: str) -> str:
    return d.get(lang, d.get("en", ""))


def _tr_line(tr: str | None) -> str:
    """Small muted translation under the original — shown only when a translation exists."""
    return f'<div class="cm-tr">{_h.escape(tr)}</div>' if tr else ""


def _stamp_full(when: object) -> str:
    """Absolute time, for the tooltip. The relative label answers "is this fresh"; this one
    answers "which day exactly", and an operator comparing against the Instagram app needs it."""
    if not isinstance(when, datetime):
        return ""
    return when.strftime("%d.%m.%Y %H:%M")


def _stamp_ago(when: object, lang: str) -> str:
    """How long ago, at the coarseness that matters: comments are collected hourly, so
    minute-level precision would be false accuracy."""
    if not isinstance(when, datetime):
        return ""
    delta = utc_now().replace(tzinfo=None) - when.replace(tzinfo=None)
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return _lbl({"ru": "только что", "en": "just now", "id": "baru saja"}, lang)
    if hours < 24:
        n = int(hours)
        return _lbl({"ru": f"{n} ч назад", "en": f"{n}h ago", "id": f"{n} jam lalu"}, lang)
    days = int(hours // 24)
    if days < 30:
        return _lbl({"ru": f"{days} дн назад", "en": f"{days}d ago",
                     "id": f"{days} hari lalu"}, lang)
    return when.strftime("%d.%m.%Y")


def _conversion_line(by_status: dict, lang: str) -> str:
    """The number this page was missing: did anyone we answered actually come into DM.

    'dm_sent' looks like an outcome and is not — it records only that our public line carried
    an invitation. Without this line an operator reads seven invitations as seven wins. Both
    outcomes are shown side by side because the comparison is the point: if inviting converts
    no better than simply being useful, that is a decision about the wording."""
    from app.modules.comments.conversion import Conversion  # noqa: PLC0415

    invited = by_status.get("dm_sent", Conversion(0, 0))
    answered = by_status.get("replied", Conversion(0, 0))
    if not (invited.replies or answered.replies):
        return ""
    label = _lbl({
        "ru": "Дошли в личку за 2 недели после ответа",
        "en": "Came into DM within 2 weeks of the reply",
        "id": "Masuk ke DM dalam 2 minggu setelah balasan",
    }, lang)
    with_invite = _lbl({"ru": "с приглашением", "en": "with an invite",
                        "id": "dengan ajakan"}, lang)
    plain = _lbl({"ru": "просто ответ", "en": "plain answer", "id": "balasan biasa"}, lang)
    of = _lbl({"ru": "из", "en": "of", "id": "dari"}, lang)
    # "0 of 7", not "0%": the raw pair says how much evidence there is, and at this sample
    # size that matters more than the ratio.
    return (
        f'<div class="cm-conv"><span class="cm-conv-l">{_h.escape(label)}</span>'
        f'<span class="cm-conv-v">{_h.escape(with_invite)}: '
        f'<b>{invited.arrived}</b> {_h.escape(of)} {invited.replies}</span>'
        f'<span class="cm-conv-v">{_h.escape(plain)}: '
        f'<b>{answered.arrived}</b> {_h.escape(of)} {answered.replies}</span></div>'
    )


def _comments_panel_html(rows: list, lang: str, multi_branch: bool, trs: dict,
                         conversion: dict | None = None) -> str:
    title = _h.escape(t("nav.comments"))
    intro = _h.escape(_lbl({
        "ru": "Комментарии под нашими постами: что бот ответил публично и кого позвал в директ. "
              "Обновляется раз в час.",
        "en": "Comments under our posts: what the bot replied publicly and who it invited to DM. "
              "Refreshes hourly.",
        "id": "Komentar di postingan kami: balasan publik bot dan siapa yang diajak ke DM. "
              "Diperbarui tiap jam.",
    }, lang))
    if not rows:
        empty = _h.escape(_lbl({
            "ru": "Пока нет собранных комментариев. Включите «Отвечать на комментарии» в "
                  "Настройках канала — раз в час бот подтянет новые.",
            "en": "No comments collected yet. Turn on 'Reply to comments' in channel Settings — "
                  "the bot pulls new ones hourly.",
            "id": "Belum ada komentar. Aktifkan 'Balas komentar' di Pengaturan channel.",
        }, lang))
        return (f'<div class="panel cm-panel"><h2>{title}</h2><p class="muted">{intro}</p>'
                f'<div class="emp">{empty}</div></div>')

    # Group by post, preserving query order (media_id, occurred_at DESC).
    posts: dict[str, dict] = {}
    for r in rows:
        p = posts.setdefault(r.media_id, {
            "permalink": r.media_permalink, "caption": r.media_caption,
            "branch": r.branch_name, "comments": []})
        p["comments"].append(r)

    out = [f'<div class="panel cm-panel"><h2>{title}</h2><p class="muted">{intro}</p>'
           + _conversion_line(conversion or {}, lang)]
    for _mid, p in posts.items():
        cap = _h.escape((p["caption"] or "")[:90]) or _h.escape(_lbl(
            {"ru": "(без подписи)", "en": "(no caption)", "id": "(tanpa teks)"}, lang))
        link = p["permalink"] or "#"
        br = f' · {_h.escape(p["branch"])}' if multi_branch else ""
        out.append(
            f'<div class="cm-post"><div class="cm-post-h">'
            f'<i class="fa-regular fa-image"></i> '
            f'<a href="{_h.escape(link)}" target="_blank" rel="noopener">{cap}</a>'
            f'<span class="muted">{br} · {len(p["comments"])}</span></div>')
        for c in p["comments"]:
            icon, sd = _STATUS.get(c.status, ("•", {"en": c.status}))
            st = _h.escape(_lbl(sd, lang))
            author = _h.escape(c.author_username or "—")
            ctext = _h.escape(c.text or "")
            tr = trs.get(c.id, {})
            if c.reply_text:
                body = (f'<div class="cm-reply"><i class="fa-solid fa-turn-up fa-rotate-90">'
                        f'</i> {_h.escape(c.reply_text)}{_tr_line(tr.get("reply"))}</div>')
            elif c.skip_reason:
                body = f'<div class="cm-skip muted">{_h.escape(c.skip_reason)}</div>'
            else:
                body = ""
            out.append(
                f'<div class="cm-item"><div class="cm-lead"><b>@{author}</b> '
                f'<span class="cm-when" title="{_h.escape(_stamp_full(c.occurred_at))}">'
                f'{_h.escape(_stamp_ago(c.occurred_at, lang))}</span> {ctext}'
                f'{_tr_line(tr.get("text"))}</div>'
                f'{body}<div class="cm-status">{icon} {st}</div></div>')
        out.append("</div>")
    out.append("</div>")
    out.append(_STYLE)
    return "".join(out)


_STYLE = (
    "<style>"
    # #main is overflow:hidden, so the panel needs its OWN scroller or a long list is clipped
    # (this is why new comments 'didn't show' — they were below the fold with no scrollbar).
    ".cm-panel{height:100%;overflow-y:auto;padding:.6rem .95rem;box-sizing:border-box}"
    # The conversion strip sits under the intro, above the posts: it is the summary, and a
    # summary below the detail is one nobody reads.
    ".cm-conv{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;margin:2px 0 6px;"
    "padding:8px 12px;border:1px solid var(--line,#2a2f3d);border-radius:8px;font-size:13px}"
    ".cm-conv-l{color:var(--muted,#8b93a7)}"
    ".cm-conv-v{font-variant-numeric:tabular-nums}"
    ".cm-conv-v b{font-size:15px}"
    ".cm-post{margin:14px 0;border:1px solid var(--line,#2a2f3d);border-radius:10px;"
    "overflow:hidden}"
    ".cm-post-h{padding:10px 12px;background:var(--card2,#1d212d);font-weight:600;"
    "display:flex;gap:8px;align-items:center}"
    ".cm-post-h a{color:var(--accent,#4f8cff);text-decoration:none}"
    ".cm-item{padding:10px 12px;border-top:1px solid var(--line,#2a2f3d)}"
    ".cm-lead{margin-bottom:4px}"
    # Muted and inline before the question — the operator's first question about any comment
    # is how stale it is, and it must not compete with the text itself.
    ".cm-when{color:var(--muted,#8b93a7);font-size:12px;white-space:nowrap;margin-right:6px}"
    ".cm-reply{margin:4px 0 4px 14px;padding:6px 10px;border-left:2px solid var(--accent,#4f8cff);"
    "background:var(--card,#171a23);border-radius:0 8px 8px 0}"
    ".cm-skip{margin:4px 0 4px 14px;font-size:13px}"
    ".cm-status{font-size:12px;color:var(--muted,#9aa1b5);margin-top:4px}"
    ".cm-tr{font-size:12.5px;color:var(--muted,#9aa1b5);font-style:italic;margin-top:2px}"
    "</style>"
)


@router.get("/comments/panel", response_class=HTMLResponse)
async def comments_panel(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    conditions, params = [], {}
    if branch_ids:
        conditions.append("pc.branch_id = ANY(:bids)")
        params["bids"] = branch_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    async with session_scope() as session:
        rows = list((await session.execute(text(_Q.format(where=where)), params)).all())
        # Cache only — the page never waits on the broker. It used to translate whatever was
        # missing before rendering a single line, so opening the panel after a batch of new
        # comments meant sitting through that many model calls. Anything not yet translated
        # simply shows in the original; the hourly ingest fills the cache for next time.
        trs = _cached_translations(rows, lang)
        # Only for a single selected branch: the join is per-tenant, and summing two branches
        # into one ratio would compare accounts with different audiences and different volume.
        conversion: dict = {}
        if rows and branch_ids and len(branch_ids) == 1:
            from app.modules.comments.conversion import conversion_by_status  # noqa: PLC0415
            conversion = await conversion_by_status(session, branch_ids[0])
    multi = not branch_ids or len(branch_ids) > 1
    return HTMLResponse(_comments_panel_html(rows, lang, multi, trs, conversion))
