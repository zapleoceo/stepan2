"""Phone → lead resolution is scoped to one branch and never guesses.

The MCP write tools used to search every branch and act on the first row the scan returned,
so one tenant's connector could read or move another tenant's lead on nothing more than a
shared nine-digit tail. Every case below is that bug from a different angle.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.adapters.db.models import Branch, Lead  # noqa: E402
from app.domain.enums import Stage  # noqa: E402
from app.modules.leads.lookup import AmbiguousPhone, find_lead  # noqa: E402


async def _branch(s, name: str) -> int:
    b = Branch(name=name, lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _lead(s, branch_id: int, phone: str, name: str) -> Lead:
    lead = Lead(branch_id=branch_id, display_name=name, phone_e164=phone,
                stage=Stage.PRESENTING, agent_enabled=True)
    s.add(lead)
    await s.flush()
    return lead


async def test_matches_regardless_of_spacing_and_prefix(db_session) -> None:
    bid = await _branch(db_session, "Indonesia")
    await _lead(db_session, bid, "+62 812-3456", "Budi")
    lead = await find_lead(db_session, "+628123456", bid)
    assert lead is not None and lead.display_name == "Budi"
    assert await find_lead(db_session, "+62999", bid) is None


async def test_each_branch_sees_only_its_own_lead(db_session) -> None:
    indo = await _branch(db_session, "Indonesia")
    malay = await _branch(db_session, "Malaysia")
    await _lead(db_session, indo, "+628123456789", "Budi")
    await _lead(db_session, malay, "+628123456789", "Aisyah")
    assert (await find_lead(db_session, "+628123456789", indo)).display_name == "Budi"
    assert (await find_lead(db_session, "+628123456789", malay)).display_name == "Aisyah"


async def test_cross_branch_search_refuses_and_names_the_branches(db_session) -> None:
    indo = await _branch(db_session, "Indonesia")
    malay = await _branch(db_session, "Malaysia")
    await _lead(db_session, indo, "+628123456789", "Budi")
    await _lead(db_session, malay, "+628123456789", "Aisyah")
    with pytest.raises(AmbiguousPhone) as exc:
        await find_lead(db_session, "+628123456789", None)  # a universal MCP token
    assert {c["branch_id"] for c in exc.value.candidates} == {indo, malay}


async def test_candidates_leak_neither_name_nor_full_number(db_session) -> None:
    indo = await _branch(db_session, "Indonesia")
    malay = await _branch(db_session, "Malaysia")
    await _lead(db_session, indo, "+628123456789", "Budi")
    await _lead(db_session, malay, "+628123456789", "Aisyah")
    with pytest.raises(AmbiguousPhone) as exc:
        await find_lead(db_session, "+628123456789", None)
    rendered = repr(exc.value.candidates) + str(exc.value)
    assert "Budi" not in rendered and "Aisyah" not in rendered
    assert "628123456789" not in rendered
    assert all(c["phone"] == "…6789" for c in exc.value.candidates)


async def test_a_scoped_search_never_names_another_tenants_branch(db_session) -> None:
    """The refusal must not become the leak it prevents: candidates come only from the
    branch that was searched, so the caller learns nothing about who else holds the number."""
    indo = await _branch(db_session, "Indonesia")
    malay = await _branch(db_session, "Malaysia")
    await _lead(db_session, indo, "+628123456789", "Budi")
    await _lead(db_session, indo, "+618123456789", "Bagus")
    await _lead(db_session, malay, "+608123456789", "Aisyah")
    with pytest.raises(AmbiguousPhone) as exc:
        await find_lead(db_session, "08123456789", indo)
    assert {c["branch_id"] for c in exc.value.candidates} == {indo}


async def test_two_leads_inside_one_branch_are_also_refused(db_session) -> None:
    """Same tenant, two rows sharing the tail — still nobody's call to make but a human's."""
    bid = await _branch(db_session, "Indonesia")
    await _lead(db_session, bid, "+628123456789", "Budi")
    await _lead(db_session, bid, "+618123456789", "Bagus")
    with pytest.raises(AmbiguousPhone):
        await find_lead(db_session, "08123456789", bid)


async def test_full_number_beats_a_shared_tail(db_session) -> None:
    """A caller who quoted the whole E.164 number gets that lead, not the one that merely
    shares its last nine digits — and not whichever row the scan happened to reach first."""
    bid = await _branch(db_session, "Indonesia")
    await _lead(db_session, bid, "+608123456789", "wrong country")  # inserted first
    await _lead(db_session, bid, "+628123456789", "Budi")
    lead = await find_lead(db_session, "+628123456789", bid)
    assert lead is not None and lead.display_name == "Budi"
