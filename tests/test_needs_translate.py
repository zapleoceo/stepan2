"""needs_translate: auto-translate captured needs (jobs/pains/gains) into the current UI
language, cached per (original phrase, language) on lead.needs_tr — never re-bill a phrase
already translated for that language, and degrade to the original text on broker failure."""
from __future__ import annotations

import json

from app.modules.conversation.needs import NeedsProfile
from app.modules.conversation.needs_translate import translated_needs


class _EchoLLM:
    """Returns a deterministic '<lang>:<phrase>' translation for every input line."""

    def __init__(self, lang_tag: str = "RU") -> None:
        self.lang_tag = lang_tag
        self.calls = 0
        self.last_items: list[str] | None = None

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        user_msg = messages[-1]["content"]
        lines = [ln.split(". ", 1)[1] for ln in user_msg.splitlines() if ln.strip()]
        self.last_items = lines
        out = [f"{self.lang_tag}:{ln}" for ln in lines]
        return json.dumps(out), {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


class _FailingLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        raise RuntimeError("broker down")

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


async def test_first_call_translates_everything_and_returns_cache_to_persist() -> None:
    profile = NeedsProfile(jobs=["belajar coding"], pains=["takut gagal"], gains=["kerja stabil"])
    llm = _EchoLLM()
    translated, new_tr = await translated_needs(profile, None, "ru", llm)
    assert translated.jobs == ["RU:belajar coding"]
    assert translated.pains == ["RU:takut gagal"]
    assert translated.gains == ["RU:kerja stabil"]
    assert llm.calls == 1
    assert new_tr is not None
    cached = json.loads(new_tr)["ru"]
    assert cached["belajar coding"] == "RU:belajar coding"


async def test_cache_hit_never_calls_the_broker_again() -> None:
    profile = NeedsProfile(jobs=["belajar coding"])
    llm = _EchoLLM()
    _, new_tr = await translated_needs(profile, None, "ru", llm)
    assert llm.calls == 1
    # second render, same phrase, same language — must be a pure cache hit
    translated2, new_tr2 = await translated_needs(profile, new_tr, "ru", llm)
    assert llm.calls == 1  # no new broker call
    assert new_tr2 is None  # nothing to persist — caller must skip the DB write
    assert translated2.jobs == ["RU:belajar coding"]


async def test_new_item_only_translates_the_delta() -> None:
    """merge_needs grows the lists over the conversation — only the NEW phrase should cost
    a broker call; previously-cached phrases must not be re-sent."""
    old_profile = NeedsProfile(jobs=["belajar coding"])
    llm = _EchoLLM()
    _, cache_v1 = await translated_needs(old_profile, None, "ru", llm)

    grown_profile = NeedsProfile(jobs=["belajar coding", "bikin startup"])
    _, cache_v2 = await translated_needs(grown_profile, cache_v1, "ru", llm)
    assert llm.calls == 2
    assert llm.last_items == ["bikin startup"]  # only the delta was sent
    cached = json.loads(cache_v2)["ru"]
    assert cached["belajar coding"] == "RU:belajar coding"  # old entry preserved
    assert cached["bikin startup"] == "RU:bikin startup"


async def test_different_languages_cache_independently() -> None:
    profile = NeedsProfile(pains=["takut gagal"])
    _, cache_ru = await translated_needs(profile, None, "ru", _EchoLLM("RU"))
    translated_en, cache_both = await translated_needs(profile, cache_ru, "en", _EchoLLM("EN"))
    assert translated_en.pains == ["EN:takut gagal"]
    d = json.loads(cache_both)
    assert d["ru"]["takut gagal"] == "RU:takut gagal"
    assert d["en"]["takut gagal"] == "EN:takut gagal"


async def test_broker_failure_degrades_to_original_and_does_not_poison_the_cache() -> None:
    profile = NeedsProfile(gains=["masa depan cerah"])
    translated, new_tr = await translated_needs(profile, None, "ru", _FailingLLM())
    assert translated.gains == ["masa depan cerah"]  # shown untranslated, not blank/crashed
    assert new_tr is None  # nothing cached — next render retries instead of freezing


async def test_empty_profile_short_circuits_without_a_broker_call() -> None:
    llm = _EchoLLM()
    translated, new_tr = await translated_needs(NeedsProfile(), None, "ru", llm)
    assert translated == NeedsProfile()
    assert new_tr is None
    assert llm.calls == 0
