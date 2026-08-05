"""The proactive half of the comments panel: what we wrote under other people's posts.

Rendered above the reactive list, and it shows the refusals as well as the sends. That is the
whole reason the rows exist — the judge's threshold is the only real knob on this mission, and
an operator who sees only what went out has no way to tell a strict judge from an idle one.
"""
from __future__ import annotations

import html as _h

from app.modules.comments.translate import cached

OUTBOUND_Q = (
    "SELECT oc.id, oc.media_permalink, oc.media_caption, oc.author_username, oc.status,"
    " oc.skip_reason, oc.text, oc.text_tr, oc.handled_at, oc.created_at, b.name AS branch_name"
    " FROM outbound_comment oc JOIN branch b ON b.id = oc.branch_id"
    " {where}"
    " ORDER BY COALESCE(oc.handled_at, oc.created_at) DESC LIMIT 60"
)

_STATUS = {
    "sent":    ("💬", {"ru": "Отправлено", "en": "Posted", "id": "Terkirim"}),
    "skipped": ("➖", {"ru": "Решили не писать", "en": "Passed", "id": "Dilewati"}),
    "error":   ("⚠️", {"ru": "Ошибка отправки", "en": "Send error", "id": "Gagal kirim"}),
    "pending": ("⏳", {"ru": "В работе", "en": "In progress", "id": "Diproses"}),
}


def _lbl(d: dict, lang: str) -> str:
    return d.get(lang, d.get("en", ""))


def outbound_block(rows: list, lang: str, multi_branch: bool, stamp) -> str:  # noqa: ANN001
    """The whole section, or "" when the mission has never run on the selected branches.

    An empty block is worse than none: a heading over nothing reads as broken, and this
    mission is off by default on every branch."""
    if not rows:
        return ""
    sent = sum(1 for r in rows if r.status == "sent")
    title = _h.escape(_lbl({
        "ru": "Под чужими постами", "en": "Under other people's posts",
        "id": "Di postingan orang lain"}, lang))
    hint = _h.escape(_lbl({
        "ru": f"Бот сам заходит к тем, кто нам уже писал. Написали {sent} из "
              f"{len(rows)} просмотренных постов — остальные не подошли.",
        "en": f"The bot visits people who already wrote to us. Commented on {sent} of "
              f"{len(rows)} posts it looked at — the rest did not fit.",
        "id": f"Bot mengunjungi orang yang sudah menulis ke kami. Berkomentar di {sent} dari "
              f"{len(rows)} postingan."}, lang))
    out = [f'<div class="ob-block"><div class="ob-h">{title}</div>'
           f'<div class="ob-hint muted">{hint}</div>']
    for r in rows:
        out.append(_row(r, lang, multi_branch, stamp))
    out.append("</div>")
    return "".join(out)


def _row(r, lang: str, multi_branch: bool, stamp) -> str:  # noqa: ANN001
    icon, sd = _STATUS.get(r.status, ("•", {"en": r.status}))
    when = r.handled_at or r.created_at
    author = _h.escape(r.author_username or "—")
    cap = _h.escape((r.media_caption or "")[:110])
    link = r.media_permalink or "#"
    br = f' · {_h.escape(r.branch_name)}' if multi_branch else ""
    if r.status == "sent" and r.text:
        tr = cached(r.text_tr, lang)
        body = (f'<div class="ob-text">{_h.escape(r.text)}'
                + (f'<div class="cm-tr">{_h.escape(tr)}</div>' if tr else "")
                + "</div>")
    else:
        # The reason is what makes this row useful. A skipped row with no reason shown is a
        # row nobody can learn anything from.
        body = (f'<div class="cm-skip muted">{_h.escape(r.skip_reason or "")}</div>'
                if r.skip_reason else "")
    return (
        f'<div class="ob-item"><div class="cm-lead">'
        f'<b>@{author}</b> <span class="cm-when" title="{_h.escape(stamp.full(when))}">'
        f'{_h.escape(stamp.ago(when, lang))}</span> '
        f'<a href="{_h.escape(link)}" target="_blank" rel="noopener" class="ob-cap">{cap}</a>'
        f'</div>{body}'
        f'<div class="cm-status">{icon} {_h.escape(_lbl(sd, lang))}{br}</div></div>'
    )


STYLE = (
    ".ob-block{margin:6px 0 14px;border:1px solid var(--line,#2a2f3d);border-radius:10px;"
    "overflow:hidden}"
    ".ob-h{padding:10px 12px;background:var(--card2,#1d212d);font-weight:600}"
    ".ob-hint{padding:0 12px 8px;font-size:12.5px}"
    ".ob-item{padding:10px 12px;border-top:1px solid var(--line,#2a2f3d)}"
    # The caption is context, not the content — the line we wrote is what the operator reads.
    ".ob-cap{color:var(--muted,#8b93a7);text-decoration:none;font-size:12.5px}"
    ".ob-text{margin:4px 0 4px 14px;padding:6px 10px;"
    "border-left:2px solid var(--accent,#4f8cff);background:var(--card,#171a23);"
    "border-radius:0 8px 8px 0}"
)
