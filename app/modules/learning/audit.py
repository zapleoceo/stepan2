"""Weekly learning audit — the self-improvement loop's read-only eye.

Collects a week of outbound messages and funnel outcomes, checks every reply against the
deterministic guard set, and ships a compact Russian progress report to the owner's Telegram.
PROPOSE-ONLY by design: it changes nothing itself — the owner reads the report and decides.
Enabled per branch via learning_audit_enabled (default off)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.conversation import guard

if TYPE_CHECKING:
    from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

_CHECKS = (
    ("порядок цены", lambda t: guard.price_order_wrong(t)),
    ("выдуманный доход", lambda t: guard.fabricated_income_figure(t)),
    ("ложная доставка", lambda t: guard.false_delivery_claims(t)),
    ("невозможный оффер", lambda t: guard.impossible_capability_offers(t)),
    ("не тот канал", lambda t: guard.wrong_channel_claims(t)),
    ("WA-доставка", lambda t: guard.whatsapp_delivery_offers(t)),
    ("длительность Booster", lambda t: guard.booster_wrong_duration(t)),
    ("протухшая дата", lambda t: guard.stale_dates(t)),
    ("2+ вопроса", lambda t: ["x"] if t.count("?") >= 2 else []),
    ("стаб-хендофф", lambda t: ["x"] if "pastikan dulu ke tim" in t else []),
    ("меню-заглушка", lambda t: ["x"] if "Biar nggak muter-muter" in t else []),
)


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
        out_msgs = await self._rows(
            "SELECT m.text FROM message m WHERE m.branch_id=:bid AND m.direction='out'"
            " AND m.sent_by='agent' AND m.occurred_at > :cutoff", cutoff=cutoff)
        flag_counts: dict[str, int] = {}
        for (t,) in out_msgs:
            for name, fn in _CHECKS:
                if fn(t or ""):
                    flag_counts[name] = flag_counts.get(name, 0) + 1
        funnel = await self._rows(
            "WITH t AS (SELECT ct.id tid, l.phone_e164,"
            "  (SELECT min(m2.id) FROM message m2 WHERE m2.thread_id=ct.id"
            "     AND m2.direction='out') fo"
            " FROM channel_thread ct JOIN lead l ON l.id=ct.lead_id"
            " WHERE l.branch_id=:bid AND ct.created_at > :cutoff)"
            " SELECT count(*),"
            "  count(*) FILTER (WHERE fo IS NOT NULL AND EXISTS (SELECT 1 FROM message m3"
            "    WHERE m3.thread_id=tid AND m3.direction='in' AND m3.id>fo)),"
            "  count(*) FILTER (WHERE phone_e164 IS NOT NULL AND phone_e164<>'')"
            " FROM t", cutoff=cutoff)
        new_threads, replied, phones = (funnel[0] if funnel else (0, 0, 0))
        react = await self._rows(
            "SELECT count(*) FILTER (WHERE to_stage='nurturing'),"
            "  count(*) FILTER (WHERE from_stage=to_stage)"
            " FROM stage_event WHERE branch_id=:bid AND reason='reactivation'"
            " AND created_at > :cutoff", cutoff=cutoff)
        r_sent, r_suppressed = (react[0] if react else (0, 0))
        reply_rate = round(100 * replied / new_threads) if new_threads else 0
        lines = [
            f"📚 Обучение Степана — аудит за {days} дн.",
            "",
            f"Сообщений проверено: {len(out_msgs)}",
            "Нарушения в отправленном: "
            + (", ".join(f"{k}: {v}" for k, v in sorted(
                flag_counts.items(), key=lambda x: -x[1])) if flag_counts else "0 ✅"),
            "",
            f"Воронка: новых лидов {new_threads}, ответили после 1-го сообщения "
            f"{replied} ({reply_rate}%), телефонов взято {phones}",
            f"Реактивация: заходов {r_sent}, подавлено (отказники/без крючка) {r_suppressed}",
        ]
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
