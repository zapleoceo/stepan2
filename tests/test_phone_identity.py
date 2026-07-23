"""extract_phone + ingest identity merge — a lead sharing the same number on two
channels collapses into one lead (the cross-channel merge the design promises)."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, Lead
from app.domain.enums import ChannelKind
from app.modules.leads import IngestService, extract_phone
from app.ports.channel import InboundMessage


def test_extract_phone_variants_yield_one_key() -> None:
    assert extract_phone("nomor saya 0812-3456-7890") == "+6281234567890"
    assert extract_phone("WA: +62 812 3456 7890") == "+6281234567890"
    assert extract_phone("62812 3456 7890 ya kak") == "+6281234567890"


def test_extract_phone_matches_foreign_number_by_plus_prefix() -> None:
    """thread 452: a Ukrainian lead's '+380…' number on the Indonesia branch (country_code
    default '62') used to match no _SHAPE_BY_CC entry and get silently dropped — an explicit
    '+' is unambiguous regardless of branch country, so it must not need a shape match."""
    assert extract_phone("+380994811889") == "+380994811889"
    assert extract_phone("📱 Phone number · +380 99 481 1889") == "+380994811889"
    assert extract_phone("call me at +1 415 555 0132") == "+14155550132"


def test_extract_phone_ignores_prices_and_prose() -> None:
    assert extract_phone("harga 1.200.000 rupiah") is None
    assert extract_phone("Rp 628.000 saja") is None
    assert extract_phone("halo kak apa kabar") is None
    assert extract_phone("") is None
    assert extract_phone(None) is None


def test_extract_phone_honours_branch_country_code() -> None:
    """Each branch matches ITS OWN country's mobile shape and stamps its own +cc.
    A foreign-shaped number is NOT mined for the wrong country (never mis-prefixed)."""
    # Indonesia (default) — proven, unchanged
    assert extract_phone("0812-3456-7890") == "+6281234567890"
    # Malaysia: local 01x mobile → +60
    assert extract_phone("nomor saya 0123-456-789", country_code="60") == "+60123456789"
    assert extract_phone("+60 12-345 6789", country_code="60") == "+60123456789"
    # Philippines: local 09xx mobile → +63
    assert extract_phone("0917 123 4567", country_code="63") == "+639171234567"
    # an Indonesian-shaped number on a Malaysian branch does NOT match → no mis-stamp
    assert extract_phone("0812-3456-7890", country_code="60") is None
    # unknown country code → don't guess a number out of free text
    assert extract_phone("0812-3456-7890", country_code="99") is None


async def test_existing_thread_not_repointed_by_a_typed_number(db_session) -> None:
    """The hijack/data-loss path: an ALREADY-ESTABLISHED conversation whose lead later
    types SOMEONE ELSE'S number must keep its own lead — a text-mined phone must never
    re-point a live thread onto that number's owner. (New-contact cross-channel merge is a
    separate, intended behaviour — see test_ingest_merges_same_phone_across_channels.)"""
    s = db_session
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ig = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ig)
    await s.flush()
    svc = IngestService(s, branch.id)
    when = datetime(2026, 6, 30, 10, 0)
    # lead A establishes their thread with their own number
    await svc.ingest(ig.id, [InboundMessage("ig-A", "ua", "saya 0812-3456-7890", when)])
    # lead B establishes a SEPARATE thread first (no phone) → a distinct lead
    await svc.ingest(ig.id, [InboundMessage("ig-B", "ub", "halo kak", when)])
    before = list((await s.exec(select(Lead).where(Lead.branch_id == branch.id))).all())
    assert len(before) == 2  # two distinct leads exist before the re-point attempt
    # B's EXISTING thread now types A's number — must NOT be re-pointed onto A
    await svc.ingest(ig.id, [InboundMessage("ig-B", "ub", "teman saya 0812-3456-7890", when)])
    leads = list((await s.exec(select(Lead).where(Lead.branch_id == branch.id))).all())
    assert len(leads) == 2  # B kept its own lead; not merged onto A


async def test_ingest_merges_same_phone_across_channels(db_session) -> None:
    s = db_session
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ig = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    wa = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP)
    s.add(ig)
    s.add(wa)
    await s.flush()

    svc = IngestService(s, branch.id)
    when = datetime(2026, 6, 30, 10, 0)
    await svc.ingest(ig.id, [InboundMessage("ig-1", "iguser", "nomor saya 0812-3456-7890", when)])
    await svc.ingest(wa.id, [InboundMessage("wa-1", "wauser", "halo +62 812 3456 7890", when)])

    leads = list((await s.exec(select(Lead).where(Lead.branch_id == branch.id))).all())
    assert len(leads) == 1
    assert leads[0].phone_e164 == "+6281234567890"


async def test_ingest_without_phone_keeps_leads_separate(db_session) -> None:
    s = db_session
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ig = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ig)
    await s.flush()

    svc = IngestService(s, branch.id)
    when = datetime(2026, 6, 30, 10, 0)
    await svc.ingest(ig.id, [InboundMessage("ig-A", "ua", "halo kak", when)])
    await svc.ingest(ig.id, [InboundMessage("ig-B", "ub", "info dong", when)])

    leads = list((await s.exec(select(Lead).where(Lead.branch_id == branch.id))).all())
    assert len(leads) == 2


async def test_ingest_uses_thread_participant_pk_as_ig_user_id(db_session) -> None:
    """The lead's stable IG id comes from the thread participant (lead_ig_user_id), not the
    per-item author — so a lead gets an ig_user_id even when the item's user_id is blank."""
    s = db_session
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ig = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ig)
    await s.flush()

    svc = IngestService(s, branch.id)
    when = datetime(2026, 6, 30, 10, 0)
    await svc.ingest(ig.id, [InboundMessage(
        "ig-1", sender_id="", text="halo kak", occurred_at=when,
        lead_ig_user_id="55501", sender_name="Budi")])
    lead = (await s.exec(select(Lead).where(Lead.branch_id == branch.id))).first()
    assert lead.ig_user_id == "55501" and lead.display_name == "Budi"
