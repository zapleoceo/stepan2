"""Русская половина алерта должна быть на русском — включая слова лида.

Менеджер читает уведомление, чтобы решить, идти ли в чат. Реплика лида приходила в нём
дословно на индонезийском в ОБЕИХ половинах, поэтому единственная строка, ради которой
уведомление и посылается, была нечитаемой — и приходилось открывать чат, чтобы понять, о чём
вообще речь.
"""
from __future__ import annotations

from app.modules.notifications.summarize import AlertBody, build_alert_body


class _LLM:
    """Отвечает как брокер, но без сети."""

    def __init__(self, reply: str = "перевод") -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    async def chat(self, msgs, **_kw):  # noqa: ANN001, ANN003, ANN202
        self.calls.append(msgs)
        return self.reply, {}


class _BoomLLM:
    async def chat(self, *_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("broker down")


async def _thread_with_one_message(s) -> int:  # noqa: ANN001
    from datetime import UTC, datetime

    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
    from app.domain.enums import ChannelKind

    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.WHATSAPP, handle="WA Citra")
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=b.id)
    s.add(lead)
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="wa1")
    s.add(th)
    await s.flush()
    s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, direction="in",
                  text="kalau sosial media marketing jatuhnya agak switch career sih",
                  external_id="m1", occurred_at=datetime.now(UTC).replace(tzinfo=None)))
    await s.flush()
    return th.id


async def test_the_leads_words_are_translated_even_without_a_summary(db_session) -> None:  # noqa: ANN001
    """Пересказывать нечего — в треде одна реплика. Но перевести её всё равно надо: отказ
    из-за того, что бо́льшая работа невозможна, и оставлял русскую половину индонезийской."""
    tid = await _thread_with_one_message(db_session)
    llm = _LLM("если соцсети — это уже смена профессии")

    body = await build_alert_body(db_session, llm, tid, branch_lang="id",
                                  reason_en="Bot is OFF", reason_ru="Бот выключен")

    assert body.last_msg_ru == "если соцсети — это уже смена профессии"
    assert "kalau sosial media" in body.last_msg  # дословная реплика тоже на месте


async def test_a_broken_translator_still_lets_the_alert_out(db_session) -> None:  # noqa: ANN001
    """Уведомление без перевода хуже, чем с ним. Уведомление, которое не пришло, хуже обоих."""
    tid = await _thread_with_one_message(db_session)

    body = await build_alert_body(db_session, _BoomLLM(), tid, branch_lang="id",
                                  reason_en="Bot is OFF", reason_ru="Бот выключен")

    assert body.last_msg_ru == ""
    assert "kalau sosial media" in body.last_msg


async def test_nothing_is_translated_when_there_is_nothing_to_say(db_session) -> None:  # noqa: ANN001
    llm = _LLM()
    body = await build_alert_body(db_session, llm, None, branch_lang="id",
                                  reason_en="x", reason_ru="х")
    assert body == AlertBody("", "", "x", "", "")
    assert llm.calls == []


def test_the_reason_line_no_longer_carries_the_untranslated_message() -> None:
    """Реплика уже процитирована над строкой причины в обеих половинах. Вставленная ещё и
    сюда, она попадала в русскую часть непереведённой."""
    import inspect

    from app.modules.leads import ingest

    src = inspect.getsource(ingest.IngestService._notify_bot_off)  # noqa: SLF001
    assert "{snippet}" not in src
    assert "llm=_alert_llm()" in inspect.getsource(ingest.IngestService._notify_bot_off)  # noqa: SLF001
