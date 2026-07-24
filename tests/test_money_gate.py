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
