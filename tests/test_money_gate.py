"""The v3 money gate — the only deterministic check that still blocks a send.

v2 had 21 regex checks and not one of them asked whether the reply sells; failing any of them
swapped the answer for a stub (25% reply rate) or a numbered menu. What remains here is only
what costs real money or real trust: a price the KB doesn't contain, a link that doesn't
exist, an invented income claim.
"""
from __future__ import annotations

from app.modules.conversation.money_gate import MONEY_CORRECTION, money_issues

_KB = ("Vibe Coding: durasi 6 bulan · harga Rp 13.360.000, DP Rp 500.000, "
       "cicilan Rp 2.226.000 per bulan. Info: https://itstep.id")


def test_a_grounded_price_passes() -> None:
    assert money_issues("Investasinya Rp 13.360.000 kak, DP-nya Rp 500.000", _KB) == []


def test_an_invented_price_is_blocked() -> None:
    """The single most expensive mistake this bot can make — a price the school must honour."""
    issues = money_issues("Investasinya Rp 26.000.000 kak", _KB)
    assert len(issues) == 1
    assert "26.000.000" in issues[0]


def test_a_price_quoted_with_an_empty_knowledge_base_is_blocked() -> None:
    assert money_issues("Harganya Rp 7.000.000", "") != []


def test_magnitude_wording_is_matched_against_the_same_figure() -> None:
    """'Rp 2,5 juta' and '2.500.000' are the same promise."""
    assert money_issues("DP-nya 500 ribu kak", _KB) == []


def test_a_reply_with_no_money_at_all_is_never_blocked() -> None:
    assert money_issues("Halo kak, kelasnya seru banget lho", "") == []
    assert money_issues("Kelasnya 6 bulan, seminggu 2 kali", "") == []


def test_an_ungrounded_link_is_blocked() -> None:
    issues = money_issues("Cek di https://itstep-jakarta.example.com ya kak", _KB)
    assert any("link" in i for i in issues)


def test_a_grounded_link_passes() -> None:
    assert money_issues("Infonya di https://itstep.id kak", _KB) == []


def test_an_invented_income_claim_is_blocked() -> None:
    """A promise about earnings is a trust liability, not a sales flourish."""
    assert money_issues("Alumni kami rata-rata dapat Rp 8.000.000 per bulan", _KB) != []


def test_instalment_wording_is_not_mistaken_for_an_income_claim() -> None:
    assert money_issues("Cicilannya Rp 2.226.000 per bulan kak", _KB) == []


def test_a_hedged_market_salary_range_is_allowed() -> None:
    """A salary question must be answerable with the facts_market range (thread 5049) — a
    hedged reference ('kisaran … tergantung') is not a promise and must not be blocked."""
    assert money_issues(
        "Kisaran gaji SMM specialist sekitar 5-8 juta per bulan ya Kak, tergantung "
        "perusahaan dan portfolionya", _KB) == []


def test_a_promise_about_our_own_graduates_earnings_is_still_blocked() -> None:
    """Even hedged, a claim about OUR alumni's earnings is a training-outcome liability."""
    assert money_issues(
        "Alumni kami rata-rata dapat Rp 8.000.000 per bulan kok", _KB) != []


def test_every_issue_is_reported_not_just_the_first() -> None:
    issues = money_issues("Rp 99.000.000, cek https://scam.example.com", _KB)
    assert len(issues) >= 2


def test_the_correction_demands_a_rewrite_never_a_retreat() -> None:
    """v2's corrections let the model fall back to 'I'll check with the team', which is how it
    learned to go quiet on answerable questions."""
    text = MONEY_CORRECTION.format(issues="x")
    assert "do not go silent" in text and "do not hand the lead off" in text


# ── invented services / materials (threads 5018, 5063) ───────────────────────

def test_a_free_consultation_offer_is_blocked() -> None:
    """thread 5018: 'free 30-minute business-strategy consultation' — a service that does
    not exist (facts_policy: no career-guidance/advisory service)."""
    assert money_issues(
        "Untuk sesi konsultasi gratis 30 menit tentang strategi pemasaran usaha", _KB) != []


