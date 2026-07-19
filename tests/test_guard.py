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


def test_career_service_claims_flags_offers_spares_honest_denials() -> None:
    # thread 2740: 'career guidance dari mentor praktisi' — no such service exists
    assert guard.career_service_claims("Ditambah ada career guidance dari mentor praktisi")
    assert guard.career_service_claims("kami menyediakan job placement untuk lulusan")
    # the honest denial is the CORRECT answer and must pass
    assert not guard.career_service_claims(
        "kami belum punya program penempatan kerja khusus")
    assert not guard.career_service_claims(
        "IT STEP tidak ada kerja sama penyaluran lowongan khusus")


def test_ungrounded_biz_counts_flags_invented_company_networks() -> None:
    ctx = "Sejak 1999, 24 negara, 267.000+ alumni. Partner resmi: 3 perusahaan lokal."
    # thread 2740: 'jaringan alumni di lebih dari 1.500 perusahaan' — nowhere in the KB
    assert guard.ungrounded_biz_counts(
        "akses ke jaringan alumni di lebih dari 1.500 perusahaan", ctx)
    # a count the KB does state passes (separator-insensitive)
    assert not guard.ungrounded_biz_counts("kami punya 3 perusahaan partner lokal", ctx)
    # plain numbers not glued to a company noun are out of scope
    assert not guard.ungrounded_biz_counts("kelasnya maksimal 14 orang, 2x seminggu", ctx)


def test_price_order_small_step_must_lead() -> None:
    # live 2026-07-19: totals still led 2/3 of price quotes despite the prompt rule
    assert guard.price_order_wrong(
        "Investasinya Rp 13.000.000 - bisa dicicil 4 x Rp3.250.000 tanpa bunga")
    assert guard.price_order_wrong(
        "Investasinya Rp 1.882.955 total, DP Rp 500.000 untuk amankan seat")
    # DP/cicilan first → correct order, passes
    assert not guard.price_order_wrong(
        "DP Rp 500.000 aja buat amankan seat, total Rp 1.882.955")
    assert not guard.price_order_wrong(
        "cicilan Rp 1.670.000 per bulan, atau lunas Rp 15.030.000")
    # a lone cheap price with no small-step figure is not an ordering problem
    assert not guard.price_order_wrong("Skill Booster cuma Rp 500.000, 1 hari aja")
    # a millions figure that IS the monthly instalment must not read as a total
    assert not guard.price_order_wrong(
        "Untuk kursusnya memang berbayar ya Kak, Rp 1.670.000 per bulan selama 8 bulan.")


def test_ungrounded_times_flags_invented_class_hours() -> None:
    ctx = "Open House gratis tiap Kamis jam 16:00-20:00 WIB di Menara Sudirman"
    # sim s10 night_worker: '19.00-20.00' quoted to a shift worker, KB says only 'malam'
    assert guard.ungrounded_times("Kelas kita malam hari, sekitar jam 19.00 - 20.30 WIB", ctx)
    # a time the KB does state passes, separator-normalized (16.00 vs 16:00)
    assert not guard.ungrounded_times("Open House Kamis jam 16.00-20.00 ya Kak", ctx)
    # prices with dotted thousands must not read as clock times
    assert not guard.ungrounded_times("Investasinya Rp 1.882.955, DP Rp 500.000", ctx)


def test_fabricated_income_figure_flags_earnings_not_installments() -> None:
    # bench b10g 4045: an invented alumni monthly income reached the final reply
    assert guard.fabricated_income_figure(
        "banyak alumni kami dapat proyek freelance sampai 5-6 juta per bulan")
    assert guard.fabricated_income_figure("lulusan kami bisa raup 8jt/bulan")
    # a real KB installment carries a payment word — must NOT be flagged
    assert not guard.fabricated_income_figure(
        "Investasinya Rp13.000.000, bisa dicicil 4 juta per bulan tanpa bunga")
    # a general true archetype with no number stays fine
    assert not guard.fabricated_income_figure(
        "banyak alumni kami sekarang jadi SMM specialist dan freelancer")


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


