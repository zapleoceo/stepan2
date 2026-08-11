"""Коннектор CRM: приём из своей таблицы и ответ через чужой инструмент.

Свойства, ради которых этот коннектор вообще отдельный, и все легко потерять при правке:
входящее менеджера не должно выглядеть репликой лида, «принято в очередь» не должно
записываться как «доставлено», номер обязан приходить в той же форме, что у остальных
каналов, а чужой филиал — не попадать в выборку.
"""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import select  # noqa: E402

from app.adapters.channels.crm_sender import (  # noqa: E402
    NO_CONVERSATION,
    NOT_CONFIGURED,
    REPLIES_OFF,
    CrmSenderAdapter,
)
from app.adapters.db.models import SenderInbound  # noqa: E402
from app.adapters.sender_mcp import SendOutcome  # noqa: E402
from app.domain.enums import SessionStatus  # noqa: E402
from app.modules.sender.tenant import SenderTenant  # noqa: E402

TENANT = SenderTenant(project="crm", project_id="6", branch_id="435")


class _Mcp:
    """Транспорт-заглушка: помнит вызов и отдаёт заданный исход."""

    def __init__(self, outcome: SendOutcome | None = None, configured: bool = True) -> None:
        self.outcome = outcome or SendOutcome(accepted=True, ref="wamid.OUT")
        self.configured = configured
        self.calls: list[dict] = []

    async def send_text(self, args: dict) -> SendOutcome:
        self.calls.append(args)
        return self.outcome


def _row(**kw) -> SenderInbound:
    base = {
        "external_id": "wamid.1", "conversation_id": "6281234567890", "chat_id": "987",
        "phone": "6281234567890", "text": "halo", "direction": "in",
        "received_at": datetime(2026, 8, 7, 10, 0, 0), "arrived_via": "callback",
        # Филиал и мессенджер приходят в каждом колбеке и оба решают судьбу строки: по
        # первому она попадает в выборку, по второму — считать ли `phone` телефоном.
        "branch_ref": "435", "channel_name": "whats-app",
    }
    return SenderInbound(**{**base, **kw})


async def test_a_stored_inbound_becomes_a_message_and_is_marked_done(db_session) -> None:  # noqa: ANN001
    db_session.add(_row(text="berapa harganya?"))
    await db_session.flush()
    a = CrmSenderAdapter(db_session, _Mcp(), TENANT, replies_enabled=True)

    first = await a.fetch_inbound()

    assert [m.text for m in first] == ["berapa harganya?"]
    assert first[0].external_thread_id == "6281234567890"
    assert first[0].lead_phone == "+6281234567890"
    # Второй проход не должен принести то же снова: конвейер отсеет дубль по external_id,
    # но успеет добавить лишний ход диалога.
    assert await a.fetch_inbound() == []


async def test_the_phone_arrives_in_the_form_the_merge_key_is_stored_in(db_session) -> None:  # noqa: ANN001
    """Ключ склейки лидов — `phone_e164`, и ищется он ТОЧНЫМ равенством.

    Живые лиды Джакарты лежат как `+62…` (285 из 288 карточек филиала). Колбек присылает
    wa-id голым: `6289689515687`. Отдай его как есть — `by_phone` не найдёт `+6289689515687`,
    человек заведётся второй карточкой, и WhatsApp не попадёт в общий тред с его же
    инстаграмом. Ради этого коннектор и подключали.
    """
    db_session.add(_row(phone="6289689515687", conversation_id="6289689515687"))
    await db_session.flush()

    got = await CrmSenderAdapter(db_session, _Mcp(), TENANT).fetch_inbound()

    assert got[0].lead_phone == "+6289689515687"