def test_a_business_strategy_consultation_is_blocked() -> None:
    assert money_issues("nanti kita atur konsultasi strategi bisnis ya Kak", _KB) != []


def test_a_fabricated_analysis_document_is_blocked() -> None:
    """thread 5063: a promised bespoke 'cost-analysis / break-even PDF' for a franchise lead."""
    assert money_issues(
        "aku siapin analisa biaya dan estimasi break-even dalam bentuk PDF ya", _KB) != []


def test_a_campus_visit_is_allowed() -> None:
    """The one genuinely free offer — must NOT be caught."""
    assert money_issues(
        "Kakak bisa mampir ke kampus Menara Sudirman buat lihat langsung, gratis kok", _KB) == []


def test_the_paid_demo_event_is_not_an_invented_service() -> None:
    """The Demo Event is a real carded offer — the invented-service detector must ignore it
    (its price is validated separately by the grounding check, so keep price out of here)."""
    from app.modules.conversation.guard import invented_service_offers
    assert invented_service_offers(
        "Ada Demo Event Vibe Coding, Kakak bisa coding langsung sama instruktur") == []


def test_ordinary_discovery_is_not_a_service_offer() -> None:
    """A plain question about the lead's business must never trip the invented-service gate."""
    assert money_issues("Boleh cerita usaha Kakak di bidang apa?", _KB) == []


def test_denying_a_free_service_is_not_offering_one() -> None:
    """"Is there a free trial?" is the question every price-sensitive lead asks, and the honest
    answer names the very service it denies. Live check on branch 8: the gate matched "gratis
    atau konsultasi" inside a refusal and escalated the correct answer to the hold-line."""
    from app.modules.conversation.guard import invented_service_offers
    for denial in (
        "Kita nggak ada trial gratis atau konsultasi gratis ya Kak",
        "Maaf Kak, tidak ada sesi gratis - yang gratis cuma kunjungan ke kampus",
        "Belum ada konsultasi gratis, tapi Kakak bisa mampir ke kampus",
    ):
        assert invented_service_offers(denial) == [], denial


def test_offering_a_free_consultation_is_still_caught() -> None:
    """The negation escape must not open the door: without a denial it is still invented."""
    from app.modules.conversation.guard import invented_service_offers
    assert invented_service_offers("Aku kasih konsultasi gratis dulu ya Kak") != []
    assert invented_service_offers(
        "Kita nggak ada kelas malam. Aku kasih konsultasi gratis ya") != []


# ── the uninvited-price check (follow-up nudges only) ─────────────────────────

def test_a_price_in_a_nudge_is_uninvited() -> None:
    """A follow-up is never an answer to a fresh question — a figure in one is volunteered."""
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    assert uninvited_price("Investasinya Rp 1.882.955 kak.", LeadDossier())


def test_a_priceless_nudge_is_fine() -> None:
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    assert not uninvited_price("Kelas malamnya masih ada slot lho kak", LeadDossier())


def test_a_price_is_fine_once_the_lead_is_ready() -> None:
    """Restating the total while closing an already-ready lead is not a volunteered pitch."""
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    ready = LeadDossier(readiness="ready")
    assert not uninvited_price("Investasinya Rp 1.882.955 kak.", ready)


def test_a_lead_who_never_mentioned_money_gets_no_figure_volunteered() -> None:
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    assert uninvited_price("Cicilannya bisa dari Rp 1.670.000 per bulan lho kak.",
                           LeadDossier(pains=["takut telat"]))


# ── promises to send things (text-only channel) ───────────────────────────────

def test_offering_to_send_a_video_is_blocked() -> None:
    """Live 25 July: "mau saya kirimkan video 5 menit demo dashboard?" — the lead said "Boleh,
    gratis?" and got "maaf, video belum bisa aku kirim lewat chat" the next turn. Asking for a
    yes and refusing it is worse than never offering."""
    assert money_issues("Mau saya kirimkan video 5 menit demo dashboard?", _KB) != []


def test_offering_to_send_a_module_sample_is_blocked() -> None:
    assert money_issues("Kami bisa kirim contoh modul pertama biar Kakak lihat dulu", _KB) != []
    assert money_issues("Mau saya kirim materi silabusnya?", _KB) != []


