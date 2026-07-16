"""Reply-guard: ungrounded-URL detection + regenerate-once + safe hand-off on fabrication."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlmodel import select  # noqa: E402

from app.adapters.db.models import AppSetting, Branch  # noqa: E402
from app.modules.conversation import guard  # noqa: E402
from app.modules.conversation.sim import SimService  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

_FAKE_LINK = "https://lab.itstep.id/cybersecurity-practice?access=HANDAYANI2024"


def test_parse_unsupported_line_based() -> None:
    assert guard._parse_unsupported("CLEAN") == []
    assert guard._parse_unsupported("") == []
    assert guard._parse_unsupported("- free lab access\n2. 50% discount") == [
        "free lab access", "50% discount"]


def test_parse_unsupported_tolerates_legacy_json() -> None:
    # a stale guard_verify prompt in the DB still emits JSON — must keep parsing
    assert guard._parse_unsupported('{"unsupported": ["invented link", "fake cert"]}') == [
        "invented link", "fake cert"]
    assert guard._parse_unsupported('```json\n{"unsupported": []}\n```') == []


# ─── deterministic URL grounding ────────────────────────────────────────────────

def test_ungrounded_url_flagged_grounded_allowed() -> None:
    ctx = "Program details. Source: https://itstep.id/vibe-coding is the fact base."
    assert guard.ungrounded_urls(f"cek di {_FAKE_LINK} ya", ctx) == [_FAKE_LINK]  # invented
    assert guard.ungrounded_urls("lihat https://itstep.id/vibe-coding", ctx) == []  # in KB
    assert guard.ungrounded_urls("kunjungi https://itstep.id", ctx) == []  # bare official site


def test_is_risky_detects_offers_and_links() -> None:
    assert guard.is_risky("aku kirim link akses lab gratis ya")
    assert guard.is_risky(f"ini {_FAKE_LINK}")
    # a concrete price is now risky too — chat 452 fabricated "cuma Rp 297.000" with no
    # diskon/promo/gratis trigger word, so a bare figure must reach the LLM verify step
    assert guard.is_risky("Vibe Coding harganya 13 juta, bisa dicicil.")
    assert guard.is_risky("cuma Rp 297.000 aja, murah banget")
    assert not guard.is_risky("Vibe Coding itu program yang seru banget buat belajar coding.")


def test_false_delivery_claims_catches_already_sent_but_not_offers_to_send() -> None:
    # 2026-07-05 50-thread audit: leads left believing a screenshot/dataset arrived when
    # nothing was ever sent (threads 1408, 1721) — Stepan can't attach files or use WhatsApp.
    assert guard.false_delivery_claims("Screenshotnya udah aku kirim via DM ya Kak")
    assert guard.false_delivery_claims("data e-commerce Indonesia udah aku kirim ke WA Kakak tadi")
    # an OFFER to send (not yet done) is a normal, allowed sales move
    assert not guard.false_delivery_claims("Boleh aku kirim link brosurnya ke sini?")
    assert not guard.false_delivery_claims("Mau aku kirim silabus lengkapnya lewat chat ini?")


def test_is_risky_detects_unsourced_alumni_stories() -> None:
    # chat 1827: an improvised story with nothing to back it up if the lead asks to see it
    assert guard.is_risky(
        "aku ingin cerita tentang salah satu alumni kami yang berhasil mengembangkan bisnisnya")
    assert guard.is_risky("ada lulusan kami yang sekarang kerja di startup fintech")
    # a plain course description with no alumni/success-story language at all stays cheap
    assert not guard.is_risky(
        "Vibe Coding itu program 4 bulan buat bikin aplikasi sendiri pakai AI.")


def test_is_risky_detects_unnamed_case_story_not_phrased_as_alumni() -> None:
    # thread 2324: "kita punya case alumni yang berhasil bikin dashboard tracking sales jadi
    # aplikasi mobile" — a fabricated case (Vibe Coding's only real Success Case is Pieter
    # Levels) that evaded the old regex because it isn't phrased as "alumni kami"/"salah
    # satu alumni"; only caught when the lead asked for detail, by which point the false
    # claim had already been sent
    assert guard.is_risky(
        "kita punya case alumni yang berhasil bikin dashboard tracking sales jadi aplikasi "
        "mobile pakai AI")
    assert guard.is_risky("ada peserta yang berhasil switch karir jadi developer dalam sebulan")


# ─── multiple questions / impossible capabilities / wrong channel ──────────────

def test_multiple_questions_flags_two_but_not_one() -> None:
    assert guard.multiple_questions(
        "Kak pernah ngerasa gak dapet engagement? Atau bingung bikin konten yang menarik?")
    assert guard.multiple_questions("Kalau boleh tau, Kakak kerja di bidang apa? Domisili mana?")
    assert not guard.multiple_questions("Kalau boleh tau, Kakak tertarik di bidang apa?")
    # a quoted example script's own "?" doesn't count against the real question to the lead
    assert not guard.multiple_questions("Coba jawab kayak gini: «tertarik gak kak?» ya Kak")


def test_truncate_to_one_question_keeps_first_drops_rest() -> None:
    # live case (threads 2159/2160): the model doubles up on discovery questions even after
    # a regen — trim to the first instead of a full hand-off for a perfectly answerable ask.
    doubled = ("Tentu! Sebelum aku jelaskan lebih detail, apa yang membuatmu tertarik jadi "
               "Data Analyst? Ada tujuan spesifik yang ingin dicapai?")
    trimmed = guard.truncate_to_one_question(doubled)
    assert trimmed == ("Tentu! Sebelum aku jelaskan lebih detail, apa yang membuatmu tertarik "
                        "jadi Data Analyst?")
    assert not guard.multiple_questions(trimmed)


def test_truncate_to_one_question_ignores_quoted_example_marks() -> None:
    reply = "Coba jawab kayak gini: «tertarik gak kak?» — kira-kira Kakak gimana?"
    trimmed = guard.truncate_to_one_question(reply)
    assert trimmed == reply  # only one REAL question mark outside the quote — nothing to cut


def test_truncate_to_one_question_no_question_mark_returns_unchanged() -> None:
    assert guard.truncate_to_one_question("Oke Kak, siap!") == "Oke Kak, siap!"


def test_truncate_to_one_question_keeps_only_first_of_three() -> None:
    triple = "Kakak kerja? Atau kuliah? Atau masih sekolah?"
    trimmed = guard.truncate_to_one_question(triple)
    assert trimmed == "Kakak kerja?"
    assert not guard.multiple_questions(trimmed)


def test_impossible_capability_offers_catches_voice_and_call() -> None:
    assert guard.impossible_capability_offers("aku bisa jelasin lewat voice note kalau mau")
    assert guard.impossible_capability_offers("mending aku telpon langsung kamu aja ya")
    assert not guard.impossible_capability_offers("aku jelasin di sini aja ya Kak lewat chat")


def test_wrong_channel_claims_catches_dm_on_instagram() -> None:
    assert guard.wrong_channel_claims("langsung aja DM aku di Instagram ya Kak")
    assert guard.wrong_channel_claims("chat aku di Instagram aja buat lanjutin")
    assert not guard.wrong_channel_claims("langsung aja tanya di sini ya Kak")


def test_whatsapp_delivery_offers_catches_the_promise_not_just_the_lie() -> None:
    # thread 1721: the bot promised a WhatsApp file delivery it could never fulfil, then
    # repeatedly claimed to have already sent it — false_delivery_claims blocks the LIE,
    # this must block the ORIGINAL PROMISE that started the whole disaster
    assert guard.whatsapp_delivery_offers(
        "Siap Kak! Aku kirim file dataset e-commerce Indonesia via WhatsApp ya. "
        "Boleh aku minta nomor WA Kakak?")
    assert guard.whatsapp_delivery_offers(
        "Makasih Kak! Aku kirim file dataset ke WA Kakak sekarang ya.")
    assert guard.whatsapp_delivery_offers("Boleh aku kirim brosur lengkapnya ke WhatsApp Kakak?")
    assert not guard.whatsapp_delivery_offers("Boleh aku kirim link brosurnya di sini aja ya Kak")


def test_premature_manager_handoff_catches_price_question_answered_in_kb() -> None:
    # thread 2285: "ini gratis ga kak?" escalated to a human even though the Skill
    # Booster price (Rp 700.000/600.000) was right there in the retrieved KB context
    context = "> Quick facts — Cybersecurity Skill Booster: Harga Rp 700.000 offline..."
    assert guard.premature_manager_handoff("ini gratis ga kak?", context)
    assert not guard.premature_manager_handoff("berapa harganya?", "no price figure here")
    # a price context alone doesn't make an enrol question answerable (no payment facts)
    assert not guard.premature_manager_handoff("gimana cara daftarnya?", context)


def test_premature_manager_handoff_catches_payment_question_answered_in_kb() -> None:
    # thread 2664: a HOT lead "saya bayar sekarang atau nunggu?" escalated even though the
    # BCA account + DP + methods are in the FAQ that's in context — losing a lead at payment
    pay_ctx = ("Bank BCA, PT. ITSTEP ACADEMY IND, No. Rek. 5245550101. DP 500rb untuk "
               "amankan seat; transfer, QR, atau kartu.")
    assert guard.premature_manager_handoff("saya bayar sekarang atau nunggu?", pay_ctx)
    assert guard.premature_manager_handoff("gimana cara daftarnya kak?", pay_ctx)
    assert guard.premature_manager_handoff("mau daftar, transfer ke mana?", pay_ctx)
    # a payment question with NO payment facts in context is a genuine gap → still escalate
    assert not guard.premature_manager_handoff("cara bayar gimana?", "just a course description")


def test_unexplained_manager_handoff_catches_no_stated_reason() -> None:
    # thread 2398: needs_manager=true fired on "mau kak" + "masih belajar dari nol kak" (the
    # lead agreeing + answering discovery) with manager_question AND kb_gap both empty
    assert guard.unexplained_manager_handoff(True, None, None)
    assert guard.unexplained_manager_handoff(True, "", "")
    assert not guard.unexplained_manager_handoff(True, "ada trial class gratis?", None)
    assert not guard.unexplained_manager_handoff(True, None, "нет инфы про trial")
    assert not guard.unexplained_manager_handoff(False, None, None)


# ─── integration through the real reply path (SimService) ───────────────────────

class _ScriptLLM:
    """Returns decision JSONs in sequence; embed is a no-op. Simulates the model first
    fabricating, then (or not) fixing on the guard's corrective regeneration."""

    def __init__(self, *replies: str) -> None:
        self._q = list(replies)
        self.chats = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.chats += 1
        r = self._q.pop(0) if self._q else self._q_last
        self._q_last = r
        payload = {"reply": r, "stage": "qualifying", "jobs": [], "pains": [], "gains": []}
        return json.dumps(payload), {"model": "deepseek/deepseek-chat", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="urls"))  # deterministic path
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_guard_regenerates_away_a_fabricated_link(db_session) -> None:
    bid = await _branch(db_session)
    llm = _ScriptLLM(f"Coba akses lab di {_FAKE_LINK} ya Kak",  # 1st draft: fabricated link
                     "Boleh Kak, aku bantu langsung di sini aja ya 😊")  # regen: clean
    out = await SimService(db_session, llm).say(bid, "g1", "boleh kirim akses lab?")
    assert out["ok"] and _FAKE_LINK not in out["reply"]           # fabrication removed
    assert "aku bantu langsung" in out["reply"]                   # the clean regen was used


