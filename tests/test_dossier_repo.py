"""DossierRepo — branch-scoped persistence of the v3 working memory.

The load path is what makes the v2→v3 switchover safe: a lead carrying only the legacy `needs`
JSON still comes back with a populated dossier, and writes never touch `needs`, so flipping
reply_engine in either direction costs no context.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, Lead
from app.domain.enums import Stage
from app.modules.conversation.dossier import LeadDossier, Objection
from app.modules.conversation.needs import NeedsProfile
from app.modules.conversation.repository import DossierRepo


async def _branch(s, name: str = "T") -> int:  # noqa: ANN001
    b = Branch(name=name, lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _lead(s, branch_id: int, **kw) -> int:  # noqa: ANN001, ANN003
    lead = Lead(branch_id=branch_id, stage=Stage.QUALIFYING, **kw)
    s.add(lead)
    await s.flush()
    return lead.id


async def test_saves_and_loads_a_dossier(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    lid = await _lead(db_session, bid)
    repo = DossierRepo(db_session, bid)

    d = LeadDossier(role="student", pains=["takut telat"],
                    objections=[Objection("mahal")])
    await repo.save(lid, d)
    assert await repo.load(lid) == d


async def test_a_lead_with_only_legacy_needs_loads_as_a_dossier(db_session) -> None:  # noqa: ANN001
    """The switchover case: nothing was backfilled, yet no fact is lost."""
    bid = await _branch(db_session)
    legacy = NeedsProfile(jobs=["pindah karier"], pains=["takut telat"],
                          gains=["kerja remote"], objections=["mahal"]).to_json()
    lid = await _lead(db_session, bid, needs=legacy)

    d = await DossierRepo(db_session, bid).load(lid)
    assert d.job_to_be_done == "pindah karier"
    assert d.pains == ["takut telat"]
    assert d.open_objections() == ["mahal"]


async def test_saving_never_touches_the_legacy_needs_column(db_session) -> None:  # noqa: ANN001
    """v2 must keep working on the same lead, so switching back is lossless."""
    bid = await _branch(db_session)
    legacy = NeedsProfile(pains=["takut telat"]).to_json()
    lid = await _lead(db_session, bid, needs=legacy)

    await DossierRepo(db_session, bid).save(lid, LeadDossier(pains=["baru"]))

    lead = await db_session.get(Lead, lid)
    assert lead.needs == legacy
    assert lead.dossier is not None


async def test_a_lead_from_another_branch_is_invisible(db_session) -> None:  # noqa: ANN001
    bid_a = await _branch(db_session, "A")
    bid_b = await _branch(db_session, "B")
    lid_b = await _lead(db_session, bid_b)
    await DossierRepo(db_session, bid_b).save(lid_b, LeadDossier(role="student"))

    assert await DossierRepo(db_session, bid_a).load(lid_b) == LeadDossier()


async def test_saving_a_foreign_lead_is_a_no_op(db_session) -> None:  # noqa: ANN001
    bid_a = await _branch(db_session, "A")
    bid_b = await _branch(db_session, "B")
    lid_b = await _lead(db_session, bid_b)

    await DossierRepo(db_session, bid_a).save(lid_b, LeadDossier(role="parent"))

    lead = await db_session.get(Lead, lid_b)
    assert lead.dossier is None


async def test_a_missing_lead_loads_empty_and_saves_without_raising(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    repo = DossierRepo(db_session, bid)
    assert await repo.load(None) == LeadDossier()
    assert await repo.load(999_999) == LeadDossier()
    await repo.save(999_999, LeadDossier(role="student"))