async def test_a_foreign_number_keeps_its_own_country(db_session) -> None:  # noqa: ANN001
    """wa-id — это уже международный номер, и штамповать ему код филиала нельзя.

    Через филиал Индонезии пишут не только индонезийцы (украинский +380 — живой случай,
    тред 452). Прогони такой номер через обычную канонизацию «местного» — получится
    `+62380684592151`, номер несуществующего человека.
    """
    db_session.add(_row(phone="380684592151", conversation_id="380684592151"))
    await db_session.flush()

    got = await CrmSenderAdapter(db_session, _Mcp(), TENANT).fetch_inbound()

    assert got[0].lead_phone == "+380684592151"


async def test_a_telegram_id_is_never_mistaken_for_a_phone(db_session) -> None:  # noqa: ANN001
    """У телеграма адрес — не номер, и выдать его за номер значит склеить чужих людей.

    Через sender филиала идут whats-app, telegram, viber и smsviber. У телеграм-входящих
    поле `from` приходит пустым, а id живёт в conversation_id — но правило должно держаться
    на канале, а не на удаче: приедь id в `from`, из 504412830 вышел бы «+504412830», и
    первый же индонезиец с похожим номером стал бы тем же лидом.
    """
    db_session.add(_row(external_id="tg.1", channel_name="telegram",
                        phone="504412830", conversation_id="504412830"))
    await db_session.flush()

    got = await CrmSenderAdapter(db_session, _Mcp(), TENANT).fetch_inbound()

    assert [m.text for m in got] == ["halo"], "телеграм всё равно должен доехать"
    assert got[0].lead_phone is None, "но без выдуманного телефона"


async def test_another_branch_inbound_is_left_alone(db_session) -> None:  # noqa: ANN001
    """Таблица одна на установку, канал принадлежит филиалу.

    Без ограничения первый же опросивший филиал забрал бы чужие входящие СЕБЕ и пометил бы
    их обработанными — настоящий адресат не увидел бы их никогда, и молча.
    """
    db_session.add(_row(external_id="wamid.OURS"))
    db_session.add(_row(external_id="wamid.THEIRS", branch_ref="777",
                        conversation_id="60123456789", phone="60123456789"))
    await db_session.flush()

    got = await CrmSenderAdapter(db_session, _Mcp(), TENANT).fetch_inbound()

    assert [m.external_id for m in got] == ["wamid.OURS"]
    theirs = (await db_session.execute(
        select(SenderInbound).where(SenderInbound.external_id == "wamid.THEIRS")
    )).scalars().first()
    assert theirs.processed_at is None, "чужую строку нельзя даже помечать разобранной"


async def test_a_managers_own_message_is_never_served_as_a_lead_turn(db_session) -> None:  # noqa: ANN001
    """Сообщение менеджера доезжает, но помечено исходящим — репликой лида не станет.

    Направление разбирает ingest: входящее — ход лида, исходящее — `_store_outgoing`,
    который двигает last_out_at и повода ответить не даёт. Раньше адаптер отсекал
    исходящие у себя, и получалось хуже: строки оставались в таблице навсегда, а тред
    выглядел неотвеченным — Степан писал поверх менеджера (11.08, лид 6289689515687:
    менеджер в 09:27, Степан в 09:29).

    Имя с исходящего на карточку лида не переносится: там менеджер, а не собеседник.
    """
    db_session.add(_row(external_id="wamid.OUT1", direction="out", text="saya manager",
                        from_name="Citra"))
    db_session.add(_row(external_id="wamid.IN1", direction="in", text="halo kak",
                        from_name="Juan"))
    await db_session.flush()

    got = await CrmSenderAdapter(db_session, _Mcp(), TENANT).fetch_inbound()

    assert [(m.direction, m.text) for m in got] == [
        ("out", "saya manager"), ("in", "halo kak")]
    assert [m.sender_name for m in got] == [None, "Juan"]


async def test_a_row_without_a_conversation_is_retired_not_looped(db_session) -> None:  # noqa: ANN001
    """Отвечать некуда, но и крутиться вечно строка не должна."""
    db_session.add(_row(external_id="wamid.NOCONV", conversation_id=None))
    await db_session.flush()
    a = CrmSenderAdapter(db_session, _Mcp(), TENANT, replies_enabled=True)

    assert await a.fetch_inbound() == []

    row = (await db_session.execute(select(SenderInbound))).scalars().first()
    assert row.processed_at is not None