async def test_guard_regen_bumps_the_leads_routing_signal(db_session) -> None:
    """A regen isn't just fixed in the moment — it's persisted per-lead (Lead.guard_regen_count)
    so future turns lean toward chat:smart for a lead the cheap model has already stumbled on
    (see routing.pick_capability)."""
    from app.adapters.db.models import ChannelThread, Lead

    bid = await _branch(db_session)
    llm = _ScriptLLM(f"Coba akses lab di {_FAKE_LINK} ya Kak", "Boleh, aku bantu di sini ya 😊")
    await SimService(db_session, llm).say(bid, "g_regen", "boleh kirim akses lab?")
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.external_thread_id == "sim:g_regen"))).one()
    lead = await db_session.get(Lead, thread.lead_id)
    assert lead.guard_regen_count == 1


async def test_guard_hands_off_when_fabrication_persists(db_session) -> None:
    bid = await _branch(db_session)
    llm = _ScriptLLM(f"ini linknya {_FAKE_LINK}", f"beneran kok {_FAKE_LINK}")  # both bad
    out = await SimService(db_session, llm).say(bid, "g2", "kirim link lab dong")
    # The fabrication is never sent. The would-be SAFE_FALLBACK hand-off has no phone for this
    # sim lead, so the phone-before-hand-off gate asks for a contact instead of muting (a
    # manager can't work a contact-less lead) — needs_manager is deferred to a later turn.
    assert out["ok"] and _FAKE_LINK not in out["reply"]
    assert out["reply"] == guard.ASK_PHONE_BEFORE_HANDOFF and out["needs_manager"] is False


