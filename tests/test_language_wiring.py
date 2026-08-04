"""The leaf functions now take a language. This file proves the pipeline actually hands one in.

Every test in test_language_leaks.py and test_price_locales.py calls the leaf directly with an
explicit argument, so all of them would still pass if `annotate_dates(…, branch_lang)`,
`country_code=self._country_code()` and `money_issues(…, branch_lang)` were deleted from the
services — the wiring, which is the part that was actually broken, was untested. Each test here
fails if its one argument stops being passed.

It also pins the distinction the money parser got wrong: the language we WRITE in is the lead's
(their own script wins), the language we PRICE in is the branch's. A lead who writes to branch 1
in Russian is answered in Russian and still quoted rupiah out of an Indonesian catalogue.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.canned import escalation_hold
from app.modules.conversation.engine import DecisionEngine
from app.modules.conversation.reply import ReplyService
from app.modules.settings.service import BranchSettings

_NOW = datetime.now(UTC).replace(tzinfo=None)
_ID_KB = "KB FACTS: Vibe Coding — DP Rp 500.000, total Rp 13.360.000. Batch: 8 Agustus 2030"
_EN_KB = "KB FACTS: Vibe Coding — $1,500 in total, $250 deposit. Next batch: August 8, 2030"


class _LLM:
    def __init__(self, *answers: str) -> None:
        self._answers = list(answers) or [_answer()]
        self.messages: list[list[dict]] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        if kw.get("workflow") == "discovery":
            return '{"job_to_be_done":"","pains":[],"desired_state":[],"objections":[]}', \
                {"model": "fake", "cost_usd": 0.0}
        self.messages.append(messages)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return answer, {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


class _Knowledge:
    def __init__(self, context: str) -> None:
        self._context = context

    async def knowledge_context(self, product_slug, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._context

    async def full_knowledge_context(self, lang=None):  # noqa: ANN001, ANN201
        return self._context

    async def objection_snippets(self, categories):  # noqa: ANN001, ANN201
        return ""

    async def market_snippets(self, categories):  # noqa: ANN001, ANN201
        return ""


def _answer(**over) -> str:  # noqa: ANN003
    payload = {"reply": "halo kak", "stage": "qualifying"}
    payload.update(over)
    return json.dumps(payload)


async def _thread(s, *, lang: str, inbound: str) -> tuple[int, int]:  # noqa: ANN001
    b = Branch(name=f"B-{lang}", lang=lang)
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id=f"ig-{lang}")
    s.add(th)
    await s.flush()
    for i, (direction, text) in enumerate((("out", "hi"), ("in", inbound))):
        s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id=f"w{i}",
                      direction=direction, sent_by="lead" if direction == "in" else "bot",
                      text=text, occurred_at=_NOW))
    await s.flush()
    return b.id, th.id


def _cfg(**over) -> BranchSettings:  # noqa: ANN003
    base = dict(
        agent_enabled=True, hourly_cap=0, daily_cap=0, quiet_start=0, quiet_end=0,
        reply_delay_min_s=0, reply_delay_max_s=0, tz_offset_h=7, tg_group_id="",
        followup_enabled=False, followup_schedule_h=[], daily_budget_usd=0.0,
        crm_enabled=False, crm_webhook_url="", meta_pixel_id="", meta_capi_token="",
    )
    base.update(over)
    return BranchSettings(**base)


def _service(session, branch_id: int, llm: _LLM, kb: str,  # noqa: ANN001
             settings: BranchSettings | None = None) -> ReplyService:
    return ReplyService(session, branch_id, llm, _Knowledge(kb), settings)


# ── (a) the engine hands the branch language to the date annotator ────────────

_EN_ANNOTATION = re.compile(r"\[(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ")


async def test_the_engine_annotates_dates_in_the_branch_s_own_language(db_session) -> None:  # noqa: ANN001
    """annotate_dates is called from free_kb_context, not from the golden's assembly path, so
    nothing pinned this. An English branch was handed 'hari Sabtu, 16 hari lagi' inside its own
    English knowledge base and then told not to translate dates."""
    bid, _ = await _thread(db_session, lang="en", inbound="when does it start?")
    engine = DecisionEngine(db_session, bid, _LLM(), _Knowledge(_EN_KB))

    context = await engine.free_kb_context()

    assert _EN_ANNOTATION.search(context), context
    assert "hari" not in context


async def test_branch_one_still_reads_its_dates_in_bahasa(db_session) -> None:  # noqa: ANN001
    """The other half of the same wire: the cached prefix branch 1 has been selling against."""
    bid, _ = await _thread(db_session, lang="id", inbound="kapan mulai?")
    engine = DecisionEngine(db_session, bid, _LLM(), _Knowledge(_ID_KB))

    context = await engine.free_kb_context()

    assert re.search(r"\[hari \w+, \d+ hari lagi\]", context), context


# ── (b) the reply path hands the dialling code to the phone stripper ──────────

async def test_the_reply_path_strips_a_number_in_the_branch_s_own_country(db_session) -> None:  # noqa: ANN001
    """clean_reply only recognises a phone number once it knows the country. The code was
    hardcoded to Indonesia's, so a Malaysian branch shipped invented Malaysian numbers — and
    the unit test for it calls clean_reply directly, so it could not see this wire."""
    bid, tid = await _thread(db_session, lang="en", inbound="how do I reach you?")
    llm = _LLM(_answer(reply="Sure!\nCall us on +60 12 345 6789"))

    decision = await _service(db_session, bid, llm, _EN_KB,
                              _cfg(phone_country_code="60")).decide(tid)

    assert decision is not None
    assert "+60" not in decision.reply
    assert "Sure!" in decision.reply


async def test_the_same_number_survives_on_a_branch_that_does_not_dial_it(db_session) -> None:  # noqa: ANN001
    """Branch 1's behaviour, unchanged: a +60 number is not a phone shape it knows, and the
    default has always been Indonesia's."""
    bid, tid = await _thread(db_session, lang="id", inbound="nomornya berapa kak?")
    llm = _LLM(_answer(reply="Siap kak\nHubungi +60 12 345 6789 ya"))

    decision = await _service(db_session, bid, llm, _ID_KB).decide(tid)

    assert decision is not None and "+60" in decision.reply


