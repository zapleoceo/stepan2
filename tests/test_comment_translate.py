"""Comments panel translates questions + replies to the UI language and caches the result
(never re-bills a translation), mirroring message.tr_text."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.adapters.db.models import Branch, Channel, PostComment
from app.api._routes_comments import _cached, _ensure_translations, _merge_cache
from app.domain.enums import ChannelKind


class _CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        return "переведено", {"model": "x", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003
        return [[0.0] for _ in texts]


def test_cache_helpers_roundtrip() -> None:
    raw = _merge_cache(None, "ru", "привет")
    raw = _merge_cache(raw, "en", "hi")
    assert _cached(raw, "ru") == "привет"
    assert _cached(raw, "en") == "hi"
    assert _cached(raw, "id") is None
    assert _cached(None, "ru") is None


async def _seed(s) -> tuple[int, int]:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="x")
    s.add(ch)
    await s.flush()
    pc = PostComment(branch_id=b.id, channel_id=ch.id, external_id="c1", media_id="m1",
                     text="berapa harganya?", reply_text="Rp 1.882.955",
                     occurred_at=datetime(2026, 7, 20, 10, 0), status="replied")
    s.add(pc)
    await s.flush()
    return b.id, pc.id


async def _rows(session, bid):  # noqa: ANN001, ANN201
    return list((await session.execute(text(
        "SELECT id, text, reply_text, text_tr, reply_tr FROM post_comment"
        " WHERE branch_id=:b"), {"b": bid})).all())


async def test_translates_and_caches(db_session) -> None:
    bid, cid = await _seed(db_session)
    llm = _CountingLLM()
    trs = await _ensure_translations(db_session, await _rows(db_session, bid), "ru", llm)
    assert trs[cid]["text"] == "переведено"
    assert trs[cid]["reply"] == "переведено"
    assert llm.calls == 2  # question + reply

    # second render: cache hit, no new LLM calls
    llm2 = _CountingLLM()
    trs2 = await _ensure_translations(db_session, await _rows(db_session, bid), "ru", llm2)
    assert trs2[cid]["text"] == "переведено"
    assert llm2.calls == 0  # served from post_comment.text_tr / reply_tr


async def test_indonesian_ui_skips_translation(db_session) -> None:
    bid, _cid = await _seed(db_session)
    llm = _CountingLLM()
    trs = await _ensure_translations(db_session, await _rows(db_session, bid), "id", llm)
    assert trs == {} and llm.calls == 0  # source is already Indonesian
