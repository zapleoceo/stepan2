"""Плитки «Передано менеджеру» и «Передан в CRM»: две цифры и чистый подсчёт.

29.07.2026 панель за 4 часа показывала «5 лидов / 23 передано». Двадцать два из тех
двадцати трёх были служебными строками журнала воронки — отметки сверки с CRM, которые
пишутся как ready → ready. Настоящих передач за то окно была одна.

Плитка теперь показывает «всего накопительно / из числа новых лидов»: одна цифра всегда
врёт. Накопительная не двигается вместе с окном, а когортная прячет каждую передачу, чей
разговор начался раньше — за трое суток она показывала 2 при 11 реально закрытых.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.adapters.db.models import Branch, Lead, StageEvent
from app.api._query import fetch_crm_pushed_totals, fetch_handover_totals

_NOW = datetime(2026, 7, 29, 12, 0)
_SINCE = _NOW - timedelta(hours=4)


async def _branch(session) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    session.add(b)
    await session.flush()
    return b.id


async def _lead(session, bid: int, *, created_at: datetime) -> int:  # noqa: ANN001
    lead = Lead(branch_id=bid, stage="ready", created_at=created_at)
    session.add(lead)
    await session.flush()
    return lead.id


def _ev(bid: int, lead_id: int, **kw) -> StageEvent:  # noqa: ANN003
    kw.setdefault("from_stage", "presenting")
    kw.setdefault("to_stage", "ready")
    kw.setdefault("actor", "bot")
    kw.setdefault("created_at", _NOW - timedelta(hours=1))
    return StageEvent(branch_id=bid, lead_id=lead_id, thread_id=None, **kw)


async def test_bookkeeping_rows_are_not_counted_as_handovers(db_session) -> None:
    """The live failure: a reconciliation stamp is written as ready → ready and used to read
    as a fresh hand-off, so the panel reported work that never happened."""
    bid = await _branch(db_session)
    real = await _lead(db_session, bid, created_at=_SINCE + timedelta(minutes=5))
    book = await _lead(db_session, bid, created_at=_SINCE + timedelta(minutes=5))
    db_session.add_all([
        _ev(bid, real),                                            # настоящий переход
        _ev(bid, book, from_stage="ready", to_stage="ready",       # служебная отметка
            actor="system", reason="crm_verified_present"),
    ])
    await db_session.flush()

    total, cohort = await fetch_handover_totals(db_session, [bid], since=_SINCE)
    assert (total, cohort) == (1, 1)


async def test_the_left_number_is_cumulative_and_the_right_one_is_the_cohort(
    db_session,
) -> None:
    bid = await _branch(db_session)
    old = await _lead(db_session, bid, created_at=_NOW - timedelta(days=30))
    fresh = await _lead(db_session, bid, created_at=_SINCE + timedelta(minutes=5))
    db_session.add_all([_ev(bid, old), _ev(bid, fresh)])
    await db_session.flush()

    total, cohort = await fetch_handover_totals(db_session, [bid], since=_SINCE)
    assert total == 2      # всего накопительно — окно на него не влияет
    assert cohort == 1     # из пришедших за окно


async def test_crm_tile_counts_only_leads_actually_pushed(db_session) -> None:
    """The question the tile exists to answer: of the threads the bot dropped, which ones can
    a manager actually call? A hand-off without a CRM card is nobody's task."""
    bid = await _branch(db_session)
    handed_only = await _lead(db_session, bid, created_at=_SINCE + timedelta(minutes=5))
    pushed = await _lead(db_session, bid, created_at=_SINCE + timedelta(minutes=5))
    db_session.add_all([
        _ev(bid, handed_only),
        _ev(bid, pushed),
        _ev(bid, pushed, from_stage="ready", to_stage="ready", actor="system",
            reason="crm_pushed_handoff"),
    ])
    await db_session.flush()

    assert await fetch_handover_totals(db_session, [bid], since=_SINCE) == (2, 2)
    assert await fetch_crm_pushed_totals(db_session, [bid], since=_SINCE) == (1, 1)


async def test_the_tile_renders_both_numbers(db_session) -> None:
    from app.api._i18n import _lang, t  # noqa: PLC0415
    from app.api._ui_reports import reports_panel_html  # noqa: PLC0415

    _lang.set("ru")
    html = reports_panel_html(
        stage_counts={"new": 3}, hour_in={}, hour_out={},
        handover_totals=(143, 0), crm_totals=(74, 0), deals=0,
    )
    assert "143 / 0" in html
    assert "74 / 0" in html
    assert t("rep.crm") in html