async def test_the_reply_is_addressed_from_the_conversation_we_stored(db_session) -> None:  # noqa: ANN001
    """Их инструмент адресует не одним идентификатором, а набором. Все они приходили в
    колбеке — берём из последней строки разговора, чтобы ответ ушёл туда, где лид писал."""
    db_session.add(_row(external_id="wamid.OLD", chat_id="111", sender_user_id="4521"))
    db_session.add(_row(external_id="wamid.NEW", chat_id="987", sender_user_id="4521"))
    await db_session.flush()
    mcp = _Mcp()

    a = CrmSenderAdapter(db_session, mcp, TENANT, replies_enabled=True)
    res = await a.send_text("6281234567890", "halo")

    assert res.ok
    assert res.external_message_id == "wamid.OUT"
    # Числа — числами: их схема объявляет branchId, id и userId целыми и отвергает
    # весь запрос при строке.
    assert mcp.calls == [{
        "project": "crm", "branchId": 435, "text": "halo",
        "id": 987, "conversationId": "6281234567890", "userId": 4521,
    }]


async def test_reading_is_on_long_before_answering_is(db_session) -> None:  # noqa: ANN001
    """`sender_enabled` выключен — входящие всё равно разбираются, ответы не уходят.

    Это не удобство, а единственный способ включить канал безопасно: в этом WhatsApp сидят
    живые менеджеры филиала, и переписка должна попасть в общий тред лида ЗАДОЛГО до того,
    как решено, кто здесь отвечает. Общий тумблер бота для этого не годится — он один на
    филиал, а Instagram того же филиала работает и глушить его нельзя.

    Настройка объявлена в схеме с самого начала, но не читалась ничем: тумблер был
    нарисован и не подключён.
    """
    db_session.add(_row())
    await db_session.flush()
    a = CrmSenderAdapter(db_session, _Mcp(), TENANT, replies_enabled=False)

    assert [m.text for m in await a.fetch_inbound()] == ["halo"]

    res = await a.send_text("6281234567890", "halo")
    assert not res.ok
    assert res.error == REPLIES_OFF


async def test_a_thread_we_never_received_is_refused_clearly(db_session) -> None:  # noqa: ANN001
    a = CrmSenderAdapter(db_session, _Mcp(), TENANT, replies_enabled=True)
    res = await a.send_text("нет-такого", "halo")

    assert not res.ok
    assert res.error == NO_CONVERSATION


async def test_without_a_token_the_connector_refuses_rather_than_guesses(db_session) -> None:  # noqa: ANN001
    """Токен sender ещё не выдан. Пока его нет, отправка обязана молчать — не падать и не
    выдумывать адрес."""
    db_session.add(_row())
    await db_session.flush()

    a = CrmSenderAdapter(db_session, _Mcp(configured=False), TENANT, replies_enabled=True)
    res = await a.send_text("6281234567890", "halo")

    assert not res.ok
    assert res.error == NOT_CONFIGURED
    assert await a.session_status() == SessionStatus.PENDING


async def test_a_transport_failure_is_reported_not_swallowed(db_session) -> None:  # noqa: ANN001
    db_session.add(_row())
    await db_session.flush()
    mcp = _Mcp(SendOutcome(accepted=False, error="connection reset"))

    a = CrmSenderAdapter(db_session, mcp, TENANT, replies_enabled=True)
    res = await a.send_text("6281234567890", "halo")

    assert not res.ok
    assert res.error == "connection reset"


