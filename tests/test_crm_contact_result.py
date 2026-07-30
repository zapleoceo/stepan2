"""Читатель CRM достаёт результат контакта, а не только три булевых.

До 30.07.2026 `_derive` сводил всю ленту к «существует / купил / звонили», а `name_result`
выбрасывал. Между тем филиал за месяц пользуется ровно пятью значениями — wait_call (74%),
result_think (11%), result_next_enrollment (9%), result_fail (9%), result_event (1%), — и
каждое означает разное продолжение разговора. Без них политика поведения строиться не на чем.

Проверено на боевом 30.07: у клиента 219399 читается result_fail от 29.07 15:37, менеджер
Asih Angesti, напоминание на 30.07.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.crm_mcp import CrmMcpReader

_R = CrmMcpReader("jakarta")


def _row(result: str, when: str, who: str = "Asih") -> dict:
    return {
        "typeName": "out-call", "date_time": when,
        "users": {"fio_user": who},
        "results": [{"id_result": "26", "name_result": [result]}],
    }


def test_the_newest_result_wins() -> None:
    rows = [
        _row("wait_call", "2026-07-28T10:00:00+07:00"),
        _row("result_think", "2026-07-29T15:37:16+07:00", "Citra"),
        _row("result_fail", "2026-07-27T09:00:00+07:00"),
    ]
    name, at, who = _R._latest_result(rows)  # noqa: SLF001
    assert name == "result_think"
    assert at == datetime(2026, 7, 29, 15, 37, 16, tzinfo=UTC) - timedelta(hours=7)
    assert who == "Citra"


def test_results_hidden_inside_a_grouped_row_are_still_seen() -> None:
    """CRM прячет часть контактов в `group`; без обхода терялась примерно половина ленты."""
    rows = [{"typeName": "out-call", "date_time": "2026-07-01T10:00:00+07:00",
             "group": [_row("result_next_enrollment", "2026-07-29T12:00:00+07:00")]}]
    assert _R._latest_result(rows)[0] == "result_next_enrollment"  # noqa: SLF001


def test_a_history_without_results_is_not_an_error() -> None:
    assert _R._latest_result([]) == ("", None, None)  # noqa: SLF001
    assert _R._latest_result([{"typeName": "call"}])[0] == ""  # noqa: SLF001


def test_a_future_reminder_becomes_next_contact_at() -> None:
    tomorrow = (datetime.now(UTC) + timedelta(days=2)).strftime("%d.%m.%y")
    got = _R._next_contact_at([{"remind_at": tomorrow}])  # noqa: SLF001
    assert got is not None and got.startswith(
        (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%d"))


def test_a_reminder_in_the_past_is_ignored() -> None:
    """Гейт трактует любое непустое next_contact_at как основание не проявлять инициативу.
    Вчерашнее напоминание заморозило бы лида навсегда."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%d.%m.%y")
    assert _R._next_contact_at([{"remind_at": yesterday}]) is None  # noqa: SLF001
    assert _R._next_contact_at([{"remind_at": "не дата"}]) is None  # noqa: SLF001
    assert _R._next_contact_at([]) is None  # noqa: SLF001


def test_derive_carries_the_result_into_the_state() -> None:
    raw = _R._derive(219399, [_row("wait_call", "2026-07-29T14:50:41+07:00")])  # noqa: SLF001
    assert raw["last_result"] == "wait_call"
    assert raw["last_result_by"] == "Asih"
    assert raw["exists"] is True


def test_the_result_lands_in_the_status_column() -> None:
    """crm_lead_state.status существовал и пустовал — теперь в нём то, в каком состоянии
    лид у менеджера."""
    from app.modules.crm.gate import _parse  # noqa: PLC0415
    state = _parse({"exists": True, "last_result": "result_think"})
    assert state.status == "result_think"