def test_telling_about_the_material_is_still_allowed() -> None:
    """The block is on SENDING an artefact, not on describing it — the bot must stay able to
    explain the syllabus in words, which is all it ever could do."""
    assert money_issues(
        "Aku jelasin isi modul pertamanya ya: logika dasar dan algoritma", _KB) == []
    assert money_issues("Di kelas nanti Kakak bikin dashboard sendiri", _KB) == []


# ── claims about things the bot cannot see or do ──────────────────────────────

def test_claiming_to_have_looked_at_the_profile_is_blocked() -> None:
    """The bot gets a display name and message text. Nothing else. Thread 5333 shipped
    "makasih udah share linknya - profilnya keren" to a lead whose only message was an
    autoresponder, then a day later read its own claim back out of the transcript and
    elaborated: "aku udah mampir ke link profil Kakak … kumpulan AI prompts & tools gratisnya
    juga menarik". A fabrication in the transcript is one the next turn treats as true."""
    from app.modules.conversation.money_gate import money_issues

    assert money_issues("Tadi aku udah mampir ke link profil Kakak - keren banget", "KB")
    assert money_issues("Makasih udah share linknya - profilnya keren!", "KB")
    assert money_issues("Aku lihat postingan Kakak, keren", "KB")
    assert money_issues("Dari postingan kakak kelihatan suka desain ya", "KB")


def test_ordinary_warmth_is_not_a_profile_claim() -> None:
    """The gate must not fire on praise for the person or their idea — only on praise for
    something the bot would have had to look at."""
    from app.modules.conversation.money_gate import money_issues

    assert not money_issues("Ide Kakak keren banget!", "KB")
    assert not money_issues("Keren nih rencananya, Kak", "KB")
    assert not money_issues("Nanti Kakak bikin portofolio yang bagus", "KB")


def test_offering_a_call_never_reaches_the_lead() -> None:
    """impossible_capability_offers has existed since v2 and was wired into the comments path
    and the learning audit — but never into the reply gate, so live DMs were never checked."""
    from app.modules.conversation.money_gate import money_issues

    assert money_issues("Boleh aku jelasin lewat telepon, Kak?", "KB")
    assert money_issues("Aku kirim voice note ya biar jelas", "KB")


def test_a_price_we_sent_does_not_license_the_next_one() -> None:
    """prices_quoted records the figures WE sent — decision._prices_in reads them off the bot's
    own reply — so treating it as "they raised money" made one quote exempt every later nudge
    for good. Thread 5393: a spec sheet at 12:17 filled prices_quoted, and an hour later an
    unprompted Demo Event pitch with its own price sailed through to a lead who had not said
    a word. The exemption belongs to what THEY said, not to what we already did."""
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    nudge = "Ada Demo Event tanggal 8 Agustus, tiketnya Rp 100.000 aja"
    already_pitched = LeadDossier(prices_quoted=["Rp 13.000.000", "Rp 500.000"])
    assert uninvited_price(nudge, already_pitched), "our own quote is not their invitation"


def test_a_lead_who_brought_money_up_still_gets_the_number() -> None:
    """The case the exemption exists for: someone who asked what it costs, got the answer and
    went quiet is exactly who a payment plan is useful to. Both signals are lead-sourced —
    discovery records budget_signal from their words, including a bare "berapa?"."""
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    nudge = "Cicilannya bisa dari Rp 1.670.000 per bulan lho kak."
    assert not uninvited_price(nudge, LeadDossier(budget_signal="tanya harga"))
    assert not uninvited_price(nudge, LeadDossier(payment_preference="cicilan"))
    assert not uninvited_price(nudge, LeadDossier(readiness="ready"))


# ── invented result percentages (thread 4799) ────────────────────────────────

