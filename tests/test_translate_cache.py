"""Кэш переводов помнит, НА КАКОЙ язык он переведён.

Колонка была одна и без языка, поэтому отвечала любому языку тем, что попросили первым:
пузырь, переведённый при английском интерфейсе, возвращался по-английски админу, читающему
по-русски. Выглядит как переводчик, который тебя игнорирует, а не как кэш.
"""
from __future__ import annotations

from app.api._ui_html import chat_panel_html  # noqa: F401 — ensures the module imports


def _js() -> str:
    from app.api._ui_html import app_shell
    return app_shell("ru", "", active_nav="inbox")


# ── очередь переводов ─────────────────────────────────────────────────────────


def test_translate_all_starts_from_the_newest_message() -> None:
    """Оператор смотрит вниз чата. Перевод «сверху» тратит первую минуту на сообщения
    трёхнедельной давности, пока строка, ради которой нажали, стоит непереведённой."""
    assert ".reverse()" in _js()


def test_translate_all_runs_a_bounded_pool_not_one_at_a_time() -> None:
    """По одному — минуты на длинном чате. Пачкой без ограничения — брокер отвечает
    частично, и часть переводов не возвращается."""
    js = _js()
    assert "_TRPAR=4" in js
    assert "for(var w=0;w<_TRPAR" in js


def test_every_translation_is_still_awaited_and_retried_once() -> None:
    """Ограниченный параллелизм не должен превратиться в «выстрелил и забыл»: счётчик
    закрывается только когда ответ получен, а неудача повторяется один раз."""
    js = _js()
    assert "trMsg(mid,tid,true).then" in js
    assert "if(!ok2)failed++" in js


# ── язык в ключе кэша ─────────────────────────────────────────────────────────


class _LLM:
    def __init__(self, answer: str = "переведено") -> None:
        self.answer = answer
        self.calls = 0

    async def complete(self, *_a, **_kw) -> str:  # noqa: ANN002, ANN003
        self.calls += 1
        return self.answer


async def _msg(s, text_: str, tr: str | None, lang: str | None) -> int:  # noqa: ANN001
    from datetime import UTC, datetime

    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
    from app.domain.enums import ChannelKind

    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=b.id)
    s.add(lead)
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="x")
    s.add(th)
    await s.flush()
    m = Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, direction="in",
                text=text_, tr_text=tr, tr_lang=lang, external_id="e1",
                occurred_at=datetime.now(UTC).replace(tzinfo=None))
    s.add(m)
    await s.flush()
    return m.id


async def test_a_translation_in_another_language_is_not_served(db_session, monkeypatch) -> None:  # noqa: ANN001
    from app.modules.conversation import translate as tr

    mid = await _msg(db_session, "halo kak", "hello there", "English")
    llm = _LLM("привет")
    monkeypatch.setattr(tr, "translate_text", lambda *a, **k: _done("привет"))

    out = await tr.translate_message(db_session, mid, llm, target="Russian")

    assert out == "привет"


async def test_a_translation_in_the_asked_language_costs_nothing(db_session, monkeypatch) -> None:  # noqa: ANN001
    """Смысл кэша — не платить дважды за один и тот же пузырь."""
    from app.modules.conversation import translate as tr

    mid = await _msg(db_session, "halo kak", "привет", "Russian")
    called = []
    monkeypatch.setattr(tr, "translate_text",
                        lambda *a, **k: called.append(1) or _done("новое"))

    assert await tr.translate_message(db_session, mid, _LLM(), target="Russian") == "привет"
    assert called == []


async def test_a_row_cached_before_languages_were_recorded_is_trusted(  # noqa: ANN001
    db_session, monkeypatch,
) -> None:
    """Существующие переводы делались на языке админки филиала. Перебилливать их все, чтобы
    это выяснить, дороже, чем один раз довериться."""
    from app.modules.conversation import translate as tr

    mid = await _msg(db_session, "halo kak", "привет", None)
    called = []
    monkeypatch.setattr(tr, "translate_text",
                        lambda *a, **k: called.append(1) or _done("новое"))

    assert await tr.translate_message(db_session, mid, _LLM(), target="Russian") == "привет"
    assert called == []


async def _done(value: str):  # noqa: ANN202
    return value
