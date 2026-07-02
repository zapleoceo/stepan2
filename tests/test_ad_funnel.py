"""Ad-funnel report: per-ad lead counts bucketed by stage (ORM query runs on SQLite)."""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
from app.api._query import fetch_ad_funnel
from app.api._ui_panels import _ad_funnel_html
from app.domain.enums import ChannelKind, Stage


async def _lead_from_ad(s, bid: int, cid: int, ad: str, stage: Stage) -> None:
    lead = Lead(branch_id=bid, stage=stage)
    s.add(lead)
    await s.flush()
    s.add(ChannelThread(lead_id=lead.id, channel_id=cid, external_thread_id=f"ig-{lead.id}",
                        ad_id=ad, ad_media_id="9988"))
    await s.flush()


async def test_ad_funnel_buckets_by_stage(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    db_session.add(ch)
    await db_session.flush()
    # ad A: 2 pipeline + 1 won ; ad B: 1 dormant ; plus 1 organic (no ad) — excluded
    await _lead_from_ad(db_session, b.id, ch.id, "adA", Stage.QUALIFYING)
    await _lead_from_ad(db_session, b.id, ch.id, "adA", Stage.PRESENTING)
    await _lead_from_ad(db_session, b.id, ch.id, "adA", Stage.READY)
    await _lead_from_ad(db_session, b.id, ch.id, "adB", Stage.DORMANT)
    organic = Lead(branch_id=b.id, stage=Stage.NEW)
    db_session.add(organic)
    await db_session.flush()
    db_session.add(ChannelThread(lead_id=organic.id, channel_id=ch.id,
                                 external_thread_id="ig-x", ad_id=None))
    await db_session.flush()

    rows = {r[0]: r for r in await fetch_ad_funnel(db_session, [b.id])}
    assert set(rows) == {"adA", "adB"}  # organic excluded
    _ad, _media, total, pipeline, won, dormant = rows["adA"]
    assert (total, pipeline, won, dormant) == (3, 2, 1, 0)
    assert rows["adB"][2:] == (1, 0, 0, 1)


async def test_ad_funnel_branch_scoped(db_session) -> None:
    a = Branch(name="A", lang="id")
    b = Branch(name="B", lang="id")
    db_session.add(a)
    db_session.add(b)
    await db_session.flush()
    cha = Channel(branch_id=a.id, kind=ChannelKind.INSTAGRAM)
    chb = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    db_session.add(cha)
    db_session.add(chb)
    await db_session.flush()
    await _lead_from_ad(db_session, a.id, cha.id, "adA", Stage.QUALIFYING)
    await _lead_from_ad(db_session, b.id, chb.id, "adB", Stage.QUALIFYING)
    rows = await fetch_ad_funnel(db_session, [a.id])
    assert [r[0] for r in rows] == ["adA"]  # branch B's ad not visible


def test_ad_funnel_html_renders_link_and_conv() -> None:
    from app.api._i18n import _lang
    _lang.set("en")
    html = _ad_funnel_html([("act_123", "9988", 4, 2, 1, 1)])
    assert "act_123" in html
    assert "adsmanager.facebook.com" in html
    assert "25.0%" in html  # 1 won / 4 total
    assert _ad_funnel_html([]) == ""