# ── (c) the money gate is asked in the BRANCH's currency, not the reply's ─────

async def test_a_fabricated_dollar_price_escalates_on_an_english_branch(db_session) -> None:  # noqa: ANN001
    """The end of the wire the whole step exists for. money_issues had to be handed a language
    by decide(); tested only at the leaf, deleting that argument left the suite green and the
    gate blind on every branch that does not sell in rupiah."""
    bid, tid = await _thread(db_session, lang="en", inbound="how much is it?")
    bad = _answer(reply="The full course is $2,900.")
    llm = _LLM(bad, bad)  # the rewrite repeats the invented figure

    decision = await _service(db_session, bid, llm, _EN_KB).decide(tid)

    assert decision is not None
    assert decision.needs_manager
    assert decision.reply == escalation_hold("en")


async def test_a_grounded_dollar_price_ships_on_an_english_branch(db_session) -> None:  # noqa: ANN001
    """A gate that blocks everything is as useless as one that blocks nothing."""
    bid, tid = await _thread(db_session, lang="en", inbound="how much is it?")
    llm = _LLM(_answer(reply="It's $1,500 in total, or $250 to start."))

    decision = await _service(db_session, bid, llm, _EN_KB).decide(tid)

    assert decision is not None and not decision.needs_manager
    assert decision.reply.startswith("It's $1,500")


async def test_a_russian_speaking_lead_on_branch_one_is_still_priced_in_rupiah(db_session) -> None:  # noqa: ANN001
    """The lead's script decides what we WRITE in; it must not decide what we PRICE in.

    _script_lang flips any Cyrillic-writing lead to 'ru' — on branch 1, where the catalogue and
    the knowledge base are still Indonesian. Reading a rupiah figure with another market's
    rules turned a grounded price into an invented one, and dropped IT STEP's own catalogue
    reminder out of the rewrite instruction."""
    bid, tid = await _thread(db_session, lang="id", inbound="Сколько стоит курс?")
    llm = _LLM(_answer(reply="Полная стоимость Rp 13.360.000, предоплата Rp 500.000"))

    decision = await _service(db_session, bid, llm, _ID_KB).decide(tid)

    assert decision is not None and not decision.needs_manager
    assert decision.reply.startswith("Полная стоимость")


async def test_branch_one_reads_a_russian_reply_with_indonesian_money_rules(db_session) -> None:  # noqa: ANN001
    """The half of that wire a rupiah figure cannot pin, and the reason this test exists.

    The two rows now agree on every rupiah shape, so a rupiah reply passes the gate whichever
    row is asked — only a currency branch 1 does not sell in can tell them apart. '$' is in the
    fallback row and not in the Indonesian one, which is branch 1's behaviour in production:
    money_issues took no language at all before this step and read every branch with the
    Indonesian row, where a dollar figure is not a quote from our catalogue.

    Keyed on the REPLY's language instead, one Cyrillic word moves branch 1 to the fallback
    row, a third-party tool's subscription becomes a price of 15, the Indonesian knowledge base
    does not contain it, and a correct grounded answer is replaced by the hold-line."""
    bid, tid = await _thread(db_session, lang="id", inbound="Сколько стоит курс?")
    grounded = _answer(reply="Полная стоимость Rp 13.360.000. Figma отдельно, $15 в месяц.")
    llm = _LLM(grounded, grounded)

    decision = await _service(db_session, bid, llm, _ID_KB).decide(tid)

    assert decision is not None and not decision.needs_manager
    assert "$15" in decision.reply
    assert len(llm.messages) == 1  # the gate never tripped: no correction round-trip


async def test_the_rewrite_still_names_the_tenant_catalogue_for_a_russian_lead(db_session) -> None:  # noqa: ANN001
    """Which of our offers is free is the TENANT's catalogue, not a fact about the language a
    single lead happens to write in. Keyed on the reply language it vanished for exactly the
    leads branch 1 has the least data on."""
    bid, tid = await _thread(db_session, lang="id", inbound="Сколько стоит?")
    bad = _answer(reply="Стоимость Rp 26.000.000")
    llm = _LLM(bad, bad)

    await _service(db_session, bid, llm, _ID_KB).decide(tid)

    rewrite = llm.messages[-1][-1]["content"]
    assert "Demo Event" in rewrite
