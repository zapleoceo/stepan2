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


def test_extract_phone_ignores_prices_and_prose() -> None:
    assert extract_phone("harga 1.200.000 rupiah") is None
    assert extract_phone("Rp 628.000 saja") is None
    assert extract_phone("halo kak apa kabar") is None
    assert extract_phone("") is None
    assert extract_phone(None) is None


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