async def test_the_switch_in_settings_is_the_one_the_port_actually_obeys(db_session) -> None:  # noqa: ANN001
    """Настройка филиала должна доезжать до порта, а не только существовать в схеме.

    Проверять адаптер напрямую мало: тумблер и был объявлен в схеме с самого начала, но не
    читался ничем — сборка порта передавала бы что угодно, и тест на адаптере остался бы
    зелёным. Здесь проверяется именно проводка.
    """
    from app.adapters.db.models import AppSetting, Branch, Channel  # noqa: PLC0415
    from app.connectors.crm_sender import build_port  # noqa: PLC0415
    from app.modules.settings.service import invalidate  # noqa: PLC0415

    db_session.add(Branch(id=1, name="Indonesia", lang="id", tz_offset_h=7, is_active=True))
    ch = Channel(branch_id=1, kind="crm", is_active=True)
    db_session.add(ch)
    await db_session.flush()
    invalidate(1)

    off = await build_port(db_session, ch)
    assert off.replies_enabled is False, "по умолчанию канал читает, но не отвечает"

    db_session.add(AppSetting(branch_id=1, key="sender_enabled", value="true"))
    await db_session.flush()
    invalidate(1)  # ровно то, что делает админка после записи: у настроек кэш на 30 с

    on = await build_port(db_session, ch)
    assert on.replies_enabled is True


def test_the_sender_keys_belong_to_this_connector_and_no_other() -> None:
    """Префикс объявляет, ЧЬИ это ключи, — и должен совпадать с настоящими именами.

    app_setting одна таблица на всех, и редактор канала прячет чужие поля по этому префиксу.
    Он был объявлен как `sender.` — с точкой, тогда как ключи зовутся `sender_mcp_url` и
    `sender_enabled`. Не совпав ни с одним ключом, префикс не назначал владельца, и настройки
    переписки через CRM показывались в редакторе КАЖДОГО канала — инстаграма, вотсапа, сайта.
    Ошибка молчаливая: лишние поля выглядят как задумка.
    """
    from app.connectors.crm_sender import SPEC  # noqa: PLC0415
    from app.modules.settings.schema_sender import SENDER_SECTION  # noqa: PLC0415

    assert SPEC.settings_prefixes, "без префикса ключи ничьи"
    orphans = [f.key for f in SENDER_SECTION.fields
               if not f.key.startswith(SPEC.settings_prefixes)]
    assert orphans == [], f"эти ключи не попадают под префикс коннектора: {orphans}"


def test_the_connector_declares_that_it_does_not_confirm_delivery() -> None:
    """Их conversation/send кладёт в очередь и отвечает сразу. Объяви коннектор обратное —
    outbox записал бы «отправлено» вместо «queued», и передача менеджеру при сбое доставки
    не сработала бы никогда, потому что никто больше не посмотрит."""
    from app.connectors.crm_sender import SPEC

    assert SPEC.confirms_delivery is False
    assert SPEC.send_window is not None, "вне 24 часов свободный текст запрещён"


def test_the_ids_go_out_as_numbers_because_their_schema_says_so() -> None:
    """Живой sender объявляет branchId, id и userId целыми и отвергает ВЕСЬ запрос при
    строке: «Property '/branchId': Invalid type. Expected null|integer, but received unknown».

    У нас это поля ввода и поля колбека, то есть строки, и первая версия отправляла их как
    есть. Заглушка приняла бы что угодно — дефект нашёлся только живым вызовом.
    """
    args = TENANT.send_args(chat_id="987", conversation_id="6281234567890",
                            user_id="4521", text="halo")

    assert args["branchId"] == 435
    assert args["id"] == 987
    assert args["userId"] == 4521
    assert isinstance(args["branchId"], int)
    assert args["project"] == "crm"
    assert args["text"] == "halo"


def test_a_missing_optional_id_is_omitted_rather_than_sent_as_nothing() -> None:
    """Схема разрешает пропустить id и userId. Слать выдуманный ноль вместо пропуска
    значит адресовать сообщение несуществующей записи."""
    args = TENANT.send_args(chat_id="", conversation_id="62812", user_id=None, text="halo")

    assert "id" not in args
    assert "userId" not in args
    assert args["conversationId"] == "62812"


