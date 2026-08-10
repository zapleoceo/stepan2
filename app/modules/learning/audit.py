"""Weekly learning audit — the self-improvement loop's read-only eye.

Collects a week of outbound messages and funnel outcomes, checks every reply against the
deterministic guard set, and ships a compact Russian progress report to the owner's Telegram.
PROPOSE-ONLY by design: it changes nothing itself — the owner reads the report and decides.
Enabled per branch via learning_audit_enabled (default off)."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.conversation import guard

if TYPE_CHECKING:
    from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

# A sentence-final ya/iya/kan/bukan softens a statement, it does not ask for an answer:
# "Kakak tanya soal biaya ya?" is one turn confirming context, not a second question. Counting
# raw '?' put 235 messages in the worst class when 184 of them earn it — a 28% inflation on the
# one number the report tells the owner to act on.
_SOFTENER = re.compile(r"\b(?:ya|iya|kan|bukan)\s*[!.,]*\s*$", re.IGNORECASE)


def _real_questions(text: str) -> int:
    return sum(1 for m in re.finditer(r"\?", text)
               if not _SOFTENER.search(text[max(0, m.start() - 16):m.start()]))


# Every check takes (text, sent_on). Only the date check reads the second argument, and it is
# the whole point of passing it: stale_dates defaults to TODAY, so a week-old message that
# correctly announced "9 Agustus" on the 3rd was counted as stale on the 10th. All 85 flags in
# the first report were that — zero were wrong when they shipped.
_CHECKS = (
    ("порядок цены", lambda t, _d: guard.price_order_wrong(t)),
    ("выдуманный доход", lambda t, _d: guard.fabricated_income_figure(t)),
    ("ложная доставка", lambda t, _d: guard.false_delivery_claims(t)),
    ("невозможный оффер", lambda t, _d: guard.impossible_capability_offers(t)),
    ("не тот канал", lambda t, _d: guard.wrong_channel_claims(t)),
    ("WA-доставка", lambda t, _d: guard.whatsapp_delivery_offers(t)),
    ("длительность Booster", lambda t, _d: guard.booster_wrong_duration(t)),
    ("протухшая дата", lambda t, d: guard.stale_dates(t, today=d)),
    ("2+ вопроса", lambda t, _d: ["x"] if _real_questions(t) >= 2 else []),
    ("стаб-хендофф", lambda t, _d: ["x"] if "pastikan dulu ke tim" in t else []),
    ("меню-заглушка", lambda t, _d: ["x"] if "Biar nggak muter-muter" in t else []),
)


# SQL lives here as named constants, not inline in run(): nothing in the test suite executes
# it (the audit is Postgres-only — FILTER, ~), so a dangling comma before FROM shipped once and
# only surfaced on the live database. test_learning_audit checks these strings for that class.
_Q_MESSAGES = (
    "SELECT m.text, m.occurred_at FROM message m WHERE m.branch_id=:bid"
    " AND m.direction='out' AND m.sent_by='agent' AND m.occurred_at > :cutoff"
)

# A read-only connector is a manager's own phone: its threads are conversations we WATCH, not
# leads Stepan worked. Counting them made 305 of last week's "511 new leads" someone else's
# WhatsApp, and every rate below was divided by that number. Stepan's funnel or nothing.
_Q_FUNNEL = (
    "WITH t AS (SELECT ct.id tid,"
    "  (SELECT min(m2.id) FROM message m2 WHERE m2.thread_id=ct.id"
    "     AND m2.direction='out') fo"
    " FROM channel_thread ct JOIN lead l ON l.id=ct.lead_id"
    "   JOIN channel c ON c.id=ct.channel_id"
    " WHERE l.branch_id=:bid AND ct.created_at > :cutoff AND NOT c.manager_phone)"
    " SELECT count(*),"
    "  count(*) FILTER (WHERE fo IS NOT NULL AND EXISTS (SELECT 1 FROM message m3"
    "    WHERE m3.thread_id=tid AND m3.direction='in' AND m3.id>fo))"
    " FROM t"
)

# Phones are an EVENT, not a property of this week's cohort. Counting leads created in the
# window who happen to carry a number credited Stepan for numbers the WhatsApp connector hands
# over for free, and gave him nothing for a number typed today by a lead who arrived in June.
_Q_PHONES = (
    "SELECT count(DISTINCT ct.lead_id) FROM message m"
    " JOIN channel_thread ct ON ct.id=m.thread_id"
    " JOIN channel c ON c.id=ct.channel_id"
    " WHERE m.branch_id=:bid AND m.direction='in' AND m.occurred_at > :cutoff"
    "   AND NOT c.manager_phone"
    r"   AND m.text ~ '(\+?62|0)[ .\-]?8[1-9][0-9 ().\-]{6,}'"
)

# Attempts and suppressions have to nest. Filtering attempts on to_stage='nurturing' put every
# nurturing→nurturing row in BOTH counters, so "356 attempts, 75 suppressed" described 396 rows.
_Q_REACTIVATION = (
    "SELECT count(*), count(*) FILTER (WHERE from_stage=to_stage)"
    " FROM stage_event WHERE branch_id=:bid AND reason='reactivation'"
    " AND created_at > :cutoff"
)

_Q_WATCHED = (
    "SELECT count(*) FROM channel_thread ct JOIN lead l ON l.id=ct.lead_id"
    " JOIN channel c ON c.id=ct.channel_id"
    " WHERE l.branch_id=:bid AND ct.created_at > :cutoff AND c.manager_phone"
)

QUERIES = (_Q_MESSAGES, _Q_FUNNEL, _Q_PHONES, _Q_REACTIVATION, _Q_WATCHED)


class LearningAudit:
    def __init__(self, session: AsyncSession, branch_id: int,
                 notifier: NotifierPort | None) -> None:
        self.session = session
        self.branch_id = branch_id
        self.notifier = notifier

    async def _rows(self, q: str, **p) -> list:
        return (await self.session.execute(text(q), {"bid": self.branch_id, **p})).all()

    async def run(self, days: int = 7) -> str:
        """Build the weekly report text (and send it if a notifier is wired)."""
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        out_msgs = await self._rows(_Q_MESSAGES, cutoff=cutoff)
        flag_counts: dict[str, int] = {}
        for t, sent_on in out_msgs:
            for name, fn in _CHECKS:
                if fn(t or "", sent_on.date()):
                    flag_counts[name] = flag_counts.get(name, 0) + 1
        funnel = await self._rows(_Q_FUNNEL, cutoff=cutoff)
        new_threads, replied = (funnel[0] if funnel else (0, 0))
        phones = (await self._rows(_Q_PHONES, cutoff=cutoff))[0][0]
        react = await self._rows(_Q_REACTIVATION, cutoff=cutoff)
        r_sent, r_suppressed = (react[0] if react else (0, 0))
        watched = (await self._rows(_Q_WATCHED, cutoff=cutoff))[0][0]
        reply_rate = round(100 * replied / new_threads) if new_threads else 0
        lines = [
            f"📚 Обучение Степана — аудит за {days} дн.",
            "",
            f"Сообщений проверено: {len(out_msgs)}",
            "Нарушения в отправленном: "
            + (", ".join(f"{k}: {v}" for k, v in sorted(
                flag_counts.items(), key=lambda x: -x[1])) if flag_counts else "0 ✅"),
            "",
            f"Воронка Степана: новых лидов {new_threads}, ответили после 1-го сообщения "
            f"{replied} ({reply_rate}%), телефонов взято {phones}",
            f"Реактивация: заходов {r_sent}, из них без движения {r_suppressed}",
        ]
        if watched:
            lines.append(f"Чаты менеджеров (только чтение, вне воронки): {watched}")
        if flag_counts:
            worst = max(flag_counts, key=flag_counts.get)  # type: ignore[arg-type]
            lines += ["", f"Худший класс недели: «{worst}» — предлагаю правку на след. цикле."]
        report = "\n".join(lines)
        if self.notifier is not None:
            try:
                await self.notifier.send(text=report)
            except Exception:
                logger.warning("learning audit: TG send failed", exc_info=True)
        return report