def _settings_urls_only():  # noqa: ANN201
    from app.modules.settings.service import _parse
    return _parse({"reply_guard": "urls"})


class _FakeCtx:
    def __init__(self, last_inbound: str) -> None:
        self.dialog = [SimpleNamespace(direction="in", text=last_inbound)]
        self.lead = None


class _FakeEngine:
    """Stands in for DecisionEngine in guard_decision — only last_context/complete are
    used, so a real KB/RAG setup isn't needed to test the needs_manager correction alone."""

    def __init__(self, context: str, *regen_replies: str) -> None:
        self.last_context = context
        self._q = list(regen_replies)
        self.sent: list[str] = []  # every extra_user_msg the guard sent into a regen

    async def complete(self, ctx, thread_id, lang, workflow, **kw):  # noqa: ANN001, ANN003
        self.sent.append(kw.get("extra_user_msg") or "")
        raw = self._q.pop(0)
        return raw, {"model": "fake", "cost_usd": 0.0}


async def test_guard_regenerates_a_premature_needs_manager_on_price_question(
    db_session,
) -> None:
    """Thread 2285: the model set needs_manager=true for "ini gratis ga kak?" even though
    the Cybersecurity Skill Booster price was right there in the retrieved KB context —
    guard_decision must catch this and force a regen instead of handing off."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    context = "> Quick facts — Cybersecurity Skill Booster: Harga Rp 700.000 offline..."
    engine = _FakeEngine(
        context,
        json.dumps({"reply": "Ini Rp 700.000 offline / Rp 600.000 online ya Kak 😊",
                   "stage": "qualifying", "needs_manager": False}),
    )
    ctx = _FakeCtx("ini gratis ga kak?")
    decision = parse_decision(json.dumps({
        "reply": "Untuk ini aku cek dulu ke tim ya Kak", "stage": "qualifying",
        "needs_manager": True, "manager_question": "ini gratis ga kak?",
    }))
    fixed, _meta = await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={})
    assert fixed.needs_manager is False
    assert "700.000" in fixed.reply


async def test_guard_keeps_needs_manager_if_regen_still_insists(db_session) -> None:
    """If the model still sets needs_manager=true after being told the fact is in
    context, trust it — a real gap reaching a human beats looping on a refusal."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    context = "> Quick facts — Cybersecurity Skill Booster: Harga Rp 700.000 offline..."
    engine = _FakeEngine(
        context,
        json.dumps({"reply": "Masih perlu dicek ke tim ya Kak", "stage": "qualifying",
                   "needs_manager": True}),
    )
    ctx = _FakeCtx("ini gratis ga kak?")
    decision = parse_decision(json.dumps({
        "reply": "Untuk ini aku cek dulu ke tim ya Kak", "stage": "qualifying",
        "needs_manager": True,
    }))
    fixed, _meta = await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={})
    assert fixed.needs_manager is True


