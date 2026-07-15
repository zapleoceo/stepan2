"""Ad-funnel report: per-ad lead counts bucketed by stage (ORM query runs on SQLite)."""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
from app.api._query import fetch_ad_funnel
from app.api._ui_panels import _ad_tree_html
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


async def test_ad_funnel_scoped_by_since_until(db_session) -> None:
    """The reports date-range/quick-range filter must also scope the per-ad funnel table,
    not just the KPIs above it — leads outside [since, until) are excluded."""
    from datetime import datetime

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    db_session.add(ch)
    await db_session.flush()
    old_lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING,
                     created_at=datetime(2026, 1, 1))
    new_lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING,
                     created_at=datetime(2026, 6, 1))
    db_session.add_all([old_lead, new_lead])
    await db_session.flush()
    db_session.add_all([
        ChannelThread(lead_id=old_lead.id, channel_id=ch.id, external_thread_id="ig-old",
                     ad_id="adOld"),
        ChannelThread(lead_id=new_lead.id, channel_id=ch.id, external_thread_id="ig-new",
                     ad_id="adNew"),
    ])
    await db_session.flush()

    rows = await fetch_ad_funnel(
        db_session, [b.id], since=datetime(2026, 3, 1), until=datetime(2026, 12, 1))
    assert [r[0] for r in rows] == ["adNew"]


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
    html = _ad_tree_html([("act_123", "9988", 4, 2, 1, 1)], {}, {})
    assert "act_123" in html
    assert "facebook.com/ads/library/?id=act_123" in html  # Ad Library, not Ads Manager
    assert "25.0%" in html  # 1 won / 4 total
    assert _ad_tree_html([], {}, {}) == ""


def test_ad_link_opens_the_ad_library_by_id() -> None:
    """The FB link goes to the public Ad Library keyed by ad id — it resolves any live ad
    regardless of which ad account owns it, unlike an Ads Manager account-scoped deep link."""
    from app.api._i18n import _lang
    _lang.set("en")
    html = _ad_tree_html([("120255671613970771", None, 3, 1, 1, 0)], {}, {},
                          None, "949920286532207", "1000480912055519")
    assert "facebook.com/ads/library/?id=120255671613970771" in html
    assert "adsmanager.facebook.com" not in html   # account-scoped deep link abandoned
    assert "selected_ad_ids" not in html


def test_reports_panel_drops_by_stage_table_and_shows_message_stats() -> None:
    from app.api._i18n import _lang
    from app.api._ui_panels import reports_panel_html
    _lang.set("en")
    html = reports_panel_html(
        stage_counts={"new": 3, "qualifying": 2, "ready": 1},
        hour_in={9: 4, 10: 6}, hour_out={9: 5, 11: 3},
    )
    # the duplicate "By stage" breakdown table is gone; the one-line funnel stays
    assert "rep-tbl" not in html
    assert "fnl-line" in html
    # period message totals: in=10, out=8, total=18
    assert ">10<" in html and ">8<" in html and ">18<" in html
    # hourly chart still present (in/out bars)
    assert "hchart" in html
