"""Resolve a lead by phone inside ONE branch, refusing a match it cannot pin down.

Phone is the cross-channel identity key and the match is on the trailing national digits,
so the same query string can hit leads belonging to different tenants — a Malaysian +60
number and an Indonesian +62 one share their last nine digits often enough to matter. The
MCP write tools used to search every branch and act on whichever row the scan returned
first, which meant one tenant's connector could read or mutate another tenant's lead.

Two rules follow, and both are enforced here rather than at each call site:
  * `branch_id` is positional and mandatory. Searching every branch is still possible, but
    only by passing None on purpose — never by forgetting an argument.
  * More than one candidate is an error carrying the candidates back, never a silent pick.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Lead

_SUFFIX_LEN = 9


def _digits(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


def match_key(phone: str) -> str:
    """Country/format-agnostic lookup key: the trailing national digits. Stored phones are
    canonical '+<cc>…' E.164 (the only writers are phone.to_e164 / phone.extract_phone), but
    a query may arrive as '0812…', '62…' or '+62…' — comparing the last 9 significant digits
    matches all of those without re-hardcoding a country prefix here."""
    digits = _digits(phone)
    return digits[-_SUFFIX_LEN:] if len(digits) >= _SUFFIX_LEN else digits


def mask(phone: str) -> str:
    """Last four digits only — enough for a human to recognise their own lead, useless to
    anyone reading another tenant's error message."""
    digits = _digits(phone)
    return f"…{digits[-4:]}" if len(digits) > 4 else "…"


def _summary(lead: Lead) -> dict:
    # branch_id is what the caller needs to disambiguate; the full number and the display
    # name belong to whichever tenant owns the lead, so they never travel in an error.
    return {"lead_id": lead.id, "branch_id": lead.branch_id,
            "phone": mask(lead.phone_e164 or "")}


class AmbiguousPhone(Exception):
    """A phone matched several leads. Carries the candidates so the caller can refuse with
    something actionable instead of guessing whose funnel to touch."""

    def __init__(self, phone: str, candidates: list[Lead]) -> None:
        self.candidates: list[dict] = [_summary(c) for c in candidates]
        super().__init__(
            f"{len(candidates)} leads match phone {mask(phone)}"
            " — repeat the call with an explicit branch_id")


async def match_leads(
    session: AsyncSession, phone: str, branch_id: int | None,
) -> list[Lead]:
    """Every lead whose national number equals this phone's, within `branch_id`
    (None = every branch)."""
    key = match_key(phone)
    if not key:
        return []
    stmt = select(Lead).where(Lead.phone_e164.is_not(None))
    if branch_id is not None:
        stmt = stmt.where(Lead.branch_id == branch_id)
    leads = (await session.execute(stmt)).scalars().all()
    return [lead for lead in leads if match_key(lead.phone_e164 or "") == key]


async def find_lead(
    session: AsyncSession, phone: str, branch_id: int | None,
) -> Lead | None:
    """The one lead in `branch_id` matching this phone, or None. Raises AmbiguousPhone when
    the phone cannot be pinned to a single lead.

    A full-number equality is preferred over the national-digits match, so two leads that
    merely share a nine-digit tail (different country codes) still resolve when the caller
    quoted the whole E.164 number."""
    matches = await match_leads(session, phone, branch_id)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    wanted = _digits(phone)
    exact = [lead for lead in matches if _digits(lead.phone_e164 or "") == wanted]
    if len(exact) == 1:
        return exact[0]
    raise AmbiguousPhone(phone, matches)