async def test_guard_regenerates_an_unexplained_needs_manager(db_session) -> None:
    """Thread 2398: needs_manager=true fired with manager_question, kb_gap AND stage_reason
    all left null — the model couldn't say what it was escalating. guard_decision must force
    a regen instead of handing off blind."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    engine = _FakeEngine(
        "no price context here",
        json.dumps({"reply": "Oke Kak, sip! Buat Open House-nya aku bantu daftarin ya 🙌",
                   "stage": "qualifying", "needs_manager": False}),
    )
    ctx = _FakeCtx("masih belajar dari nol kak")
    decision = parse_decision(json.dumps({
        "reply": "Untuk yang satu ini aku mau pastikan dulu ke tim ya Kak",
        "stage": "qualifying", "needs_manager": True,
    }))
    fixed, _meta = await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={})
    assert fixed.needs_manager is False
    assert "Open House" in fixed.reply


async def test_guard_keeps_needs_manager_when_regen_names_a_real_gap(db_session) -> None:
    """If the regen still escalates but now NAMES the gap, adopt it — a manager finally has
    something to act on instead of an empty alert."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    engine = _FakeEngine(
        "no price context here",
        json.dumps({
            "reply": "Untuk trial class-nya aku cek dulu ke tim ya Kak",
            "stage": "qualifying", "needs_manager": True,
            "manager_question": "ada trial class gratis?",
            "kb_gap": "trial class tidak ada di KB",
        }),
    )
    ctx = _FakeCtx("ada trial class gratis?")
    decision = parse_decision(json.dumps({
        "reply": "Untuk yang satu ini aku mau pastikan dulu ke tim ya Kak",
        "stage": "qualifying", "needs_manager": True,
    }))
    fixed, _meta = await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={})
    assert fixed.needs_manager is True
    assert fixed.manager_question == "ada trial class gratis?"
    assert fixed.kb_gap == "trial class tidak ada di KB"