def test_price_before_lead_spoke_flags_only_the_silent_clicker() -> None:
    assert guard.price_before_lead_spoke("Investasinya Rp 13.000.000", lead_spoke=False)
    assert guard.price_before_lead_spoke("cuma 750 ribu aja Kak", lead_spoke=False)
    # the same number is perfectly fine once the lead has actually asked something
    assert not guard.price_before_lead_spoke("Investasinya Rp 13.000.000", lead_spoke=True)
    # a priceless opener to a clicker is exactly what we want — never flag it
    assert not guard.price_before_lead_spoke(
        "Hai Kak! Aku MinStep dari IT STEP Academy 😊 Kakak tertarik karena apa?",
        lead_spoke=False)
    # "gratis" is not a price — an Open House invite must stay allowed
    assert not guard.price_before_lead_spoke(
        "Kita ada Open House gratis tiap Kamis, tertarik?", lead_spoke=False)


async def test_guard_regenerates_a_price_quoted_to_a_lead_who_never_spoke(db_session) -> None:
    """Threads 4064/4065 (2026-07-16): the lead only ever tapped the ad button. The opener
    obeyed the no-price rule; the follow-up an hour later led with Rp 13.000.000 anyway. The
    nudge was delivered and the cheap model ignored it — so the rule can't live in the prompt
    alone. Deterministic gate: no price until the lead says something of their own."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    context = "> Quick facts — Vibe Coding: Rp 13.000.000, cicilan 4x."
    engine = _FakeEngine(
        context,
        json.dumps({"reply": "Hai Kak! Dalam ~4 bulan Kakak bisa bikin aplikasi sendiri "
                             "pakai AI 😊 Kakak tertarik buat karier atau proyek pribadi?",
                    "stage": "qualifying"}),
    )
    ctx = _FakeCtx("💻 Ceritakan lebih detail tentang program kursusnya")  # the ad button
    decision = parse_decision(json.dumps({
        "reply": "Program Vibe Coding berbayar Rp13.000.000 (atau 4×3.250.000 tanpa bunga).",
        "stage": "qualifying"}))

    fixed, _meta = await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="followup", bill=False, decision=decision, meta={})
    assert "13.000.000" not in fixed.reply, "a silent clicker must not be quoted a price"
    assert "aplikasi" in fixed.reply  # the regen's priceless draft is what ships


async def test_guard_leaves_a_price_alone_once_the_lead_has_spoken(db_session) -> None:
    """The gate must not fire on the normal case — a lead who asked deserves the number."""
    from app.modules.conversation.decision import parse_decision
    from app.modules.conversation.reply import guard_decision

    bid = await _branch(db_session)
    engine = _FakeEngine("> Quick facts — Vibe Coding: Rp 13.000.000.")  # no regen scripted
    ctx = _FakeCtx("berapa harganya kak?")
    decision = parse_decision(json.dumps({
        "reply": "Vibe Coding Rp 13.000.000 ya Kak 😊", "stage": "qualifying"}))
    fixed, _meta = await guard_decision(
        db_session, bid, _settings_urls_only(), None, engine, ctx, thread_id=1, lang="id",
        workflow="reply", bill=False, decision=decision, meta={})
    assert fixed.reply == "Vibe Coding Rp 13.000.000 ya Kak 😊"


def test_stale_dates_flags_a_batch_that_already_started() -> None:
    from datetime import date

    today = date(2026, 7, 16)
    # thread 3912, verbatim: the Social Media Content Bootcamp card still said "batch 11 Juli"
    assert guard.stale_dates("Ada bootcamp 1 hari, batch 11 Juli, Rp 750.000", today)
    assert guard.stale_dates("Kelasnya mulai 1 Juni ya Kak", today)


def test_stale_dates_allows_upcoming_and_next_year_intakes() -> None:
    from datetime import date

    today = date(2026, 7, 16)
    assert not guard.stale_dates("Batch berikutnya 25 Juli ya Kak", today)  # still ahead
    assert not guard.stale_dates("Kelas mulai 3 Agustus", today)
    # a bare "5 Januari" in July means NEXT January — the card just carries no year
    assert not guard.stale_dates("Batch berikutnya 5 Januari", today)
    assert not guard.stale_dates("Kelas 2 minggu, 3 sesi per minggu", today)  # no date at all
    assert not guard.stale_dates("31 Februari", today)  # not a real date


def test_stale_dates_does_not_flag_today(db_session=None) -> None:
    from datetime import date

    today = date(2026, 7, 16)
    assert not guard.stale_dates("Batchnya 16 Juli, masih bisa daftar!", today)


def test_whatsapp_delivery_offers_catches_number_then_send_document() -> None:
    # thread S5: "boleh aku minta nomor WA Kakak biar aku bisa kirim brosur lengkapnya?"
    assert guard.whatsapp_delivery_offers(
        "boleh aku minta nomor WA Kakak biar aku bisa kirim brosur lengkap Skill Booster-nya?")
    assert guard.whatsapp_delivery_offers("minta nomor whatsapp ya kak, nanti aku kirimkan silabus")
    # a plain contact-ask for a real hand-off (no document promise) stays allowed
    assert not guard.whatsapp_delivery_offers("boleh aku minta nomor WhatsApp Kakak dulu ya?")
    assert not guard.whatsapp_delivery_offers("nanti tim kami hubungi via WhatsApp ya Kak")


def test_answer_dont_escalate_correction_forbids_escalation_and_phone_ask() -> None:
    """Thread 2733/S2: the model escalated on 'berapa biayanya?' and, with no phone on file,
    the phone gate turned that into a repeated 'give me your WhatsApp' stub. The reply flow
    force-answers first; the correction it sends must forbid both escalation and the phone-ask
    (the end-to-end behaviour is exercised by the regression sim)."""
    c = guard.ANSWER_DONT_ESCALATE_CORRECTION.lower()
    assert "do not set needs_manager" in c  # the actual instruction, not just the word
    assert "do not ask for a phone number" in c
    assert "knowledge base" in c  # answer must come from the KB, not a stall


def test_normalize_address_forces_kakak_over_kamu() -> None:
    # threads 4091/4060/2733: the model drifts to the familiar "kamu" mid-chat
    assert guard.normalize_address("SMM itu bikin kamu bisa kelola brand") == \
        "SMM itu bikin Kakak bisa kelola brand"
    assert guard.normalize_address("proyek yang akan kamu kerjakan, Kamu akan punya portfolio") == \
        "proyek yang akan Kakak kerjakan, Kakak akan punya portfolio"
    assert guard.normalize_address("membantu praktik kamu 😊") == "membantu praktik Kakak 😊"
    # already-correct text is untouched; 'kamu' as a substring of another word is not caught
    assert guard.normalize_address("Kakak bisa daftar sekarang") == "Kakak bisa daftar sekarang"
    assert guard.normalize_address("kamuflase warna") == "kamuflase warna"


def test_booster_wrong_duration_flags_invented_multiweek_booster() -> None:
    # thread 2864, verbatim — a Python Skill Booster invented as a 2-week course
    assert guard.booster_wrong_duration(
        "kami punya Python Skill Booster 2 minggu yang fokus bikin script prediksi kripto")
    assert guard.booster_wrong_duration(
        "aku cek dulu detail Python Skill Booster 2 minggu yang fokus bikin script")
    assert guard.booster_wrong_duration("Data Analyst Skill Booster 3 bulan")
    # the real booster (1 day) and a legit comparison must NOT be flagged
    assert not guard.booster_wrong_duration("Python Skill Booster 1 hari (5 jam), Rp 500.000")
    assert not guard.booster_wrong_duration(
        "Ada Skill Booster 1 hari, atau SMM Intensive yang 2 minggu")
    assert not guard.booster_wrong_duration("SMM Intensive 2 minggu, 3x per minggu")


def test_promised_handoff_detects_a_stated_team_takeover() -> None:
    # thread 1230: the bot said this, never set needs_manager, and kept nudging the lead
    assert guard.promised_handoff(
        "Siap Kak! Data sudah aku teruskan ke tim, mereka akan hubungi Kakak via WhatsApp "
        "dalam 1x24 jam")
    assert guard.promised_handoff("Tim kami akan hubungi Kakak di jam kerja ya")
    assert guard.promised_handoff("Nanti akan dihubungi langsung oleh tim kami")
    # "let me check with the team" is NOT a hand-off promise — SAFE_FALLBACK owns that path
    assert not guard.promised_handoff(
        "Untuk yang satu ini aku mau pastikan dulu ke tim biar infonya akurat ya Kak")
    assert not guard.promised_handoff("Kelas kami dipandu mentor praktisi dari tim kami")


def test_booster_wrong_duration_catches_wide_and_spares_comparison() -> None:
    # bench 4069: "Data Analyst Skill Booster dirancang ... dalam 1 minggu" (booster is 1 day)
    assert guard.booster_wrong_duration(
        "Data Analyst Skill Booster dirancang khusus untuk menguasai Excel dalam 1 minggu")
    assert guard.booster_wrong_duration("Skill Booster ini berlangsung selama 2 bulan")
    # a legitimate comparison — booster's real 1-hari next to SMM's real 2-minggu — is spared
    assert not guard.booster_wrong_duration(
        "Ada Skill Booster 1 hari, atau SMM Intensive yang 2 minggu")
    assert not guard.booster_wrong_duration("Skill Booster 1 hari (5 jam), Rp 500.000")


def test_vibe_wrong_duration_flags_weeks_spares_months() -> None:
    # thread 4220: Vibe Coding (a ~4-month program) shrank to '4 minggu'
    assert guard.vibe_wrong_duration("Vibe Coding cuma 4 minggu aja")
    assert not guard.vibe_wrong_duration("Vibe Coding ~4 bulan, 2x seminggu")


def test_annoyance_catches_disgust_words() -> None:
    # thread 2833: 'Najis' got six more pitches
    assert guard.lead_signaled_annoyance("Najis")
    assert not guard.lead_signaled_annoyance("oke kak makasih")


def test_open_house_as_event_flags_event_framing_spares_visit_time() -> None:
    # threads 4563/4550: OH pitched as a fixed weekly event to show up to
    assert guard.open_house_as_event(
        "kami ada Open House gratis tiap Kamis. Kakak mau datang minggu ini?")
    assert guard.open_house_as_event(
        "ada Open House Kamis ini di Menara Sudirman, bisa datang?")
    assert guard.open_house_as_event("yuk ikut acara Open House kami")
    # the reframed offer — visit time, no weekday/event label — must pass
    assert not guard.open_house_as_event(
        "kami sediakan waktu khusus buat kunjungan ke Menara Sudirman, kapan Kakak sempat?")
    assert not guard.open_house_as_event("oh iya Kak, soal jadwalnya nanti aku info")


def test_game_offering_claims_flags_invented_course_spares_honest_pivot() -> None:
    # thread 4573: a game course / 'game projects we built' that don't exist
    assert guard.game_offering_claims("kami ada kelas game development lho Kak")
    assert guard.game_offering_claims("mau lihat contoh project game yang pernah kami buat?")
    assert guard.game_offering_claims("di sini kita ajarin bikin game juga")
    # honest negation and a general programming truth both pass
    assert not guard.game_offering_claims("jujur ya Kak, kami belum ada kelas khusus game")
    assert not guard.game_offering_claims(
        "Python Back-End juga bisa dipakai buat bikin game sederhana")
