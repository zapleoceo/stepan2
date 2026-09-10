"""Отправка в CRM без живой CRM: одна процедура, её маркеры и журнал, и условия, общие для
всех путей.

До 10.09.2026 каждый путь в CRM держал свой набор условий, и они разъехались ровно так, как
пожаловалась CRM-команда: отказников возвращали в работу, загруженную историю выдавали за
новую активность, а два маркера идемпотентности не знали друг о друге. Тесты ниже держат
каждое из этих правил по отдельности — и держат их на ОБЩЕМ определении, чтобы правка одного
пути не могла снова развести его с остальными.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text

from app.modules.crm.push_mcp import (
    EVENT_WAIT_CALL,
    FAILURE_BACKOFF_H,
    FAILURE_CAP,
    HANDOFF_WINDOW_DAYS,
    PUSH_FAILED_REASON,
    PUSHED_REASON,
    CrmMcpPusher,
    LeadToPush,
    _comment_for,
    drain_writeback,
    push_block_reason,
    push_one,
    pushed_marker,
)

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _FakePusher:
    def __init__(self, fail_phones: set[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_phones = fail_phones or set()

    async def add_lead_event(self, phone, event_type, *, comment, name):  # noqa: ANN001
        self.calls.append(
            {"phone": phone, "event_type": event_type, "comment": comment, "name": name})
        if phone in self.fail_phones:
            return False, "duplicate"
        return True, "ok"


def _lead(lid, phone, **kw):  # noqa: ANN001
    d = dict(name="Budi", stage="presenting", product="smm_intensive", days_idle=3,
             last_msg="masih mikir dulu kak")
    d.update(kw)
    return LeadToPush(lead_id=lid, phone=phone, **d)


async def _seed(db_session, phone: str, *, kind: str = "instagram",  # noqa: ANN001
                wrote_ago: timedelta = timedelta(hours=1),
                crm_status: str | None = None) -> tuple[int, int, int]:
    """Филиал, канал заданного вида, лид в PRESENTING с телефоном, тред и ОДНА реплика лида
    заданной давности. Возвращает (branch_id, lead_id, thread_id)."""
    from app.adapters.db.models import (  # noqa: PLC0415
        Branch,
        Channel,
        ChannelThread,
        CrmLeadState,
        Lead,
        Message,
    )
    from app.domain.enums import Stage  # noqa: PLC0415

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=kind, is_active=True)
    lead = Lead(branch_id=b.id, stage=Stage.PRESENTING, phone_e164=phone)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id=f"x{lead.id}",
                       product_slug="smm_intensive", last_in_at=_NOW - wrote_ago)
    db_session.add(th)
    await db_session.flush()
    db_session.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id,
                           external_id=f"m{lead.id}", direction="in", sent_by="lead",
                           text="halo kak", occurred_at=_NOW - wrote_ago))
    if crm_status is not None:
        db_session.add(CrmLeadState(lead_id=lead.id, branch_id=b.id, status=crm_status,
                                    fetched_at=_NOW, verdict="proceed"))
    await db_session.flush()
    return b.id, lead.id, th.id


async def _markers(db_session, lead_id: int) -> list[str]:  # noqa: ANN001
    rows = (await db_session.execute(text(
        "SELECT reason FROM stage_event WHERE lead_id = :l ORDER BY id"), {"l": lead_id})).all()
    return [r[0] for r in rows]


# ─── одна процедура отправки ──────────────────────────────────────────────────

async def test_push_one_maps_fields_builds_the_comment_and_stamps_both_markers(
    db_session,  # noqa: ANN001
) -> None:
    bid, lid, _ = await _seed(db_session, "+62811100000")
    p = _FakePusher()
    lead = _lead(lid, "+62811100000")

    out = await push_one(db_session, bid, p, lead, marker=PUSHED_REASON,
                         event_type=EVENT_WAIT_CALL, comment=_comment_for(lead))

    assert out == "pushed"
    c0 = p.calls[0]
    assert c0["phone"] == "+62811100000" and c0["event_type"] == EVENT_WAIT_CALL
    assert c0["name"] == "Budi"
    # Контекст бота едет в комментарии (managerComment → описание в CRM).
    assert "stage=presenting" in c0["comment"] and "smm_intensive" in c0["comment"]
    assert "diam 3 hari" in c0["comment"] and "masih mikir dulu kak" in c0["comment"]
    # Два маркера: общий для отчётов и с типом внутри — для дедупа по состоянию.
    assert _markers_have(await _markers(db_session, lid),
                         PUSHED_REASON, pushed_marker(EVENT_WAIT_CALL))


def _markers_have(markers: list[str], *wanted: str) -> bool:
    return all(w in markers for w in wanted)


async def test_push_one_journals_a_failure_and_marks_no_success(db_session) -> None:  # noqa: ANN001
    """Провал раньше не оставлял следа — и лид пробовался заново каждый час без предела."""
    bid, lid, _ = await _seed(db_session, "+62822200000")
    p = _FakePusher(fail_phones={"+62822200000"})

    out = await push_one(db_session, bid, p, _lead(lid, "+62822200000"), marker=PUSHED_REASON,
                         event_type=EVENT_WAIT_CALL, comment="c")

    assert out == "failed"
    markers = await _markers(db_session, lid)
    assert PUSH_FAILED_REASON in markers
    assert PUSHED_REASON not in markers


async def test_push_one_skips_a_state_that_already_went(db_session) -> None:  # noqa: ANN001
    """Повтор того же состояния — не событие. Смена состояния — событие (другой маркер)."""
    bid, lid, _ = await _seed(db_session, "+62833300000")
    p = _FakePusher()
    lead = _lead(lid, "+62833300000")

    assert await push_one(db_session, bid, p, lead, marker=PUSHED_REASON,
                          event_type=EVENT_WAIT_CALL, comment="c") == "pushed"
    assert await push_one(db_session, bid, p, lead, marker=PUSHED_REASON,
                          event_type=EVENT_WAIT_CALL, comment="c") == "skipped"
    assert len(p.calls) == 1, "второго вызова в CRM быть не должно"


def test_comment_handles_missing_product_and_message() -> None:
    c = _comment_for(_lead(1, "+62811100000", product=None, last_msg=""))
    assert "belum jelas" in c and '"-"' in c  # graceful fallbacks, no crash


# ─── условия, общие для всех путей ────────────────────────────────────────────

async def test_drain_marks_success_and_never_repeats_it(db_session) -> None:  # noqa: ANN001
    bid, _, _ = await _seed(db_session, "+628111222333")
    ok = _FakePusher()

    assert await drain_writeback(db_session, bid, ok) == {"eligible": 1, "pushed": 1, "failed": 0}
    r2 = await drain_writeback(db_session, bid, ok)
    assert r2["eligible"] == 0 and r2["pushed"] == 0  # already pushed, never again


async def test_a_failed_push_waits_out_the_backoff_then_retries(db_session) -> None:  # noqa: ANN001
    """Двухчасовой отказ токена 08.09.2026 дал 708 строк ошибок: каждый прогон бился в те
    же 25 лидов. Теперь провал — пауза, а не немедленный повтор."""
    bid, lid, _ = await _seed(db_session, "+62844400000")
    fail = _FakePusher(fail_phones={"+62844400000"})

    assert await drain_writeback(db_session, bid, fail) == {"eligible": 1, "pushed": 0, "failed": 1}
    assert (await drain_writeback(db_session, bid, fail))["eligible"] == 0, "пауза после провала"

    # Провал состарился — лид снова в очереди.
    await db_session.execute(text(
        "UPDATE stage_event SET created_at = :t WHERE lead_id = :l AND reason = :r"),
        {"t": _NOW - timedelta(hours=FAILURE_BACKOFF_H + 1), "l": lid, "r": PUSH_FAILED_REASON})
    assert (await drain_writeback(db_session, bid, _FakePusher()))["pushed"] == 1


async def test_a_lead_that_failed_too_often_leaves_the_queue(db_session) -> None:  # noqa: ANN001
    """Предел повторов — и он виден: провалы лежат в журнале, а не растворяются."""
    from app.adapters.db.models import StageEvent  # noqa: PLC0415

    bid, lid, _ = await _seed(db_session, "+62855500000")
    for i in range(FAILURE_CAP):
        db_session.add(StageEvent(
            branch_id=bid, lead_id=lid, thread_id=None, from_stage="presenting",
            to_stage="presenting", actor="system", reason=PUSH_FAILED_REASON,
            created_at=_NOW - timedelta(days=i + 2)))
    await db_session.flush()

    assert (await drain_writeback(db_session, bid, _FakePusher()))["eligible"] == 0
    assert await push_block_reason(db_session, lid) == "push backing off"


async def test_a_lead_the_manager_refused_is_never_pushed_back(db_session) -> None:  # noqa: ANN001
    """129 отказников уехали в CRM как «перезвонить» 11–12.08.2026, и мы знали их статус.
    Отказ менеджера — конец истории для отправки, пока он сам не поставит другой результат."""
    bid, lid, _ = await _seed(db_session, "+62866600000", crm_status="result_fail")

    assert (await drain_writeback(db_session, bid, _FakePusher()))["eligible"] == 0
    assert await push_block_reason(db_session, lid) == "refused in CRM"


async def test_a_lead_already_in_the_crm_is_not_pushed_back_into_it(db_session) -> None:  # noqa: ANN001
    """Разговор пришёл ИЗ CRM (коннектор crm_native) — человек там есть, менеджер с ним уже
    говорит. 535 таких уехали обратно 11–12.08.2026 как «diam N hari, perlu di-follow up»."""
    bid, lid, _ = await _seed(db_session, "+62877700000", kind="crm_sender")

    assert (await drain_writeback(db_session, bid, _FakePusher()))["eligible"] == 0
    assert await push_block_reason(db_session, lid) == "already in CRM"


async def test_old_history_is_not_new_activity(db_session) -> None:  # noqa: ANN001
    """Тёплый — значит написал НЕДАВНО. Загруженная история хранит настоящие даты сообщений,
    поэтому одно и то же условие отсекает и её, и просто остывшего лида."""
    bid, _, _ = await _seed(db_session, "+62888800000",
                            wrote_ago=timedelta(days=HANDOFF_WINDOW_DAYS + 1))
    assert (await drain_writeback(db_session, bid, _FakePusher()))["eligible"] == 0

    bid2, _, _ = await _seed(db_session, "+62899900000", wrote_ago=timedelta(days=1))
    assert (await drain_writeback(db_session, bid2, _FakePusher()))["eligible"] == 1


async def test_block_reason_is_none_for_a_pushable_lead(db_session) -> None:  # noqa: ANN001
    _, lid, _ = await _seed(db_session, "+62810100000")
    assert await push_block_reason(db_session, lid) is None


# ─── поиск клиента: три ответа, а не два ───────────────────────────────────────

class _McpResult:
    def __init__(self, text_: str, is_error: bool = False) -> None:
        self.content = [SimpleNamespace(text=text_)]
        self.isError = is_error


class _McpSession:
    """Сессия-заглушка: отвечает на поиск заданным результатом и записывает все вызовы."""

    def __init__(self, search: _McpResult) -> None:
        self.search = search
        self.tools: list[str] = []

    async def call_tool(self, name: str, args: dict) -> _McpResult:  # noqa: ARG002
        self.tools.append(name)
        if name == "crm_client_search":
            return self.search
        return _McpResult("ok")


async def _push_with(monkeypatch, search: _McpResult) -> tuple[tuple[bool, str], list[str]]:  # noqa: ANN001
    from contextlib import asynccontextmanager  # noqa: PLC0415

    import app.adapters.mcp_client as mcp_client  # noqa: PLC0415

    sess = _McpSession(search)

    @asynccontextmanager
    async def fake_session(url, timeout_s=0):  # noqa: ANN001, ARG001
        yield sess

    monkeypatch.setattr(mcp_client, "session", fake_session)
    res = await CrmMcpPusher("https://crm/mcp?token=x", "jakarta").add_lead_event(
        "+62811", EVENT_WAIT_CALL, comment="c", name="Budi")
    return res, sess.tools


async def test_a_known_client_gets_an_event_not_a_new_request(monkeypatch) -> None:  # noqa: ANN001
    (ok, _), tools = await _push_with(monkeypatch, _McpResult('{"count_all": 1}'))
    assert ok and tools == ["crm_client_search", "crm_lead_add_event"]


async def test_an_unknown_client_gets_a_new_request(monkeypatch) -> None:  # noqa: ANN001
    (ok, _), tools = await _push_with(monkeypatch, _McpResult('{"count_all": 0}'))
    assert ok and tools == ["crm_client_search", "crm_internet_request_create"]


async def test_a_failed_search_creates_nothing(monkeypatch) -> None:  # noqa: ANN001
    """«Упал» ≠ «нет». 08.09.2026 за два часа отказа токена поиск падал 62 раза, и каждый раз
    следом шла попытка завести НОВУЮ заявку на клиента, который в CRM давно есть."""
    (ok, detail), tools = await _push_with(
        monkeypatch, _McpResult("Branch 'jakarta' is not allowed", is_error=True))
    assert not ok and "search failed" in detail
    assert tools == ["crm_client_search"], "после упавшего поиска — ни события, ни заявки"


async def test_an_unparseable_search_creates_nothing_either(monkeypatch) -> None:  # noqa: ANN001
    (ok, _), tools = await _push_with(monkeypatch, _McpResult("<html>502</html>"))
    assert not ok and tools == ["crm_client_search"]
