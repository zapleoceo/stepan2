"""Достать телефоны WhatsApp-лидов, заведённых до починки разбора @lid.

WhatsApp прячет адрес чата за `@lid` — приватный идентификатор без номера внутри. Настоящий
номер едет рядом, в `remoteJidAlt`, и разбор его уже читает. Но подставляется он только когда
в тред приходит НОВОЕ сообщение, поэтому лиды, замолчавшие до починки, остались без телефона:
на тредах, активных за двое суток, номер есть у 100%, старше недели — у 9%.

Телефон здесь не украшение. Это единственный ключ, по которому WhatsApp-разговор сшивается с
тем же человеком в Instagram и в CRM. Без него один человек живёт тремя карточками, и
менеджер, глядя в любую из них, не видит остальных двух.

Только дозаполнение: номер пишется лишь там, где его нет. Существующий не трогаем — он мог
прийти из CRM или быть введён руками, и затирать его ответом чужого API нельзя.

    docker compose run --rm --no-deps api python scripts/backfill_wa_phones.py --dry-run
    docker compose run --rm --no-deps api python scripts/backfill_wa_phones.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.adapters.channels.transports import _wa_phone, _wa_records
from app.adapters.db.models import Channel, ChannelThread, Lead
from app.adapters.db.session import session_scope
from app.config import settings
from app.domain.enums import ChannelKind

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill")


async def _chat_index(instance: str) -> dict[str, str]:
    """`@lid` → +номер, по всем сообщениям инстанса.

    Идём через findMessages, а не findChats: номер живёт в ключе СООБЩЕНИЯ (`remoteJidAlt`),
    и список чатов его не несёт — ровно поэтому он и терялся."""
    import httpx  # noqa: PLC0415

    cfg = settings()
    url = cfg.evolution_url.rstrip("/") + f"/chat/findMessages/{instance}"
    headers = {"apikey": cfg.evolution_api_key}
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(url, json={}, headers=headers)
        r.raise_for_status()
        records = _wa_records(r.json())
    index: dict[str, str] = {}
    for rec in records:
        key = rec.get("key") or {}
        jid = str(key.get("remoteJid") or "")
        phone = _wa_phone(str(key.get("remoteJidAlt") or "")) or _wa_phone(jid)
        if jid and phone:
            index.setdefault(jid, phone)
    return index


async def main(dry_run: bool) -> None:
    async with session_scope() as session:
        channels = (await session.execute(
            select(Channel).where(Channel.kind == ChannelKind.WHATSAPP.value)  # type: ignore[arg-type]
        )).scalars().all()

        filled = skipped = collided = 0
        for channel in channels:
            label = channel.handle or f"канал {channel.id}"
            instance = await _instance_of(session, channel)
            if not instance:
                logger.warning("%s: инстанс не настроен, пропускаю", label)
                continue
            try:
                index = await _chat_index(instance)
            except Exception as exc:  # noqa: BLE001 — один инстанс не должен ронять остальные
                logger.warning("%s: не прочитан (%s)", label, str(exc)[:120])
                continue
            logger.info("%s: в WhatsApp известно номеров — %d", label, len(index))

            rows = (await session.execute(
                select(ChannelThread, Lead)
                .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
                .where(
                    ChannelThread.channel_id == channel.id,  # type: ignore[arg-type]
                    Lead.phone_e164.is_(None),  # type: ignore[union-attr]
                )
            )).all()

            for thread, lead in rows:
                phone = index.get(thread.external_thread_id or "")
                if not phone:
                    skipped += 1
                    continue
                # Тот же номер уже у другого лида: это сшивка двух карточек одного человека,
                # а не заполнение пустого поля. Слияние здесь делать нельзя — оно тянет за
                # собой треды, сообщения и стадию, — поэтому только считаем и называем.
                taken = (await session.execute(
                    select(Lead.id).where(Lead.phone_e164 == phone)  # type: ignore[arg-type]
                )).scalars().first()
                if taken is not None:
                    collided += 1
                    logger.info("  лид %s ← %s уже у лида %s (нужна сшивка вручную)",
                                lead.id, phone, taken)
                    continue
                filled += 1
                logger.info("  лид %s ← %s", lead.id, phone)
                if not dry_run:
                    lead.phone_e164 = phone
                    session.add(lead)

        if dry_run:
            logger.info("ПРОБНЫЙ ПРОГОН: заполнили бы %d, дубли %d, без номера %d",
                        filled, collided, skipped)
            return
        await session.commit()
        logger.info("готово: заполнено %d, дубли %d, без номера %d", filled, collided, skipped)


async def _instance_of(session, channel: Channel) -> str | None:  # noqa: ANN001
    """Имя инстанса Evolution — из тех же зашифрованных настроек канала, что читает боевой
    build_port. Выводить его из `handle` («WA Citra») нельзя: там имя человека, а инстансы
    называются по номеру."""
    from app.connectors.session_store import active_session_settings  # noqa: PLC0415

    dump = await active_session_settings(session, channel.id or 0)
    return (dump or {}).get("instance")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="показать, ничего не записывая")
    asyncio.run(main(ap.parse_args().dry_run))
