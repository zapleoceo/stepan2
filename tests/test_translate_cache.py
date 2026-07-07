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
        self.last_messages = messages
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


def test_target_for_lang_maps_ui_codes() -> None:
    """UI language code -> the target name the translate prompt uses. Two of the three
    call sites used to hardcode 'Russian' regardless of the viewer's UI language — this
    is the single shared mapping all of them must use now."""
    from app.modules.conversation.translate import target_for_lang
    assert target_for_lang("ru") == "Russian"
    assert target_for_lang("en") == "English"
    assert target_for_lang("id") == "Indonesian"
    assert target_for_lang("xx") == "Russian"  # unknown code -> safe default


def test_system_prompt_never_assumes_source_is_target() -> None:
    """Real failure: 'sok' (Indonesian slang) got 'translated' as the lookalike Russian
    word 'сок' (juice), and 'kasih tau' got an outright refusal ('this isn't Russian').
    The prompt must explicitly rule out both failure modes."""
    from app.modules.conversation.translate import _system_prompt
    prompt = _system_prompt("Russian")
    assert "never" in prompt.lower() and "refuse" in prompt.lower()
    assert "lookalike" in prompt.lower() or "NEVER already written" in prompt
    assert "{target}" not in prompt  # regression: an unformatted literal braces bug


def test_system_prompt_forbids_chat_assistant_collapse() -> None:
    """Real failure: 'Kyaknya aku ikut offline kak soalnya kerjaa' made the model drop the
    translate task entirely and answer as a generic chat assistant ('How can I help? I
    don't understand...'). The prompt must explicitly forbid treating the input as a live
    message addressed to the model."""
    from app.modules.conversation.translate import _system_prompt
    prompt = _system_prompt("Russian")
    assert "not a chat assistant" in prompt.lower()
    assert "data" in prompt.lower()
    assert "never respond" in prompt.lower()


async def test_translate_text_wraps_input_in_delimiters() -> None:
    """Delimiting the user-turn content reinforces that it's a DATA block to transform,
    not a live chat turn — part of the fix for the chat-assistant-collapse failure above."""
    from app.modules.conversation.translate import translate_text
    llm = CountingLLM("перевод")
    body = "Kyaknya aku ikut offline kak soalnya kerjaa"
    await translate_text(llm, body)
    user_content = llm.last_messages[1]["content"]
    assert user_content == f"'''{body}'''"


async def test_translate_text_strips_leaked_delimiters() -> None:
    """Real failure: despite being told not to, the live model echoed the ''' delimiters
    back around its translation ("''Я буду отключен...''"). translate_text must strip
    them rather than leaking prompt scaffolding into the stored/displayed translation."""
    from app.modules.conversation.translate import translate_text
    llm = CountingLLM("''перевод текста''")
    assert await translate_text(llm, "halo") == "перевод текста"


# ─── retry when chat:fast silently doesn't translate (thread 2161) ──────────────

class _SequenceLLM:
    """Returns one output per call, in order; records the capability asked for each time."""

    def __init__(self, *outs: str) -> None:
        self._q = list(outs)
        self.caps: list[str] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.caps.append(kw.get("capability"))
        return self._q.pop(0), {"model": "fake", "cost_usd": 0.001}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


async def test_looks_translated_flags_untranslated_russian_output() -> None:
    """Live case: cohere/command-r7b-12-2024 returned the Indonesian source basically
    unchanged (once even switched to Chinese) when asked to translate to Russian — 0 of 5
    sampled attempts produced real Cyrillic."""
    from app.modules.conversation.translate import _looks_translated
    assert not _looks_translated("Halo! Senang hearing itu Kakak tertarik sama SMM", "Russian")
    assert _looks_translated("Привет! Рад, что тебе интересно", "Russian")
    # only checked for a Russian target — English/Indonesian share the Latin script
    assert _looks_translated("Hello there", "English")


async def test_translate_text_retries_on_smart_when_fast_fails_to_translate() -> None:
    from app.modules.conversation.translate import translate_text
    # chat:fast echoes the Indonesian source back untranslated; chat:smart gets it right
    llm = _SequenceLLM("Halo! masih Indonesia aja", "Привет! уже по-русски")
    out = await translate_text(llm, "halo apa kabar", target="Russian")
    assert out == "Привет! уже по-русски"
    assert llm.caps == ["chat:fast", "chat:smart"]


async def test_translate_text_no_retry_when_fast_already_translated() -> None:
    from app.modules.conversation.translate import translate_text
    llm = _SequenceLLM("Привет, как дела")
    out = await translate_text(llm, "halo apa kabar", target="Russian")
    assert out == "Привет, как дела"
    assert llm.caps == ["chat:fast"]  # no wasted smart-model call

