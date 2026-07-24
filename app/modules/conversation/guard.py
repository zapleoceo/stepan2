"""Reply guard — the verification layer that stops the bot from stating things the KB
doesn't support, AND from a handful of live conversation-quality failures that don't need
KB context to detect (multiple questions in one turn, offering a capability Stepan doesn't
have, contradicting its own channel).

Two tiers, cheapest first:
  1. deterministic (always on): any URL not present verbatim in the KB context is a
     fabrication (this alone would have blocked the fake `lab.itstep.id/...?access=...` in
     chat 1736); a claim of an already-sent file/screenshot/WA delivery is always false;
     more than one '?' in a turn means the lead got two questions and answered one (thread
     1729/1793); offering a voice note/call (thread 1330) or telling an Instagram lead to
     "go DM on Instagram" (thread 2092) are structurally impossible regardless of KB.
  2. selective LLM verify: only when the reply looks risky (a link, an offer, a resource
     hand-out, a price figure, or a specific alumni/success story), a cheap model lists
     claims unsupported by the KB context — including a story that isn't one of the
     product's curated Success Cases (chat 1827: "salah satu alumni kami..." with nothing
     to back it up if the lead asks to see it).

On an unfixable violation the caller regenerates once, then falls back to a safe
"let me confirm with the team" hand-off — never sends the fabrication.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime

from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
# Bare official site / no-path is allowed even if not quoted in context; anything with a
# path or query (a specific resource) must be grounded in the KB text.
_BARE_OK = re.compile(r"^https?://(www\.)?itstep\.id/?$", re.IGNORECASE)

# Reply shapes worth an LLM grounding check — offers, resources, hand-outs, access.
_RISKY_RE = re.compile(
    r"\b(gratis|free|akses|access|link|lab|trial|reserve|reservein|slot|voucher|"
    r"kupon|promo|diskon|discount|beasiswa|scholarship|garansi|jaminan|refund|"
    r"sertifikat cisco|cyberops|template|tutorial|download|kirim(?:kan)? (?:link|file|akses)|"
    r"harga|biaya|tarif|cicilan|angsuran)\b",
    re.IGNORECASE)
# A concrete money figure (e.g. "Rp 297.000", "1.670.000/bulan", "500 ribu") — the exact
# shape of the chat-452 fabrication, which carried no "diskon/promo/gratis" trigger word.
_PRICE_RE = re.compile(
    r"\brp\.?\s?\d[\d.,]*|\d[\d.,]*\s?(?:ribu|juta|rb\b)", re.IGNORECASE)

# A claim that a file/screenshot/dataset has ALREADY been sent, or delivered specifically
# via WhatsApp — deterministically false regardless of KB content: Stepan is text-only (no
# image/file attach capability) and Instagram-only (no WhatsApp channel). A 50-thread live
# audit (2026-07-05) found leads left believing a screenshot/dataset had arrived when
# nothing was ever sent (threads 1408, 1721).
_DELIVERY_NOUN = r"(?:screenshot|foto|gambar|file|dokumen|dataset|dm|wa|whatsapp)"
_FALSE_DELIVERY_RE = re.compile(
    rf"\b{_DELIVERY_NOUN}\w*\b[^.!?\n]{{0,15}}\b(?:udah|sudah)\b[^.!?\n]{{0,20}}\bkirim(?:kan)?\b"
    rf"|\b(?:udah|sudah)\b[^.!?\n]{{0,40}}\bkirim(?:kan)?\b[^.!?\n]{{0,40}}\b{_DELIVERY_NOUN}\b",
    re.IGNORECASE)

# Alumni/success-story narrative — a specific-sounding "one of our alumni did X" claim.
# Policy (2026-07-06): illustrative stories are fine, but ONLY when they're the exact cases
# already curated in a product's "Success cases" section (real named public figures + links,
# or the Director's own real projects) — never improvised on the fly with no case behind it.
# Chat 1827 is the live example: "salah satu alumni kami yang berhasil..." with zero name,
# link, or specific detail — if the lead asks to see it, there is nothing to show. This
# doesn't block generalized TRUE archetype language ("banyak peserta kami mulai dari nol,
# ada yang jadi developer...") — the LLM verify step judges that distinction using the
# actual Success Cases / Stories content in context.
_STORY_RE = re.compile(
    r"\b(alumni kami|lulusan kami|peserta kami|salah satu (peserta|siswa|alumni|mentor|"
    r"murid)|kita (?:ada|punya) case\b|case alumni|ada (?:peserta|siswa|alumni|murid) yang "
    r"(?:berhasil|sukses))\b", re.IGNORECASE)


def false_delivery_claims(reply: str) -> list[str]:
    """Claims of an already-sent file/screenshot/WA delivery — always fabricated (Stepan
    cannot attach files and has no WhatsApp channel), so this needs no KB context at all."""
    return [m.group(0) for m in _FALSE_DELIVERY_RE.finditer(reply or "")]


_IMPOSSIBLE_CAPABILITY_RE = re.compile(
    r"\b(voice note|rekaman suara|video call|telpon (?:langsung|kamu|kakak)|"
    r"nelpon (?:langsung|kamu|kakak)|jelasin(?:in)? (?:lewat|via) (?:telepon|telpon|call))\b",
    re.IGNORECASE)


def impossible_capability_offers(reply: str) -> list[str]:
    """Offers of a capability Stepan structurally doesn't have (voice/video/calls) — always
    false regardless of KB content, same tier as false_delivery_claims."""
    return [m.group(0) for m in _IMPOSSIBLE_CAPABILITY_RE.finditer(reply or "")]


def quotes_price(reply: str) -> bool:
    """A concrete money figure appears in the reply — same shape the money gate already
    verifies against the KB. Used by the pitch gate as a content-based backstop: the model
    can mislabel its own `move` (thread 4972 shipped a full price quote tagged
    `answer_question`, which isn't in `_PITCH_MOVES`), but it can't hide the figure itself."""
    return bool(_PRICE_RE.search(reply or ""))


# Thread 1721: the bot promised "aku kirim file dataset ... via WhatsApp ya", asked for the
# lead's number, then repeatedly claimed to have already sent it (false_delivery_claims
# above blocks THAT half) — but the ORIGINAL future-tense promise to reach the lead over
# WhatsApp was never blocked, and it's just as impossible: Stepan has exactly one channel
# (Instagram DM) and no WhatsApp send capability at all. Block the promise at its source
# instead of only the lie that follows it.
_WHATSAPP_DELIVERY_RE = re.compile(
    r"\bkirim(?:in|kan)?\b[^.!?\n]{0,80}\b(?:via|lewat|ke)\s+(?:wa|whatsapp)\b"
    r"|\b(?:via|lewat|ke)\s+(?:wa|whatsapp)\b[^.!?\n]{0,80}\bkirim(?:in|kan)?\b"
    # "your WA number so I can send you the brochure/file" — the bot promising to deliver a
    # DOCUMENT to WhatsApp (thread S5). Kept narrow: it needs both a WA-number ask AND a
    # send-a-document verb, so a plain "boleh minta nomor WA?" for a real hand-off is untouched.
    r"|\bnomor\s+(?:wa|whatsapp)\b[^.!?\n]{0,80}\bkirim(?:in|kan)?\b[^.!?\n]{0,40}"
    r"\b(?:brosur|file|dokumen|silabus|pdf|materi|modul)\b",
    re.IGNORECASE)


def whatsapp_delivery_offers(reply: str) -> list[str]:
    """A promise to send anything over WhatsApp — always false, Stepan has no WhatsApp
    channel and can only reply inside this Instagram DM thread."""
    return [m.group(0) for m in _WHATSAPP_DELIVERY_RE.finditer(reply or "")]


# Services/materials the bot INVENTS out of thin air — the model's most expensive
# hallucination class after prices, and one no other gate caught (money_gate = figures,
# false_delivery = files-already-sent). Thread 5018: a "free 30-minute business-strategy
# consultation" that doesn't exist; thread 5063: a fabricated "break-even estimate / royalty
# analysis / cost-analysis PDF" for a franchise lead. Policy: the ONLY free thing the bot may
# offer is a campus visit; the Demo Event is a paid offer with its own product card. A bespoke
# consultation/session/coaching or a promise to prepare a custom analysis document is never
# real — facts_policy states outright there is no career-guidance/advisory service.
_INVENTED_SERVICE_RE = re.compile(
    # free consultation / session / coaching as a standalone service
    r"\b(?:konsultasi|sesi|bimbingan|coaching|mentoring)\b[^.!?\n]{0,30}"
    r"\b(?:gratis|free|cuma-cuma|tanpa biaya)\b"
    r"|\b(?:gratis|free)\b[^.!?\n]{0,20}\b(?:konsultasi|sesi\s+konsultasi|bimbingan)\b"
    # a business / marketing / strategy consultation — an invented advisory service
    r"|\bkonsultasi\b[^.!?\n]{0,30}\b(?:strategi|pemasaran|marketing|bisnis|usaha)\b"
    # a promise to prepare/send a bespoke analysis / proposal / cost or break-even document
    r"|\b(?:siapin|siapkan|kirim(?:in|kan)?|buatin|buatkan|susun(?:kan)?)\b[^.!?\n]{0,45}"
    r"\b(?:analisa|analisis|proposal|estimasi|perhitungan)\b[^.!?\n]{0,35}"
    r"\b(?:biaya|break.?even|balik\s*modal|royalti|royalty|pdf|dokumen)\b",
    re.IGNORECASE)


def invented_service_offers(reply: str) -> list[str]:
    """A promised service/session/document that is not part of the offering (threads 5018,
    5063). Only a campus visit is free; the Demo Event is a paid, carded offer. Everything
    else here — free consultations, business/marketing strategy sessions, bespoke
    cost/break-even analyses — is invented and must not reach the lead."""
    return [m.group(0) for m in _INVENTED_SERVICE_RE.finditer(reply or "")]


_ID_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7,
    "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
_ID_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(januari|februari|maret|april|mei|juni|juli|agustus|september|"
    r"oktober|november|desember)\b",
    re.IGNORECASE)
# A KB card's date outlives the date. Cards carry no year, so a bare "11 Juli" is read as
# this year — except when that reading puts it far in the past, which almost always means
# next year's intake (a December reply naming "5 Januari").
_NEXT_INTAKE_HORIZON_DAYS = 180


def stale_dates(reply: str, today: date | None = None) -> list[str]:
    """A date being offered that has already passed.

    Thread 3912 (2026-07-16): Stepan pitched the Social Media Content Bootcamp's "batch 11
    Juli" on the 15th and 16th — the batch was gone. The price he quoted was right and the
    card backed him up; the card itself had simply expired, and nothing anywhere noticed.
    Facts in the KB are trusted absolutely, so the one class of fact that rots on its own
    needs a clock, not a proofreader."""
    now = today or datetime.now(UTC).date()
    out = []
    for m in _ID_DATE_RE.finditer(reply or ""):
        try:
            when = date(now.year, _ID_MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            continue  # 31 Februari and friends — not a date, not our problem
        gone = (now - when).days
        if 0 < gone <= _NEXT_INTAKE_HORIZON_DAYS:
            out.append(f"date already past: {m.group(0)} (was {gone}d ago)")
    return out


# Every Skill Booster is a 1-day (5-hour) taster. Thread 2864: the model invented a "Python
# Skill Booster 2 minggu" — conflating the booster line with SMM Intensive's 2-week length,
# and inventing a crypto-script focus — offered as a cheaper alternative. It carried no price
# or link, so is_risky never routed it to the grounding verify and it shipped twice. A booster
# named with ANY week/month duration is always a fabrication (only SMM Intensive is 2 weeks;
# everything else is 1 day or 4-9 months), so catch it deterministically. Tight window so a
# legitimate "Skill Booster 1 hari, atau SMM Intensive 2 minggu" comparison isn't flagged.
# A booster given a week/month length anywhere in the same clause — but NOT when 'hari'/'jam'
# (its real 1-day duration) or another product name sits between, which is a legitimate
# comparison ("Skill Booster 1 hari, atau SMM Intensive 2 minggu"). Bench 4069: "Data Analyst
# Skill Booster dirancang … dalam 1 minggu" slipped the old tight 15-char window.
_BOOSTER_DURATION_RE = re.compile(
    r"\bbooster\b(?:(?!\bhari\b|\bjam\b|\bintensive\b|\bsmm\b|\bvibe\b|\bpython\b)[^.!?\n]){0,90}?"
    r"\b\d+\s*(?:minggu|bulan)\b",
    re.IGNORECASE)


def booster_wrong_duration(reply: str) -> list[str]:
    m = _BOOSTER_DURATION_RE.search(reply or "")
    return [f"Skill Booster given a week/month length — boosters are 1 day: {m.group(0)}"] \
        if m else []


_EARN_WORD_RE = re.compile(
    r"\b(dapat|dapet|penghasilan|gaji|gajih|hasilkan|menghasilkan|income|earning|raup|cuan|"
    r"freelance|proyek|fee|omzet|profit|untung)\w*", re.IGNORECASE)
_PAY_WORD_RE = re.compile(
    r"\b(cicil|angsur|bayar|investasi|dp\b|biaya|harga|total|uang muka|pembayaran)\w*",
    re.IGNORECASE)
_PER_MONTH_MONEY_RE = re.compile(
    r"\d[\d.,]*\s*(?:juta|jt|ribu|rb|k)\b[^.!?\n]{0,18}?(?:per\s*bulan|/?\s*bulan|sebulan"
    r"|perbulan|tiap bulan|sebulannya)", re.IGNORECASE)


def fabricated_income_figure(reply: str) -> list[str]:
    out = []
    for bubble in (reply or "").split("|||"):
        if _PAY_WORD_RE.search(bubble):
            continue
        m = _PER_MONTH_MONEY_RE.search(bubble)
        if m and _EARN_WORD_RE.search(bubble):
            out.append(f"invented monthly-income figure (no KB source): {m.group(0)}")
    return out


_MILLIONS_RE = re.compile(r"rp\s*\.?\s*\d{1,3}[.,]\d{3}[.,]\d{3}", re.IGNORECASE)
_SMALL_STEP_RE = re.compile(r"\bdp\b|cicil\w*|angsur\w*|per\s*bulan|/\s*bulan|uang\s*muka",
                            re.IGNORECASE)


# A millions figure IMMEDIATELY followed by a per-month marker is itself the instalment
# ("Rp 1.670.000 per bulan") — that's the small step, not a total, so it never counts as
# the shock anchor.
_MONTHLY_SUFFIX_RE = re.compile(r"^[\s,]*(?:/|per\s*|se)bulan", re.IGNORECASE)


def price_order_wrong(reply: str) -> list[str]:
    text = reply or ""
    step = _SMALL_STEP_RE.search(text)
    if not step:
        return []
    for million in _MILLIONS_RE.finditer(text):
        if _MONTHLY_SUFFIX_RE.match(text[million.end():million.end() + 14]):
            continue  # a monthly figure, not a total
        if million.start() < step.start():
            return ["full price total appears BEFORE the DP/instalment - lead with the "
                    "smallest real step (DP/cicilan), full amount only after, as context"]
        break  # first real total sits after the small step — order is right
    return []


_LEAD_ANNOYANCE_RE = re.compile(
    r"\b(jangan ganggu|gak usah ganggu|nggak usah ganggu|tolong jangan ganggu|"
    r"berhenti (?:chat|kirim|hubungi|nge-?chat)|stop (?:chat|hubungi|mengirim|nge-?chat)|"
    r"udah jangan (?:chat|hubungi)|capek diganggu|sok asik|sukanya chat.*mulu|"
    # disgust/vulgar rejection words — 'Najis' got 6 more pitches (thread 2833, 24h review);
    # 'jangan ganggu i' (broken english mix, 4417) also slipped the strict phrase list
    r"najis+\b|bangke+\b|anjir+ (?:spam|bot)|jangan ganggu\b|"
    r"diem+(?:in)?\b|shu+t+\b)",
    re.IGNORECASE)


def lead_signaled_annoyance(last_inbound: str) -> bool:
    """The lead's own last message reads as irritation at being contacted — a follow-up
    should never fire on top of this unaddressed."""
    return bool(_LEAD_ANNOYANCE_RE.search((last_inbound or "").strip()))


# Chat 2092: the bot told an Instagram lead to "langsung aja DM aku di Instagram" — but this
# conversation IS the Instagram DM. Stepan has exactly one channel; redirecting a lead who
# is already there to "go DM on Instagram" is always a self-contradiction, never a real
# instruction — no KB context needed to know that.
_WRONG_CHANNEL_RE = re.compile(
    r"\bdm\b[^.!?\n]{0,40}\binstagram\b|\binstagram\b[^.!?\n]{0,40}\bdm\b"
    r"|\bchat(?:kan)?\b[^.!?\n]{0,20}\bdi instagram\b",
    re.IGNORECASE)


def wrong_channel_claims(reply: str) -> list[str]:
    """Telling an Instagram-DM lead to go message on Instagram — always wrong, this IS
    Instagram."""
    return [m.group(0) for m in _WRONG_CHANNEL_RE.finditer(reply or "")]




# Used when the model wants a manager hand-off but we have no phone/WhatsApp for the lead:
# ask for the contact first (a manager can't follow up on a contact-less lead), keeping the
# bot on. Only a later turn WITH a phone actually mutes the bot and escalates.
# The persona addresses the lead as "Kakak" (warm-polite), but the model drifts to the
# familiar "kamu" mid-conversation — threads 4091/4060/2733/2816 mix both in one chat, which
# reads as two different people talking. "kamu" and "Kakak" are grammatically interchangeable
# as a second-person address, so a straight substitution is safe and needs no regen (a prompt
# rule alone didn't hold — the drift is in the model's default register). Applied per bubble.
_KAMU_RE = re.compile(r"\bkamu\b", re.IGNORECASE)


def normalize_address(text: str) -> str:
    """Force the persona's address form: the familiar 'kamu' → 'Kakak' (always capitalised,
    the way an ID honorific is written). Leaves everything else untouched."""
    return _KAMU_RE.sub("Kakak", text or "")


GUARD_HANDOFF_REASON = (
    "Степан не смог составить корректный ответ (сработала защита от выдумок) — "
    "нужен ручной ответ менеджера")

_VERIFY_SYSTEM = (
    "You check a sales bot's draft reply for fabrication. You get the KNOWLEDGE BASE the "
    "bot may use, then the DRAFT. List every CONCRETE factual claim in the draft that is "
    "NOT supported by the knowledge base: invented links, free/discount/trial offers, lab "
    "or resource access, prices, dates, certifications, guarantees, statistics, and COURSE "
    "DURATIONS/LENGTHS. A duration attached to the WRONG product is fabrication too: flag any "
    "duration that doesn't match THAT product's card (e.g. 'SMM Intensive dalam ~4 bulan' when "
    "its card says 2 minggu) — never let a short course's weeks be blurred into a long "
    "program's months, or vice versa. "
    "PROHIBITIONS: the knowledge base contains explicit bans — lines with NEVER / 'does NOT "
    "happen' / 'do NOT invent/promise' / 'jangan' / 'BUKAN'. Flag any draft claim that "
    "promises or asserts something a ban forbids, EVEN IF related words appear elsewhere in "
    "the KB. Examples of bans to enforce: Open House offers no mentor session, no live class / "
    "class demo, no student/alumni project or campaign showcase; a Skill Booster gives an "
    "E-certificate, not BNSP; never promise an income or a guaranteed salary; never state a "
    "discount that isn't written in the KB. So 'kenalan mentor di Open House', 'lihat contoh "
    "karya peserta', 'coba suasana kelas', 'dapat sertifikat BNSP' on a booster, or 'pasti "
    "dapat gaji X' are all violations to flag even though 'mentor', 'peserta', 'BNSP', 'gaji' "
    "appear in the KB. "
    "ALUMNI/SUCCESS-STORY CLAIMS: a specific-sounding story ('salah satu alumni kami yang...', "
    "a named or implied individual with a concrete outcome) is a fabrication UNLESS that exact "
    "case (name, outcome, or link) appears in the knowledge base's Success Cases / Stories "
    "content. A GENERALIZED true statement ('banyak peserta kami mulai dari nol, ada yang jadi "
    "developer, ada yang freelance') is fine even without a specific case — only flag a "
    "SPECIFIC unsourced story. A public figure the KB lists as an EXTERNAL example (e.g. a "
    "founder in Success Cases) must be presented as that public example — flag it if the draft "
    "reframes them as 'alumni/peserta/lulusan kami' (our own student), which is false even "
    "though the name is in the KB. Ignore generic rapport, questions, and paraphrases of KB facts. "
    "Output ONE unsupported claim per line (a short quote or description), nothing else — no "
    "numbering, no JSON, no prose. If everything is grounded, reply with the single word CLEAN.")

_CLEAN_TOKENS = frozenset({"clean", "none", "ok", "grounded", "[]", "-", "n/a", "kosong"})
# a leading list marker only: "- ", "* ", "• ", "1. ", "2) " — not digits inside the claim
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")


def _parse_unsupported(raw: str) -> list[str]:
    """Unsupported-claims list from the verifier's reply. Tolerates the new line-based format
    AND a legacy JSON body ({"unsupported": [...]}), so a stale guard_verify prompt in the DB
    keeps working through the transition."""
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("{") or s.startswith("```"):  # legacy JSON shape
        body = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            items = json.loads(body).get("unsupported") or []
            return [str(x).strip() for x in items if str(x).strip()][:8]
        except (json.JSONDecodeError, AttributeError):
            pass  # not real JSON — fall through to line parsing
    out: list[str] = []
    for line in s.splitlines():
        claim = _LIST_MARKER_RE.sub("", line.strip()).strip()  # drop only a leading bullet/number
        if not claim:
            continue
        if claim.lower() in _CLEAN_TOKENS:  # explicit "all grounded" sentinel
            return []
        out.append(claim)
    return out[:8]


def _grounded_url(url: str, context: str) -> bool:
    u = url.rstrip(".,);’'\"")
    return bool(_BARE_OK.match(u)) or u.lower() in context.lower()


def ungrounded_urls(reply: str, context: str) -> list[str]:
    """URLs in the reply not backed by the KB context — the highest-confidence fabrication."""
    return [u for u in _URL_RE.findall(reply or "") if not _grounded_url(u, context)]


# Promise-shaped claims about things the cards forbid at Open House — meet a mentor, sit in
# on a class, see alumni/student project work — carry no price/URL/gratis word, so is_risky
# missed them and the KB's NEVER lines never got enforced (thread 2879: "kenalan mentor",
# "contoh aplikasi yang dibuat peserta kami"). Trigger a verify so the prohibition is checked.
_PROHIBITION_TOPIC_RE = re.compile(
    r"\b(kenalan|ketemu|bertemu|sesi|ngobrol\s+(?:sama|dengan))\s+mentor"
    r"|\b(suasana|coba|cobain|ikut|masuk|demo|rasakan|rasain)\s+kelas"
    r"|\b(contoh|karya|hasil|project|proyek|portofolio|portfolio)\b[^.?!]{0,30}"
    r"\b(peserta|alumni|siswa|murid|lulusan)\b",
    re.IGNORECASE)


def is_risky(reply: str) -> bool:
    """Cheap gate: does the reply look like it might hand out an offer/resource/link,
    state a concrete price (chat-452 shape), tell a specific alumni/success story
    (chat-1827 shape), or promise an Open-House experience the cards forbid (chat-2879)?"""
    text = reply or ""
    return bool(
        _URL_RE.search(text) or _RISKY_RE.search(text) or _PRICE_RE.search(text)
        or _STORY_RE.search(text) or _PROHIBITION_TOPIC_RE.search(text))


# Price-vocabulary risky words — when these are the ONLY risky trigger and every quoted
# figure is verbatim in the KB context, the draft merely repeats a grounded fact.
_PRICE_WORDS = frozenset({"harga", "biaya", "tarif", "cicilan", "angsuran"})
_MAGNITUDE = {"juta": 1_000_000, "jt": 1_000_000, "ribu": 1_000, "rb": 1_000, "k": 1_000}

_BARE_NUMBER_RE = re.compile(r"\d[\d.,]{3,}")
# A money figure with an OPTIONAL magnitude word — crucially captures that word even behind
# an "Rp" prefix ("Rp2,5 juta"), which the old regex dropped (→ 25 instead of 2_500_000).
_MONEY_RE = re.compile(
    r"(rp\.?\s*)?(\d[\d.,]*)\s*(juta|jt|ribu|rb|k)?\b", re.IGNORECASE)


def _parse_money(num: str, mag: str) -> int | None:
    """A magnitude word makes the number a DECIMAL count of that unit — '2,5 juta' → 2.5 →
    2_500_000, '1,67 juta' → 1_670_000, '500 ribu' → 500_000 (Indonesian ',' = decimal).
    With NO magnitude word, separators are thousands groupers — '1.882.955' → 1_882_955."""
    digits_only = re.sub(r"[^\d]", "", num)
    if not digits_only:
        return None
    if not mag:
        return int(digits_only)
    s = num.replace(",", ".")
    parts = s.split(".")
    if len(parts) > 1:
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return int(round(float(s) * _MAGNITUDE[mag]))
    except ValueError:
        return int(digits_only) * _MAGNITUDE[mag]


def _canonical_prices(text: str, *, liberal: bool = False) -> set[int]:
    """Every money figure in `text` as a canonical integer — 'Rp 1.882.955' → 1882955,
    'Rp2,5 juta' → 2500000, '500rb' / '500 ribu' → 500000 — so a reply figure can be matched
    against the KB regardless of formatting. Strict side: only figures carrying an 'Rp' prefix
    OR a magnitude word count (a bare '16' isn't a price). liberal=True (the KB side) also
    takes bare digit runs ('500,000 IDR'): extra KB numbers only make the subset check
    stricter-side-safe, while the REPLY side stays strict money shapes."""
    out: set[int] = set()
    for m in _MONEY_RE.finditer(text or ""):
        has_rp = bool(m.group(1))
        mag = (m.group(3) or "").lower()
        if not has_rp and not mag:
            continue  # bare number, no Rp and no magnitude word — not a price on the strict side
        val = _parse_money(m.group(2), mag)
        if val is not None:
            out.add(val)
    if liberal:
        for m in _BARE_NUMBER_RE.finditer(text or ""):
            digits = re.sub(r"[^\d]", "", m.group(0))
            if digits:
                out.add(int(digits))
    return out


def price_claims_grounded(reply: str, context: str) -> bool:
    """True when the ONLY thing that made this reply risky is price talk AND every figure it
    quotes appears (canonically) in the KB context — the draft repeats a grounded fact, so
    the LLM verify would spend ~3k tokens re-reading the KB to confirm a substring match we
    can do here (verify fired on 600+ replies/day, mostly exactly this case). Any other
    risky trigger (free/promo/access/story/URL) still goes to the full verify."""
    text = reply or ""
    if _STORY_RE.search(text) or _URL_RE.search(text) or _PROHIBITION_TOPIC_RE.search(text):
        return False
    for m in _RISKY_RE.finditer(text):
        if m.group(0).lower() not in _PRICE_WORDS:
            return False  # a non-price offer word (gratis/promo/akses/…) — verify for real
    prices = _canonical_prices(text)
    if not prices:
        return False  # price words but no figure — nothing to string-match, let the LLM judge
    return prices <= _canonical_prices(context, liberal=True)


# Public alias — the v3 money gate matches figures with exactly these canonicalisation rules
# (Rp prefixes, 'juta'/'ribu' magnitudes, liberal on the KB side); it imports rather than
# growing a second money parser that could disagree with this one.
canonical_prices = _canonical_prices


async def verify_grounding(
    llm: LLMPort, reply: str, context: str, *, branch_id: int,
    thread_id: int, bill: bool = True, budget: object = None, system: str | None = None,
) -> list[str]:
    """LLM grounding check on a risky reply; returns unsupported claims ([] = clean).
    `system` overrides the checker prompt (from the editable `guard_verify` KB doc). `budget`
    (a BudgetService, duck-typed) records this call's cost so the daily cap counts it."""
    messages = [
        {"role": "system", "content": system or _VERIFY_SYSTEM},
        {"role": "user", "content": f"KNOWLEDGE BASE:\n{context[:12000]}\n\nDRAFT:\n{reply}"},
    ]
    try:
        # No require_json_schema: the verifier answers in plain lines, so the broker isn't
        # limited to JSON-mode providers (wider/cheaper pool, fewer timeouts). The parser
        # still accepts a legacy JSON body from a stale guard_verify prompt.
        # chat:smart (2026-07-19): this is the FABRICATION gate — a weak verifier waved
        # '1.500 perusahaan' and 'career guidance' through (threads 2740). A fabrication
        # reaching a customer is the most expensive error, so it is NOT a place to economize;
        # it fires only on risky replies (is_risky), so the volume stays low.
        raw, meta = await llm.chat(
            messages, capability="chat:smart",
            workflow="guard", thread_id=thread_id, branch_id=branch_id)
        if not bill:
            meta.pop("cost_usd", None)  # sandbox verify shouldn't distort cost meta
        elif budget is not None:
            await budget.record(float(meta.get("cost_usd") or 0.0))
        return _parse_unsupported(raw)
    except Exception as exc:  # noqa: BLE001 — a failed verify must not block the reply
        logger.warning("guard verify failed branch=%d thread=%d: %s", branch_id, thread_id, exc)
        return []