def test_the_reconciliation_window_also_numbers_the_branch() -> None:
    args = TENANT.inbound_args("whats-app")

    assert args == {"project": "crm", "channel": "whats-app", "branchId": 435}


async def test_a_muted_connector_is_not_even_offered_for_generation(db_session) -> None:  # noqa: ANN001
    """Пока ответы выключены, тред не должен попадать в отбор на генерацию.

    Отказ на отправке — не защита от расхода: текст к тому моменту сочинён и оплачен
    брокеру. Замерено на проде 11.08: сразу после заведения канала Степан сложил два
    ответа лидам Джакарты, и оба легли в очередь как failed с этой причиной.

    Отбор уже отсекает выключенные каналы ровно по этой логике — здесь то же самое, но
    приём при этом обязан продолжать работать.
    """
    from app.adapters.db.models import (  # noqa: PLC0415
        AppSetting,
        Branch,
        Channel,
        ChannelThread,
        Lead,
    )
    from app.domain.enums import Stage  # noqa: PLC0415
    from app.modules.settings.service import invalidate  # noqa: PLC0415
    from app.worker.wiring import threads_awaiting_reply  # noqa: PLC0415

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind="crm_sender", is_active=True)
    db_session.add(ch)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, agent_enabled=True)
    db_session.add(lead)
    await db_session.flush()
    db_session.add(ChannelThread(
        lead_id=lead.id, channel_id=ch.id, external_thread_id="c-1",
        last_in_at=datetime(2026, 8, 11, 7, 0, 0), last_out_at=None))
    await db_session.flush()
    invalidate(b.id)

    assert await threads_awaiting_reply(db_session, b.id) == []

    db_session.add(AppSetting(branch_id=b.id, key="sender_enabled", value="true"))
    await db_session.flush()
    invalidate(b.id)

    assert len(await threads_awaiting_reply(db_session, b.id)) == 1


async def test_replies_being_off_is_a_configuration_not_a_delivery_failure(db_session) -> None:  # noqa: ANN001
    """Выключенные ответы не должны усыплять лида.

    11.08 на проде отказ «ответы выключены» дошёл до общей обработки ошибок отправки,
    та посчитала его неустранимым сбоем доставки и увела ТРИ живых лида Джакарты в
    dormant с выключенным агентом. Включение ответов их бы уже не разбудило — стадия
    молчания переживает настройку.
    """
    from app.adapters.db.models import (  # noqa: PLC0415
        AppSetting,
        Branch,
        Channel,
        ChannelThread,
        Lead,
        Outbox,
    )
    from app.domain.enums import Stage  # noqa: PLC0415
    from app.modules.conversation.outbox import OutboxSender  # noqa: PLC0415
    from app.modules.settings.service import invalidate  # noqa: PLC0415

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind="crm_sender", is_active=True)
    db_session.add(ch)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, agent_enabled=True)
    db_session.add(lead)
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="c-1",
                       last_in_at=datetime(2026, 8, 11, 7, 0, 0))
    db_session.add(th)
    await db_session.flush()
    db_session.add(Outbox(branch_id=b.id, thread_id=th.id, text="halo",
                          status="pending", source="agent",
                          scheduled_at=datetime(2026, 8, 11, 7, 1, 0)))
    for k, v in {"sender_enabled": "false", "quiet_start": "0", "quiet_end": "0",
                 "hourly_cap": "0", "daily_cap": "0"}.items():
        db_session.add(AppSetting(branch_id=b.id, key=k, value=v))
    await db_session.flush()
    invalidate(b.id)

    port = CrmSenderAdapter(db_session, _Mcp(), TENANT, replies_enabled=False)
    row = await OutboxSender(db_session, b.id, port).send_next(th.id)

    assert row is not None
    assert row.status == "skipped", "не failed: это настройка, а не сбой доставки"
    refreshed = await db_session.get(Lead, lead.id)
    assert refreshed.stage == Stage.QUALIFYING, "лида нельзя усыплять за выключённый канал"
    assert refreshed.agent_enabled is True
