"""Данные для панели чата — единственное место, которое знает схему.

Раньше эти запросы жили прямо в _routes_chat.py: 67 обращений к сессии в слое, которому
по правилам проекта нельзя ходить в базу вовсе. Роут теперь занимается HTTP и рендером,
а знание о таблицах не растекается по слою представления.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

# Колонки шапки чата. Один список на два роута (смена стадии и смена продукта) — раньше
# они держали два почти одинаковых запроса на 25 колонок, и любая правка схемы требовала
# помнить про оба.
PANEL_FIELDS = (
    "thread_id", "name", "stage", "lead_id", "branch_id", "product_slug", "ig_id",
    "phone", "created_at", "last_in_at", "ig_username", "avatar_url",
    "lead_source", "ad_id", "ad_media_id", "ad_preview_url", "agent_enabled", "is_blocked",
    "follower_count", "following_count", "last_active_at", "lead_seen_at", "tz_offset_h",
    "needs", "needs_tr", "manager_note", "channel_kind",
)
_PANEL_COLUMNS = (
    "ct.id, l.display_name, l.stage, l.id AS lead_id, l.branch_id, ct.product_slug,"
    " ct.external_thread_id, l.phone_e164, l.created_at, ct.last_in_at,"
    " l.ig_username, l.avatar_url,"
    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url,"
    " l.agent_enabled, l.is_blocked,"
    " l.follower_count, l.following_count, l.last_active_at, ct.lead_seen_at, b.tz_offset_h,"
    " l.dossier AS needs, l.needs_tr, l.manager_note, ch.kind AS channel_kind"
)
_PANEL_FROM = (
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " JOIN branch b ON b.id = l.branch_id"
    " LEFT JOIN channel ch ON ch.id = ct.channel_id WHERE ct.id = :tid"
)


class ChatRepo:
    """Чтение и запись по одному треду. Ветку НЕ проверяет — это делает вызывающий через
    _guarded_branch, потому что граница арендатора должна стоять до любой работы."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── чтение ────────────────────────────────────────────────────────────────
    async def panel_row(self, thread_id: int) -> Any:
        return (await self.session.execute(
            text(f"SELECT {_PANEL_COLUMNS}{_PANEL_FROM}"), {"tid": thread_id})).first()

    async def branch_and_tz(self, thread_id: int) -> Any:
        return (await self.session.execute(
            text("SELECT l.branch_id, b.tz_offset_h FROM channel_thread ct"
                 " JOIN lead l ON l.id = ct.lead_id"
                 " JOIN branch b ON b.id = l.branch_id WHERE ct.id = :tid"),
            {"tid": thread_id})).first()

    async def needs_row(self, thread_id: int) -> Any:
        return (await self.session.execute(
            text("SELECT l.branch_id, l.dossier, l.needs_tr, l.id FROM channel_thread ct"
                 " JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"),
            {"tid": thread_id})).first()

    async def lead_flags(self, thread_id: int, column: str) -> Any:
        """id/ветка/один булев флаг лида. `column` — имя из белого списка, не ввод."""
        if column not in ("agent_enabled", "is_blocked"):
            raise ValueError(f"unexpected lead flag: {column}")
        return (await self.session.execute(
            text(f"SELECT l.id, l.branch_id, l.{column} FROM channel_thread ct"  # noqa: S608
                 " JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"),
            {"tid": thread_id})).first()

    async def media_asset(self, asset_id: int) -> Any:
        return (await self.session.execute(
            text("SELECT data, mime, kind, branch_id FROM media_asset WHERE id = :id"),
            {"id": asset_id})).first()

    async def recent_texts(self, thread_id: int, limit: int) -> list[Any]:
        return (await self.session.execute(
            text("SELECT direction, text FROM message WHERE thread_id = :tid AND text <> ''"
                 " ORDER BY occurred_at DESC, id DESC LIMIT :lim"),
            {"tid": thread_id, "lim": limit})).all()

    async def message_direction(self, thread_id: int, message_id: int) -> Any:
        return (await self.session.execute(
            text("SELECT direction FROM message WHERE id=:mid AND thread_id=:tid"),
            {"mid": message_id, "tid": thread_id})).first()

    async def outbox_texts(self, thread_id: int, outbox_id: int) -> Any:
        return (await self.session.execute(
            text("SELECT text, tr_text FROM outbox WHERE id=:oid AND thread_id=:tid"),
            {"oid": outbox_id, "tid": thread_id})).first()

    # ── запись ────────────────────────────────────────────────────────────────
    async def cache_needs_translation(self, lead_id: int, value: str) -> None:
        await self.session.execute(
            text("UPDATE lead SET needs_tr = :v WHERE id = :id"), {"v": value, "id": lead_id})
        await self.session.flush()

    async def set_bot_enabled(self, lead_id: int, enabled: bool) -> None:
        """agent_off_manual помнит, что решение принял ЧЕЛОВЕК: без него ingest._revive_bot
        возвращал бота на первое же входящее и молча отменял выбор менеджера."""
        await self.session.execute(
            text("UPDATE lead SET agent_enabled = :v, agent_off_manual = :m WHERE id = :id"),
            {"v": enabled, "m": not enabled, "id": lead_id})

    async def cancel_queued_bot_messages(self, thread_id: int) -> int:
        """Выключение забирает и то, что бот уже написал, но не успел отправить: между
        генерацией и отправкой проходит одна-три минуты. Статус 'canceled', а не 'failed' —
        ничего не сломалось, повторять нечего, красной кнопки в чате быть не должно."""
        res = await self.session.execute(
            text("UPDATE outbox SET status = 'canceled',"
                 " error = 'bot switched off — human took the thread'"
                 " WHERE thread_id = :t AND status = 'pending' AND source <> 'manager'"),
            {"t": thread_id})
        return res.rowcount or 0

    async def set_blocked(self, lead_id: int, blocked: bool) -> None:
        """Блокировка гасит и бота: заблокированного лида Степан игнорирует полностью."""
        if blocked:
            await self.session.execute(
                text("UPDATE lead SET is_blocked=true, agent_enabled=false WHERE id=:id"),
                {"id": lead_id})
        else:
            await self.session.execute(
                text("UPDATE lead SET is_blocked=false WHERE id=:id"), {"id": lead_id})

    async def clear_context(self, thread_id: int, at: datetime) -> None:
        """Досье стирается вместе с историей: человек, нажавший «очистить», ждёт чистого
        листа, а иначе Степан забывал разговор, но помнил цель и продукт и продолжал
        продавать в пустой чат (тред 452)."""
        await self.session.execute(
            text("UPDATE channel_thread SET context_cleared_at=:t WHERE id=:tid"),
            {"t": at, "tid": thread_id})
        await self.session.execute(
            text("UPDATE lead SET dossier=NULL, needs=NULL"
                 " WHERE id = (SELECT lead_id FROM channel_thread WHERE id=:tid)"),
            {"tid": thread_id})

    async def restore_context(self, thread_id: int) -> None:
        await self.session.execute(
            text("UPDATE channel_thread SET context_cleared_at=NULL WHERE id=:tid"),
            {"tid": thread_id})

    async def set_stage(self, lead_id: int, stage: str) -> None:
        await self.session.execute(
            text("UPDATE lead SET stage = :s WHERE id = :id"), {"s": stage, "id": lead_id})

    async def set_product(self, thread_id: int, slug: str | None) -> None:
        """product_source='manager' — ручной выбор человека, его не перебивает ни реклама,
        ни решение модели."""
        await self.session.execute(
            text("UPDATE channel_thread SET product_slug = :p, product_source = 'manager'"
                 " WHERE id = :tid"), {"p": slug, "tid": thread_id})

    async def set_manager_note(
        self, thread_id: int, note: str | None, who: str | None, now: datetime,
    ) -> None:
        await self.session.execute(
            text("UPDATE lead SET manager_note = :n, manager_note_by = :who,"
                 " manager_note_at = :now"
                 " WHERE id = (SELECT lead_id FROM channel_thread WHERE id = :tid)"),
            {"n": note, "who": who, "now": now, "tid": thread_id})

    async def request_unsend(self, message_id: int) -> None:
        """Исходящее в Instagram отзывается воркером удалений; строка исчезнет после того,
        как отзыв реально прошёл, а не по факту нажатия."""
        await self.session.execute(
            text("UPDATE message SET delete_requested=true WHERE id=:mid"), {"mid": message_id})

    async def delete_message(self, thread_id: int, message_id: int) -> None:
        """last_in_at перематывается по остатку: удаление входящего оставляло его
        протухшим, и список чатов держал старый порядок."""
        await self.session.execute(
            text("DELETE FROM message WHERE id=:mid AND thread_id=:tid"),
            {"mid": message_id, "tid": thread_id})
        await self.session.execute(
            text("UPDATE channel_thread SET last_in_at = (SELECT max(occurred_at) FROM message"
                 " WHERE thread_id=:tid AND direction='in') WHERE id=:tid"),
            {"tid": thread_id})

    async def cancel_pending(self, thread_id: int, outbox_id: int) -> None:
        """И очередное, и упавшее: 'skipped' держит отправщик и не даёт поллингу вернуть."""
        await self.session.execute(
            text("UPDATE outbox SET status='skipped' WHERE id=:oid AND thread_id=:tid"
                 " AND status IN ('pending', 'failed')"), {"oid": outbox_id, "tid": thread_id})

    async def retry_pending(self, thread_id: int, outbox_id: int, now: datetime) -> None:
        await self.session.execute(
            text("UPDATE outbox SET status='pending', error=NULL, scheduled_at=:now"
                 " WHERE id=:oid AND thread_id=:tid AND status='failed'"),
            {"now": now, "oid": outbox_id, "tid": thread_id})

    async def cache_outbox_translation(self, outbox_id: int, value: str) -> None:
        await self.session.execute(
            text("UPDATE outbox SET tr_text=:t WHERE id=:oid"), {"t": value, "oid": outbox_id})
