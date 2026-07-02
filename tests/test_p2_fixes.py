"""P2 correctness/DRY: shared branch clock, coach revert as inverse substring swap."""
from __future__ import annotations

from datetime import date, datetime

from app.adapters.db.models import Branch, CoachingEdit, KnowledgeDoc
from app.domain.clock import branch_day_start_utc, branch_now, branch_today
from app.modules.conversation.coach_service import revert_edit

# ─── clock util ───────────────────────────────────────────────────────────────

def test_branch_clock_offsets() -> None:
    assert isinstance(branch_today(7), date)
    assert isinstance(branch_now(7), datetime)
    # WIB (UTC+7) is 7h ahead of UTC (allow sub-second drift between the two calls)
    assert abs((branch_now(7) - branch_now(0)).total_seconds() - 7 * 3600) < 1


def test_branch_day_start_utc_wraps() -> None:
    # 2026-07-02 01:00 UTC, tz=+7 → local 08:00 same day → local midnight 00:00 = prev
    # day 17:00 UTC (2026-07-01 17:00)
    now = datetime(2026, 7, 2, 1, 0)
    start = branch_day_start_utc(now, 7)
    assert start == datetime(2026, 7, 1, 17, 0)
    assert start <= now


# ─── coach revert (inverse substring, not whole-doc clobber) ──────────────────

async def _doc_and_edit(s, *, content: str, old: str, new: str) -> tuple[int, int, KnowledgeDoc]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    doc = KnowledgeDoc(branch_id=b.id, slug="faq", title="FAQ", content=content)
    edit = CoachingEdit(branch_id=b.id, request="r", status="applied", slug="faq",
                        old_text=old, new_text=new, summary="s")
    s.add(doc)
    s.add(edit)
    await s.flush()
    return b.id, edit.id, doc


async def test_revert_restores_only_the_changed_substring(db_session) -> None:
    # doc already reflects an applied edit that turned "harga 1jt" into "harga 2jt"
    bid, eid, doc = await _doc_and_edit(
        db_session,
        content="Kelas Vibe Coding, harga 2jt, mulai 15 Juli. Info lain tetap.",
        old="harga 1jt", new="harga 2jt",
    )
    result = await revert_edit(db_session, bid, eid)
    assert result is not None and result.status == "reverted"
    # only the substring reverts; everything else (schedule, other text) is intact
    assert doc.content == "Kelas Vibe Coding, harga 1jt, mulai 15 Juli. Info lain tetap."


async def test_revert_refused_when_new_text_absent(db_session) -> None:
    bid, eid, doc = await _doc_and_edit(
        db_session, content="doc was rewritten since — no new_text here",
        old="harga 1jt", new="harga 2jt",
    )
    result = await revert_edit(db_session, bid, eid)
    assert result is not None and result.status == "revert_failed"
    assert doc.content == "doc was rewritten since — no new_text here"  # untouched


async def test_revert_ignores_non_applied_or_foreign_branch(db_session) -> None:
    bid, eid, _ = await _doc_and_edit(db_session, content="x harga 2jt", old="a", new="harga 2jt")
    assert await revert_edit(db_session, bid + 999, eid) is None  # wrong branch
