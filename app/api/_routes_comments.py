"""Post-comments panel — see how the bot handles comments under our posts, like the chat
view does for DMs. Read-only: comments grouped by post, each showing the author's line, the
bot's public reply (or why it was skipped/hidden), and the status."""
from __future__ import annotations

import html as _h

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request
from app.api._i18n import apply_lang, t

router = APIRouter()

_Q = (
    "SELECT pc.media_id, pc.media_permalink, pc.media_caption, pc.external_id,"
    " pc.author_username, pc.text, pc.status, pc.skip_reason, pc.reply_text,"
    " pc.occurred_at, b.name AS branch_name"
    " FROM post_comment pc JOIN branch b ON b.id = pc.branch_id"
    " {where}"
    " ORDER BY pc.media_id, pc.occurred_at DESC LIMIT 300"
)

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


def _comments_panel_html(rows: list, lang: str, multi_branch: bool) -> str:
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
        return (f'<div class="panel"><h2>{title}</h2><p class="muted">{intro}</p>'
                f'<div class="emp">{empty}</div></div>')

    # Group by post, preserving query order (media_id, occurred_at DESC).
    posts: dict[str, dict] = {}
    for r in rows:
        p = posts.setdefault(r.media_id, {
            "permalink": r.media_permalink, "caption": r.media_caption,
            "branch": r.branch_name, "comments": []})
        p["comments"].append(r)

    out = [f'<div class="panel"><h2>{title}</h2><p class="muted">{intro}</p>']
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
            if c.reply_text:
                body = (f'<div class="cm-reply"><i class="fa-solid fa-turn-up fa-rotate-90">'
                        f'</i> {_h.escape(c.reply_text)}</div>')
            elif c.skip_reason:
                body = f'<div class="cm-skip muted">{_h.escape(c.skip_reason)}</div>'
            else:
                body = ""
            out.append(
                f'<div class="cm-item"><div class="cm-lead"><b>@{author}</b> {ctext}</div>'
                f'{body}<div class="cm-status">{icon} {st}</div></div>')
        out.append("</div>")
    out.append("</div>")
    out.append(_STYLE)
    return "".join(out)


_STYLE = (
    "<style>"
    ".cm-post{margin:14px 0;border:1px solid var(--line,#2a2f3d);border-radius:10px;"
    "overflow:hidden}"
    ".cm-post-h{padding:10px 12px;background:var(--card2,#1d212d);font-weight:600;"
    "display:flex;gap:8px;align-items:center}"
    ".cm-post-h a{color:var(--accent,#4f8cff);text-decoration:none}"
    ".cm-item{padding:10px 12px;border-top:1px solid var(--line,#2a2f3d)}"
    ".cm-lead{margin-bottom:4px}"
    ".cm-reply{margin:4px 0 4px 14px;padding:6px 10px;border-left:2px solid var(--accent,#4f8cff);"
    "background:var(--card,#171a23);border-radius:0 8px 8px 0}"
    ".cm-skip{margin:4px 0 4px 14px;font-size:13px}"
    ".cm-status{font-size:12px;color:var(--muted,#9aa1b5);margin-top:4px}"
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
        rows = (await session.execute(text(_Q.format(where=where)), params)).all()
    multi = not branch_ids or len(branch_ids) > 1
    return HTMLResponse(_comments_panel_html(list(rows), lang, multi))
