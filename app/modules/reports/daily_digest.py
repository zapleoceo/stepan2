"""Daily dialogue digest — one self-contained .md for offline/AI analysis.

Answers "how is Stepan actually talking, and where do sales leak?" in a single file:
the conversation-logic changes already shipped (so an analyst doesn't re-propose them),
the funnel numbers, the needs cloud, then the raw dialogs with what the bot understood.
Read-only: no IG writes, no lead mutation.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api._changelog import RELEASES
from app.modules.conversation.signals import AD_TEMPLATE_RE, is_auto_reply

_PRICE_RE = re.compile(r"\brp\.?\s?\d[\d.,]*|\d[\d.,]*\s?(?:ribu|juta|rb\b)", re.IGNORECASE)
_CLOSE_RE = re.compile(
    r"amankan\s+(tempat|seat|slot)|dp\s*(rp\s*)?500|nomor\s+wa|rekening|transfer|reservasi",
    re.IGNORECASE)
_CHANGES_SHOWN = 8
_CLOUD_TOP = 15


def _lead_spoke(texts: list[str]) -> bool:
    """The lead typed something of their own — not the ad's prefill, not their auto-responder."""
    return any(t.strip() and not AD_TEMPLATE_RE.match(t.strip()) and not is_auto_reply(t)
               for t in texts)


def _fmt_needs(raw: str | None) -> str:
    """The needs profile as the bot stored it → a short human line."""
    try:
        n = json.loads(raw or "{}")
    except ValueError:
        return "—"
    parts = [f"{label}: {', '.join(v)}" for label, key in
             (("цели", "jobs"), ("боли", "pains"), ("выгоды", "gains"))
             if (v := n.get(key))]
    return " · ".join(parts) or "— (ничего не выявлено)"


async def _changes_section() -> str:
    out = ["## 1. Что уже изменено в логике общения",
           "",
           "_Это уже внедрено и работает — не предлагать повторно._",
           ""]
    for r in RELEASES[:_CHANGES_SHOWN]:
        out.append(f"### {r['version']} — {r['title']} ({r['date']})")
        out.append(r["blurb"])
        out.append("")
    return "\n".join(out)


async def _cloud_section(session: AsyncSession, branch_id: int) -> str:
    rows = (await session.execute(text(
        "SELECT e.kind, e.label, COUNT(DISTINCT t.lead_id) c"
        " FROM lead_need_tag t JOIN need_entity e ON e.id = t.entity_id"
        " WHERE t.branch_id = :b GROUP BY e.kind, e.label ORDER BY c DESC"),
        {"b": branch_id})).all()
    by_kind: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for kind, label, c in rows:
        by_kind[kind].append((label, c))
    out = ["## 3. Облако потребностей (сколько лидов на метку)", ""]
    for kind, title in (("pains", "Боли"), ("jobs", "Цели"), ("gains", "Выгоды")):
        items = by_kind.get(kind, [])[:_CLOUD_TOP]
        out.append(f"### {title}")
        out.extend([f"- {label} — {c}" for label, c in items] or ["- (пусто)"])
        out.append("")
    return "\n".join(out)


async def _threads(session: AsyncSession, branch_id: int, limit: int):  # noqa: ANN202
    rows = (await session.execute(text(
        "SELECT ct.id, l.stage, l.needs, ct.product_slug"
        " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
        " WHERE l.branch_id = :b AND ct.last_out_at IS NOT NULL"
        " ORDER BY ct.last_out_at DESC LIMIT :n"), {"b": branch_id, "n": limit})).all()
    meta = {r[0]: (str(r[1]), r[2], r[3]) for r in rows}
    if not meta:
        return {}, {}
    msgs = (await session.execute(text(
        "SELECT thread_id, direction, text FROM message"
        " WHERE thread_id = ANY(:ids) ORDER BY thread_id, id"), {"ids": list(meta)})).all()
    dialogs: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for tid, d, t in msgs:
        dialogs[tid].append((d, t or ""))
    return meta, dialogs


def _funnel_section(meta: dict, dialogs: dict) -> str:
    f: dict[str, int] = defaultdict(int)
    for tid, (stage, needs, _slug) in meta.items():
        f["total"] += 1
        ins = [t for d, t in dialogs.get(tid, []) if d == "in"]
        outs = [t for d, t in dialogs.get(tid, []) if d == "out"]
        if not _lead_spoke(ins):
            f["silent_clicker"] += 1
            continue
        f["engaged"] += 1
        if '"pains":' in (needs or "") and '"pains": []' not in (needs or ""):
            f["pain_captured"] += 1
        if any(_PRICE_RE.search(o) for o in outs):
            f["price_given"] += 1
        if any(_CLOSE_RE.search(o) for o in outs):
            f["close_attempted"] += 1
        if stage in ("ready", "handed_off", "manager"):
            f["advanced"] += 1
    t = max(f["total"], 1)
    rows = [("Всего диалогов", f["total"]), ("Молча ушли после клика", f["silent_clicker"]),
            ("Заговорили своими словами", f["engaged"]), ("Боль выявлена", f["pain_captured"]),
            ("Цена названа", f["price_given"]), ("Попытка закрытия", f["close_attempted"]),
            ("Дошло до ready/handoff/manager", f["advanced"])]
    out = ["## 2. Воронка по этой выборке", "", "| Шаг | Кол-во | % |", "|---|---|---|"]
    out.extend(f"| {name} | {v} | {round(100 * v / t)}% |" for name, v in rows)
    out.append("")
    return "\n".join(out)


def _dialogs_section(meta: dict, dialogs: dict) -> str:
    out = ["## 4. Диалоги — как Степан общается", ""]
    for tid, (stage, needs, slug) in meta.items():
        seq = dialogs.get(tid, [])
        outs = [t for d, t in seq if d == "out"]
        priced = "да" if any(_PRICE_RE.search(o) for o in outs) else "нет"
        closed = "да" if any(_CLOSE_RE.search(o) for o in outs) else "нет"
        out.append(f"### Чат #{tid} · стадия: {stage} · продукт: {slug or '—'}")
        out.append(f"**Что бот понял:** {_fmt_needs(needs)}")
        out.append(f"**Цена названа:** {priced} · **Попытка закрытия:** {closed}")
        out.append("")
        for d, t in seq:
            who = "ЛИД" if d == "in" else "СТЕПАН"
            body = (t or "").replace("\n", " / ").replace("|||", " ⏎ ")
            out.append(f"- **{who}:** {body}")
        out.append("")
    return "\n".join(out)


async def build_digest(session: AsyncSession, branch_id: int, limit: int = 300) -> str:
    """The whole digest as markdown. `limit` = how many most-recent threads to include."""
    meta, dialogs = await _threads(session, branch_id, limit)
    header = (f"# Stepan — выгрузка диалогов, филиал {branch_id}\n\n"
              f"Дата: {date.today().isoformat()} · диалогов в файле: {len(meta)}\n\n"
              "Файл для анализа: как Степан ведёт диалог и где теряются продажи.\n\n")
    return "\n".join([
        header,
        await _changes_section(),
        _funnel_section(meta, dialogs),
        await _cloud_section(session, branch_id),
        _dialogs_section(meta, dialogs),
    ])