def test_a_result_stated_as_a_percentage_is_blocked() -> None:
    """The knowledge base bans these outright, and the model produced one anyway — twice in
    one thread. First as an outside brand: "contoh nyata Design Pickle, brand asal Amerika,
    berhasil dapat 50% pelanggan baru". Eight hours later, re-attributed to "alumni SMM
    Intensive" — an invented outside case turned into an invented graduate of ours.

    The figure came from the prohibition itself: "50%" appears exactly once in the whole
    knowledge base, inside the sentence forbidding it. Nothing downstream caught it — no
    price, no link, no rupiah figure, so every money check passed."""
    assert money_issues(
        "Ada contoh nyata Design Pickle, brand asal Amerika, berhasil dapat 50% pelanggan "
        "baru cuma lewat retargeting Meta Ads", _KB) != []
    assert money_issues("alumni kami dapat 50% pelanggan baru", _KB) != []
    assert money_issues("omzetnya naik 30% dalam sebulan", _KB) != []
    assert money_issues("engagement naik 200% lho Kak", _KB) != []


def test_our_real_discounts_are_percentages_too_and_must_pass() -> None:
    """Ours are carded and stated in percent — the check must not eat them."""
    assert money_issues("Ada diskon referral 10% buat Kakak dan temannya", _KB) == []
    assert money_issues("Buat pelajar ada potongan 10% ya Kak", _KB) == []
    # A discount in one sentence does not excuse a result claim in the next.
    assert money_issues(
        "Ada diskon 10% buat pelajar. Alumni kami dapat 50% pelanggan baru.", _KB) != []


def test_a_price_the_ad_itself_promised_may_be_given_once_in_a_nudge() -> None:
    """Two measurements pulled apart here and this is where they meet. Meta's prefill asks
    about cost, and leading the FIRST message with a figure halves the reply rate (16.1% vs
    36.3%, 819 threads) — so the opener carries none. But silencing the nudge too means the
    lead never gets a number at all: only 24% of 453 price-ad threads ever saw one. Thread
    5293 lived it — an ad that asks about cost, four of our messages, no figure in two days.

    So the ad buys a price once: not in the opener, and not again after we have answered."""
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.money_gate import uninvited_price

    nudge = "Oh iya Kak, biayanya Rp 1.882.955, bisa DP Rp 500.000 dulu."
    fresh = LeadDossier()  # nothing quoted yet — the ad's question is still hanging
    assert not uninvited_price(nudge, fresh, ad_promised_price=True)
    # …but only once. Once we have answered, the ad has been honoured.
    answered = LeadDossier(prices_quoted=["Rp 1.882.955"])
    assert uninvited_price(nudge, answered, ad_promised_price=True)
    # And a lead who never came from a price ad is unaffected.
    assert uninvited_price(nudge, fresh, ad_promised_price=False)


# ── a start given as a window, not a date ────────────────────────────────────

def test_a_start_promised_as_a_month_is_blocked() -> None:
    """stale_dates catches an explicit day that has passed. This catches the vaguer promise
    that never had a day in it, and it has cost two threads on two products with the same
    six words: 5366 (SMM Intensive, "kelas mulai akhir Juli ini", 26 July) and 5431 (Vibe
    Coding, same wording, 27 July). Both times the card carried the window and the model
    repeated it faithfully. A start date must be a DATE."""
    assert money_issues("Kelas berikutnya start akhir Juli ini lho", _KB) != []
    assert money_issues("Batch berikutnya mulai awal Agustus ya Kak", _KB) != []
    assert money_issues("Grup baru bulan depan Kak", _KB) != []
    assert money_issues("Program ini mulai pertengahan September", _KB) != []


def test_ordinary_payment_and_schedule_talk_is_not_a_start_promise() -> None:
    """The window words appear in perfectly good sentences. A payment term is not a start
    date, and neither is a class that simply has not been given one."""
    assert money_issues("Sisanya dibayar sebelum kelas mulai ya Kak", _KB) == []
    assert money_issues("Cicilannya tiap akhir bulan Kak", _KB) == []
    assert money_issues(
        "Tanggal mulainya masih disusun tim, nanti dikonfirmasi ya", _KB) == []
    assert money_issues("Kelasnya 2x seminggu di malam hari", _KB) == []


def test_a_real_date_still_passes() -> None:
    """The fix is not 'never mention a start' — a day from the knowledge base is the point."""
    assert money_issues("Skill Booster Python hari Minggu, 2 Agustus 2026, jam 09:00-14:00",
                        _KB) == []