async def test_guard_trims_a_still_doubled_question_instead_of_handing_off(db_session) -> None:
    """Live case (threads 2159/2160): the regen ALSO asked two questions — a style slip,
    not a fabrication, so it must be trimmed to one rather than handed off to a manager."""
    bid = await _branch(db_session)
    doubled_twice = (
        "Tentu! Apa yang membuatmu tertarik jadi Data Analyst? Ada tujuan tertentu?",
        "Boleh! Sebelum itu, kamu sudah kerja atau masih kuliah? Mau fokus ke bidang apa?",
    )
    llm = _ScriptLLM(*doubled_twice)
    out = await SimService(db_session, llm).say(bid, "g3", "ceritakan lebih detail dong")
    assert out["ok"]
    assert out["reply"] != guard.SAFE_FALLBACK and out["needs_manager"] is False
    assert not guard.multiple_questions(out["reply"])
    assert out["reply"] == "Boleh! Sebelum itu, kamu sudah kerja atau masih kuliah?"


# ─── price_claims_grounded: skip the LLM verify for a grounded price repeat ───

def test_grounded_price_skips_verify() -> None:
    ctx = "> Quick facts — SMM: Harga Rp 1.882.955 total (DP Rp 500.000), 2 minggu."
    reply = "Biayanya Rp 1.882.955 ya Kak, dengan DP 500.000 untuk amankan seat 😊"
    assert guard.price_claims_grounded(reply, ctx) is True


def test_formatting_variants_still_match() -> None:
    ctx = "DP / seat-lock: 500,000 IDR advance; harga total Rp 1.882.955"
    assert guard.price_claims_grounded("DP-nya 500rb aja Kak", ctx) is True
    assert guard.price_claims_grounded("DP 500 ribu dulu ya Kak", ctx) is True


def test_ungrounded_price_still_verifies() -> None:
    ctx = "Harga Rp 1.882.955 total."
    assert guard.price_claims_grounded("Biayanya cuma Rp 750.000 kok Kak!", ctx) is False


def test_non_price_offer_word_still_verifies() -> None:
    # 'gratis' isn't price vocabulary — a free-offer claim needs the real verify
    ctx = "Harga Rp 700.000."
    assert guard.price_claims_grounded("Ini gratis kok Kak, harga normal Rp 700.000", ctx) is False


def test_story_or_url_still_verifies() -> None:
    ctx = "Harga Rp 700.000. Landing: https://itstep.id/x"
    assert guard.price_claims_grounded(
        "Salah satu alumni kami sukses! Harganya Rp 700.000", ctx) is False
    assert guard.price_claims_grounded(
        "Harga Rp 700.000 — cek https://itstep.id/x", ctx) is False


def test_price_word_without_figure_still_verifies() -> None:
    assert guard.price_claims_grounded("Harganya terjangkau banget Kak!", "Harga Rp 1jt") is False


# ─── _canonical_prices: Indonesian "Rp X juta/ribu" + decimal comma (thread 899) ───

def test_canonical_prices_magnitude_words() -> None:
    assert guard._canonical_prices("Rp2,5 juta per bulan") == {2_500_000}
    assert guard._canonical_prices("Rp 2,5 juta") == {2_500_000}
    assert guard._canonical_prices("mulai 1,67 juta/bln") == {1_670_000}
    assert guard._canonical_prices("DP 500 ribu") == {500_000}
    assert guard._canonical_prices("cuma 750rb") == {750_000}
    assert guard._canonical_prices("harganya 13 juta") == {13_000_000}


def test_canonical_prices_thousands_separators() -> None:
    assert guard._canonical_prices("Rp 1.882.955") == {1_882_955}
    assert guard._canonical_prices("Rp 750.000 offline") == {750_000}


