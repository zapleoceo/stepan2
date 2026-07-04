"""Per-message translation cache: first call translates + stores, later calls are free."""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind
from app.modules.conversation.translate import translate_message

_NOW = datetime.now(UTC).replace(tzinfo=None)


class CountingLLM:
    def __init__(self, out: str = "перевод") -> None:
        self.out = out
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        self.last_kw = kw
        return self.out, {"model": "fake", "cost_usd": 0.001}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


async def _msg(s, *, text: str = "halo apa kabar", tr: str | None = None) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    s.add(ch)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    m = Message(branch_id=b.id, thread_id=thread.id, channel_id=ch.id, external_id="m1",
                direction="in", sent_by="lead", text=text, tr_text=tr, occurred_at=_NOW)
    s.add(m)
    await s.flush()
    return m.id


async def test_first_call_translates_and_caches(db_session) -> None:
    mid = await _msg(db_session)
    llm = CountingLLM("привет как дела")
    assert await translate_message(db_session, mid, llm) == "привет как дела"
    assert llm.calls == 1
    # persisted on the row
    from sqlalchemy import text
    stored = (await db_session.execute(
        text("SELECT tr_text FROM message WHERE id=:m"), {"m": mid})).scalar()
    assert stored == "привет как дела"


async def test_cache_hit_skips_llm(db_session) -> None:
    mid = await _msg(db_session, tr="уже переведено")
    llm = CountingLLM()
    assert await translate_message(db_session, mid, llm) == "уже переведено"
    assert llm.calls == 0  # no re-bill


async def test_missing_or_empty_message_returns_none(db_session) -> None:
    llm = CountingLLM()
    assert await translate_message(db_session, 999999, llm) is None
    empty = await _msg(db_session, text="")
    assert await translate_message(db_session, empty, llm) is None
    assert llm.calls == 0


async def test_translate_uses_generous_max_tokens() -> None:
    """A real 400-token cap truncated Cyrillic translations mid-sentence (output is
    token-heavy). translate_text must ask for a comfortably large budget."""
    from app.modules.conversation.translate import translate_text
    llm = CountingLLM("перевод")
    await translate_text(llm, "halo apa kabar kak")
    assert llm.last_kw.get("max_tokens", 0) >= 1000
