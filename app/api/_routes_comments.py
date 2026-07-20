"""Post-comments panel — see how the bot handles comments under our posts, like the chat
view does for DMs. Read-only: comments grouped by post, each showing the author's line, the
bot's public reply (or why it was skipped/hidden), and the status."""
from __future__ import annotations

import asyncio
import html as _h
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request
from app.api._i18n import apply_lang, t
from app.modules.conversation.translate import target_for_lang, translate_text

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

# At most this many translations run concurrently on a first render — the broker queues the
# rest. Keeps a cold page (nothing cached yet) from firing 50+ parallel chat:fast calls.
_TR_CONCURRENCY = 6


def _cached(raw: str | None, lang: str) -> str | None:
    if not raw:
        return None
    try:
        return json.loads(raw).get(lang)
    except (ValueError, AttributeError):
        return None


def _merge_cache(raw: str | None, lang: str, value: str) -> str:
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    d[lang] = value
    return json.dumps(d, ensure_ascii=False)


async def _ensure_translations(session, rows: list, lang: str, llm) -> dict:  # noqa: ANN001
    """Translate every question + reply to `lang`, cached in post_comment.{text_tr,reply_tr}.
    Returns {comment_id: {'text': str|None, 'reply': str|None}}. Indonesian (the source) is
    never translated — the raw text is already in the operator's likely reading language."""
    result: dict = {}
    if lang == "id":
        return result
    target = target_for_lang(lang)
    sem = asyncio.Semaphore(_TR_CONCURRENCY)

    async def _one(body: str, cache_raw: str | None, col: str, cid: int):  # noqa: ANN202
        hit = _cached(cache_raw, lang)
        if hit is not None:
            return hit
        if not (body or "").strip():
            return None
        async with sem:
            tr = await translate_text(llm, body, target)
        if tr:
            await session.execute(
                text(f"UPDATE post_comment SET {col}=:v WHERE id=:id"),  # noqa: S608
                {"v": _merge_cache(cache_raw, lang, tr), "id": cid})
        return tr

    tasks = []
    for r in rows:
        result[r.id] = {"text": None, "reply": None}
        tasks.append(("text", r.id, _one(r.text, r.text_tr, "text_tr", r.id)))
        if r.reply_text:
            tasks.append(("reply", r.id, _one(r.reply_text, r.reply_tr, "reply_tr", r.id)))
    done = await asyncio.gather(*(c for _, _, c in tasks), return_exceptions=True)
    for (field, cid, _), out in zip(tasks, done, strict=True):
        if isinstance(out, Exception):
            logger.warning("comment translate failed id=%s: %s", cid, out)
            continue
        result[cid][field] = out
    return result

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


def _comments_panel_html(rows: list, lang: str, multi_branch: bool, trs: dict) -> str:
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

    out = [f'<div class="panel cm-panel"><h2>{title}</h2><p class="muted">{intro}</p>']
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
                f'<div class="cm-item"><div class="cm-lead"><b>@{author}</b> {ctext}'
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
        trs: dict = {}
        if rows and lang != "id":
            from app.adapters.llm.broker import BrokerLLM  # noqa: PLC0415
            trs = await _ensure_translations(session, rows, lang, BrokerLLM())
    multi = not branch_ids or len(branch_ids) > 1
    return HTMLResponse(_comments_panel_html(rows, lang, multi, trs))