def test_canonical_prices_ignores_bare_numbers_strict() -> None:
    assert guard._canonical_prices("kelas 2 minggu, 16 jam") == set()
    assert 500_000 in guard._canonical_prices("500,000 IDR", liberal=True)


def test_fabricated_juta_price_now_ungrounded() -> None:
    # thread 899: "Rp2,5 juta/bulan" invented; the real cards have 1.670.000, 750.000, etc.
    ctx = "Harga Rp 1.670.000/bln; booster Rp 750.000."
    assert guard.price_claims_grounded("paket mulai Rp2,5 juta per bulan", ctx) is False


# ─── is_risky / price skip: Open-House prohibition topics (thread 2879) ───

def test_is_risky_detects_prohibition_topics() -> None:
    assert guard.is_risky("di Open House bisa kenalan mentor")
    assert guard.is_risky("Kakak bisa coba suasana kelas langsung")
    assert guard.is_risky("mau aku kirim contoh aplikasi yang dibuat peserta kami?")
    assert not guard.is_risky("Kakak bisa datang ke kampus dan tanya ke tim kami")


def test_price_skip_not_bypassing_prohibition() -> None:
    ctx = "Harga Rp 750.000. Open House: NEVER promise mentor sessions."
    assert guard.price_claims_grounded("harga 750rb, dan bisa kenalan mentor", ctx) is False


async def test_pipeline_skips_llm_verify_for_grounded_price(db_session) -> None:
    """End-to-end: a reply that only repeats the KB's own price must NOT spend a verify
    call — the scripted LLM would raise if a second (verify) chat arrived."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    context = "> Quick facts — Cybersecurity Skill Booster: Harga Rp 700.000 offline."
    engine = _FakeEngine(context)  # no regen scripts: any extra complete() would pop-crash

    class _NoVerifyLLM:
        async def chat(self, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("LLM verify must be skipped for a grounded price")

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    decision = parse_decision(json.dumps({
        "reply": "Harganya Rp 700.000 offline ya Kak 😊", "stage": "qualifying"}))
    ctx = _FakeCtx("berapa harga kursusnya?")
    fixed, _meta = await guard_decision(
        db_session, bid, None, _NoVerifyLLM(), engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={})
    assert fixed.reply == "Harganya Rp 700.000 offline ya Kak 😊"  # untouched, no verify


async def test_guard_regen_still_carries_the_turns_situational_nudge(db_session) -> None:
    """Thread 4092 (2026-07-16): a lead whose ONLY message was the ad button got a correct
    no-price opener; guard regenerated it for naming a product absent from the KB, and the
    regen — no longer told the lead had never spoken — answered the button click with the
    full price and DP. A regen re-answers the SAME turn, so the nudge must ride along."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision
    from app.modules.conversation.situations import AD_OPENER_NUDGE

    bid = await _branch(db_session)
    context = "> Quick facts — SMM Intensive Course: Rp 1.882.955, DP Rp 500.000."
    engine = _FakeEngine(
        context,
        json.dumps({"reply": "Halo Kak! Di SMM Intensive Course kamu belajar strategi "
                             "konten yang jualan 😊 Kamu lagi cari buat bisnis atau karier?",
                    "stage": "qualifying"}),
    )
    decision = parse_decision(json.dumps({
        "reply": f"Di kelas SMM kami kamu belajar banyak! Cek {_FAKE_LINK}",
        "stage": "qualifying"}))
    ctx = _FakeCtx("Halo, saya ingin tahu detail program SMM dan biaya kursusnya")

    await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={},
        situational=AD_OPENER_NUDGE)
    assert engine.sent, "the fabrication should have triggered a regen"
    assert any(AD_OPENER_NUDGE in s for s in engine.sent), \
        "the regen dropped the situational nudge — it would re-answer the turn blind"


async def test_guard_regen_without_a_situation_sends_the_bare_correction(db_session) -> None:
    """Most turns have no situation; the correction must not grow a stray trailing newline."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    engine = _FakeEngine("> Quick facts — nothing relevant.",
                         json.dumps({"reply": "Aku cek dulu ya Kak", "stage": "qualifying"}))
    decision = parse_decision(json.dumps({
        "reply": "Cek di https://bukan-punya-kita.example ya", "stage": "qualifying"}))
    await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, _FakeCtx("linknya mana kak?"),
        thread_id=1, lang="id", workflow="reply", bill=False, decision=decision, meta={})
    assert engine.sent and not engine.sent[0].endswith("\n")
