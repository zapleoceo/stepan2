"""Сопоставление объявления продукту достаёт и тех, кто пришёл раньше него.

Лид приходит по рекламе, продукт которой ещё не сопоставлен, — и остаётся без продукта
навсегда: привязка применялась только к новым тредам, назад никто не смотрел. На 31.07.2026
так накопилось 208 тредов из 2567 рекламных, 8%, и ВСЕ 208 старше своей же привязки. Живой
путь при этом исправен: тредов, созданных после привязки и без продукта, ноль.

Цена не в отчётах, а в разговоре: без продукта модель не получает карточку курса, по которому
человек пришёл, и выясняет то, что реклама уже сказала.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
from app.domain.enums import ChannelKind
from app.modules.ads.mapping import AdMappingService

_AD = "120255671613970771"


async def _thread(session, branch_id: int, channel_id: int, *,  # noqa: ANN001
                  ad_id: str | None, slug: str | None = None,
                  source: str | None = None) -> int:
    lead = Lead(branch_id=branch_id, stage="qualifying")
    session.add(lead)
    await session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=channel_id,
                           external_thread_id=f"ig-{lead.id}", ad_id=ad_id,
                           product_slug=slug, product_source=source)
    session.add(thread)
    await session.flush()
    return thread.id


async def _fixture(session):  # noqa: ANN001, ANN201
    branch = Branch(name="T", lang="id")
    session.add(branch)
    await session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    session.add(channel)
    await session.flush()
    return branch.id, channel.id


async def test_threads_that_predate_the_mapping_get_the_product(db_session) -> None:
    bid, cid = await _fixture(db_session)
    old = await _thread(db_session, bid, cid, ad_id=_AD)
    await AdMappingService(db_session, bid).upsert(_AD, "smm_intensive", actor="Dima")

    row = await db_session.get(ChannelThread, old)
    assert row.product_slug == "smm_intensive"
    assert row.product_source == "ad"


async def test_a_product_the_model_or_a_manager_chose_is_never_overwritten(db_session) -> None:
    """Они знают о лиде больше, чем объявление, по которому он кликнул. Перетирать их
    решение задним числом — потерять то, что выяснили в разговоре."""
    bid, cid = await _fixture(db_session)
    by_model = await _thread(db_session, bid, cid, ad_id=_AD,
                             slug="vibe_coding", source="model")
    by_manager = await _thread(db_session, bid, cid, ad_id=_AD,
                               slug="python_backend", source="manager")
    await AdMappingService(db_session, bid).upsert(_AD, "smm_intensive", actor="Dima")

    assert (await db_session.get(ChannelThread, by_model)).product_slug == "vibe_coding"
    assert (await db_session.get(ChannelThread, by_manager)).product_slug == "python_backend"


async def test_other_ads_are_untouched(db_session) -> None:
    bid, cid = await _fixture(db_session)
    other = await _thread(db_session, bid, cid, ad_id="999999")
    await AdMappingService(db_session, bid).upsert(_AD, "smm_intensive", actor="Dima")
    assert (await db_session.get(ChannelThread, other)).product_slug is None


async def test_threads_without_an_ad_are_untouched(db_session) -> None:
    bid, cid = await _fixture(db_session)
    organic = await _thread(db_session, bid, cid, ad_id=None)
    await AdMappingService(db_session, bid).upsert(_AD, "smm_intensive", actor="Dima")
    assert (await db_session.get(ChannelThread, organic)).product_slug is None
