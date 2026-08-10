"""Добор входящих, которых не довёз колбек.

Ретраев у колбека нет — их собственная спека говорит, что ошибки только логируются. Значит
сообщение, пришедшее в момент нашего перезапуска, пропадает: у них оно есть, у нас его не
было, и никто не узнает. Тесты закрепляют три способа, которыми этот добор мог бы тихо
перестать быть починкой: не прочитать хвост усечённого ответа, посчитать спасённым то, что
и так пришло, и упасть вместо того, чтобы промолчать.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import select  # noqa: E402

from app.adapters.db.models import SenderInbound  # noqa: E402
from app.adapters.sender_mcp import kiev_window  # noqa: E402
from app.modules.sender import catchup  # noqa: E402
from app.modules.sender.tenant import SenderTenant  # noqa: E402

TENANT = SenderTenant(project="crm", project_id="6", branch_id="435")
NOW = datetime(2026, 8, 7, 10, 30, 0)


def _msg(external_id: str, text: str = "halo") -> dict:
    return {"id": "1", "external_id": external_id, "from": "6281234567890",
            "message": text, "chanel": "whats-app", "project_id": "6",
            "branch_id": "435", "conversation_id": "6281234567890", "chat_id": "9"}


class _Mcp:
    """Заглушка транспорта: отдаёт заготовленные ответы и помнит запрошенные окна."""

    def __init__(self, pages: list[tuple[list[dict], bool]] | None = None,
                 configured: bool = True) -> None:
        self.pages = list(pages or [([], False)])
        self.configured = configured
        self.windows: list[tuple[datetime, datetime]] = []

    async def inbound_since(self, args: dict, since: datetime,
                            until: datetime) -> tuple[list[dict], bool]:
        self.windows.append((since, until))
        return self.pages.pop(0) if self.pages else ([], False)


def test_the_window_goes_out_in_kiev_time_not_ours() -> None:
    """sender трактует dateStart/dateEnd в киевском времени — не в UTC и не в джакартском
    (Виктор, 05.08.2026). Ошибка здесь тихая: запрос уйдёт за период, в который никто не
    писал, вернёт пусто, и это будет выглядеть как «ничего не потеряли»."""
    start, end = kiev_window(datetime(2026, 8, 7, 3, 0, 0), datetime(2026, 8, 7, 4, 0, 0))

    assert (start, end) == ("2026-08-07 06:00:00", "2026-08-07 07:00:00")


async def test_a_message_the_callback_missed_is_stored(db_session) -> None:  # noqa: ANN001
    mcp = _Mcp([([_msg("wamid.MISSED", "berapa harganya?")], False)])

    assert await catchup.sweep(db_session, mcp, TENANT, now=NOW) == 1

    rows = (await db_session.execute(select(SenderInbound))).scalars().all()
    assert [(r.external_id, r.arrived_via) for r in rows] == [("wamid.MISSED", "catchup")]


async def test_what_the_callback_already_delivered_is_not_counted_twice(db_session) -> None:  # noqa: ANN001
    """Каждый проход намеренно перечитывает territory прошлого, так что перекрытие — норма.
    Считать его спасением значило бы выдумывать сбой на каждом запуске."""
    from app.modules.sender.inbound import CALLBACK, store, to_row

    await store(db_session, to_row(_msg("wamid.SEEN"), arrived_via=CALLBACK))
    mcp = _Mcp([([_msg("wamid.SEEN"), _msg("wamid.NEW")], False)])

    assert await catchup.sweep(db_session, mcp, TENANT, now=NOW) == 1


async def test_a_truncated_answer_is_split_until_it_fits(db_session) -> None:  # noqa: ANN001
    """`truncated=true` значит, что хвост не поместился. Взять что дали — потерять сообщения
    ровно тогда, когда их много."""
    mcp = _Mcp([
        ([_msg("wamid.A")], True),      # окно целиком — обрезано
        ([_msg("wamid.A")], False),     # первая половина
        ([_msg("wamid.B")], False),     # вторая половина
    ])

    rescued = await catchup.sweep(db_session, mcp, TENANT, now=NOW, lookback_min=30)

    assert rescued == 2, "хвост усечённого окна должен быть дочитан"
    first, second, third = mcp.windows
    assert first == (NOW - timedelta(minutes=30), NOW)
    assert second[1] == third[0] == NOW - timedelta(minutes=15), "окно делится пополам"


async def test_an_unconfigured_sweep_does_nothing_instead_of_guessing(db_session) -> None:  # noqa: ANN001
    """Токена sender ещё нет. Пока его нет, добор обязан стоять на месте."""
    mcp = _Mcp(configured=False)

    assert await catchup.sweep(db_session, mcp, TENANT, now=NOW) == 0
    assert mcp.windows == [], "ненастроенный добор не должен никого звать"

    blank = SenderTenant(project="", project_id="", branch_id="")
    assert await catchup.sweep(db_session, _Mcp(), blank, now=NOW) == 0


async def test_a_rescue_is_logged_loudly(db_session, monkeypatch) -> None:  # noqa: ANN001
    """Колбек, тихо теряющий трафик, не виден ничем другим — только счётчиком спасённых."""
    seen: list[str] = []
    monkeypatch.setattr(catchup.logger, "warning",
                        lambda msg, *a: seen.append(msg % a if a else msg))

    await catchup.sweep(db_session, _Mcp([([_msg("wamid.X")], False)]), TENANT, now=NOW)

    assert seen
    assert "rescued 1" in seen[-1]
