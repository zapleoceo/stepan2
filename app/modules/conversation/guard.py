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

# Re-exported, not reimplemented: every caller reaches the money parser through guard, and
# prices.py owns the one implementation so a second one cannot quietly drift away from it.
from .prices import canonical_prices, quotes_price  # noqa: F401

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


# Claims of having LOOKED at the lead's profile, posts, bio or stories. The bot receives a
# display name and message text — nothing else — so every one of these is invented, and they
# arrive as flattery ("profilnya keren"), which is the most damaging place to be caught out.
#
# Thread 5333 is why this is fail-closed rather than a knowledge-base rule. Turn one:
# "makasih udah share linknya - profilnya keren, suka banget vibesnya", to a lead whose only
# message was a generic autoresponder. A day later, with the KB rule already in place, the
# model read its own earlier claim in the transcript, took it as established fact, and built
# on it with invented specifics: "aku udah mampir ke link profil Kakak … kumpulan AI prompts
# & tools gratisnya juga menarik". A fabrication in the transcript is a fabrication the next
# turn treats as true, so it has to be stopped before it is ever written down.
_PROFILE_CLAIM_RE = re.compile(
    r"(?:mampir ke|lihat|liat|cek|buka|baca|kunjungi|scroll|checked|looked at|visited)\s+"
    r"(?:link\s+)?(?:profil|profile|bio|postingan|posting|konten|story|stori|feed)\w*"
    r"|(?:profil|profile|bio|postingan|feed)\w*\s*(?:kakak|kamu|kk)?\s*"
    r"(?:keren|bagus|menarik|kece|nice|cool)"
    r"|dari\s+(?:postingan|profil|bio|story|feed)\w*\s+(?:kakak|kamu|kk)",
    re.IGNORECASE)


def profile_inspection_claims(reply: str) -> list[str]:
    """Claims to have seen the lead's profile/posts/bio — always invented, never shippable."""
    return [m.group(0).strip() for m in _PROFILE_CLAIM_RE.finditer(reply or "")]


# "Our alumnus, <Name>" — a named person presented as ours. facts_market already forbids this
# twice in prose ("НИКОГДА не говорить 'один из наших выпускников…' без реального связанного
# кейса"; a public figure must be labelled external) and the rule was broken anyway: thread
# 2367 was told "Contoh alumni kami, Pieter Levels" — a well-known indie hacker with no
# connection to the school, checkable in one search. A rule stated only in prose is a rule the
# gate cannot keep.
#
# The name is what makes it unshippable. "Banyak alumni kami mulai freelance" is a claim about
# a group and lands under the honest-archetype line the KB does allow; a person's name is a
# verifiable assertion about a specific human being, and inventing one costs everything the
# transcript said before it.
#
# Case matters here and the flags are scoped accordingly: the keywords are matched
# case-insensitively, the NAME is not. A blanket re.IGNORECASE turns [A-Z][a-z]+ into "any
# word", and "alumni kami yang mulai freelance" reads as a person called Yang Mulai.
_OURS = r"(?i:alumni|alumnus|lulusan|peserta|murid|siswa|student)"
_NAME = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"
_ALUMNI_NAME_RE = re.compile(
    rf"\b{_OURS}\s+(?i:kami|kita)\b[^.!?\n]{{0,20}}?\b({_NAME})"
    rf"|\b({_NAME})\b[^.!?\n]{{0,20}}?\b{_OURS}\s+(?i:kami|kita)\b",
    re.UNICODE)
# …but the same sentence with an explicit "not ours / from our international network" label is
# exactly what facts_market asks for, so it must not trip.
_EXTERNAL_LABEL_RE = re.compile(
    r"\b(bukan\s+alumni|jaringan\s+internasional|luar\s+negeri|bukan\s+dari\s+jakarta|"
    r"contoh\s+dari\s+luar|external|bukan\s+murid\s+kami)\b", re.IGNORECASE)


def fabricated_alumni_claim(reply: str, context: str) -> list[str]:
    """A NAMED person presented as our alumnus/student, whose name is not in the knowledge
    base. Checked per sentence, and skipped where the text labels the example as external."""
    out: list[str] = []
    for part in re.split(r"[.!?\n]|\|\|\|", reply or ""):
        if _EXTERNAL_LABEL_RE.search(part):
            continue
        for m in _ALUMNI_NAME_RE.finditer(part):
            name = (m.group(1) or m.group(2) or "").strip()
            if name and name.lower() not in (context or "").lower():
                out.append(name)
    return out


# A claim about what OTHER people wrote about us. The address on Google Maps is verifiable and
# inviting someone to look is fine; asserting what the reviews SAY is not — nobody here has read
# them. Banned in facts_policy since 27.07 and produced anyway on the very next sim run ("di
# sana banyak review dari siswa dari berbagai program"), which is what moved it into the gate.
# The lead opens the map while still in the chat, and an empty or unrelated review page lands
# at the exact moment they were deciding whether we are real.
_REVIEW_CONTENT_RE = re.compile(
    r"\b(review|ulasan|testimoni|rating|komentar)\w*\b[^.!?\n]{0,40}?"
    r"\b(banyak|bagus|positif|puas|memuaskan|ratusan|banyak banget|tinggi)\b"
    r"|\b(banyak|ratusan|puluhan)\b[^.!?\n]{0,25}?\b(review|ulasan|testimoni)\w*",
    re.IGNORECASE)


# «У меня нет примеров» — сказано, и на этом всё. База знаний запрещает такой ответ прямо:
# «НЕ УХОДИ С ПУСТЫМИ РУКАМИ. Если по ЭТОМУ курсу поимённого кейса нет — назови ближайший
# реальный и сразу скажи, по какой он программе». Правило прозаическое, и модель его обходит:
# тред 5440, 27-31.07.2026 — лид четыре раза просил отзывы, четыре раза получил «джакартских
# нет» плюс адрес и брошюру, и на пятый написал «хватит предлагать, если отзывов нет». Всё это
# время в facts_market лежали поимённые выпускники сети с фото на itstep.ph/review, включая
# ровно его случай: человек прошёл курс и построил собственный интернет-магазин, а лид пришёл
# именно за своим делом.
#
# Голое «у нас нет» — худший из возможных ответов на просьбу о доказательстве: человек просил
# подтверждение, что школа настоящая, а не досье по конкретному курсу. Честное «вот что есть,
# но это другая программа» закрывает запрос; «нет данных» оставляет его с подозрением.
#
# Порядок слов в индонезийском свободный, и обе половины встречаются живьём: «belum ada
# testimoni» и «testimoni ... belum ada». Первая версия ловила только первую, и две из трёх
# реальных фраз треда 5440 прошли мимо — ровно те, где отрицание стоит после существительного.
_PROOF_NOUN = (r"testimoni|testimonial|review|ulasan|contoh|case|cerita|bukti|"
               r"data\s+lokal|referensi")
_NO_HAVE = r"(?:belum|ga|gak|nggak|ngga|tidak|blm)\s+(?:ada|punya|pegang|bisa\s+kasih)"
_NO_PROOF_RE = re.compile(
    rf"\b{_NO_HAVE}\b[^.!?\n]{{0,40}}?\b(?:{_PROOF_NOUN})\w*"
    rf"|\b(?:{_PROOF_NOUN})\w*[^.!?\n]{{0,60}}?\b{_NO_HAVE}\b",
    re.IGNORECASE)
# Признак, что доказательство всё-таки дано: имя выпускника, компания, публичная страница или
# проверяемый факт сети. Список намеренно широкий — задача не поймать каждую формулировку, а
# отличить «нет и точка» от «нет по этой программе, зато есть вот это».
_HAS_PROOF_RE = re.compile(
    r"itstep\.ph|diploma\.itstep\.org|itstep\.id|"
    r"\bUNDP\b|\bAlina\b|\bSarintola\b|\bSreyoun\b|\bSothy\b|\bIsabelle\b|\bSovannak\b|"
    r"\b1Byte\b|\bWiresk\b|\bSPACElogic\b|"
    r"\b(?:110|267|1500)\b|\b1999\b|\b24\s*(?:negara|countries)\b|"
    r"Menara\s+Sudirman",
    re.IGNORECASE)


def empty_handed_refusal(reply: str) -> list[str]:
    """Сказал «примеров нет» и не дал ни одного проверяемого факта взамен."""
    hit = _NO_PROOF_RE.search(reply or "")
    if not hit or _HAS_PROOF_RE.search(reply or ""):
        return []
    return [hit.group(0)]


def review_content_claims(reply: str) -> list[str]:
    """Claims about what our reviews contain — as opposed to where to find them."""
    return [m.group(0).strip() for m in _REVIEW_CONTENT_RE.finditer(reply or "")]


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


# The same impossible promise without naming WhatsApp — "mau saya kirimkan video 5 menit demo
# dashboard?" (live thread, 25 July). The lead answered "Boleh, gratis?" and got "maaf, video
# belum bisa aku kirim lewat chat": we asked for a yes and refused it one turn later, which is
# worse than never offering. Stepan sends TEXT in one Instagram DM thread — no video, no file,
# no module sample, no deck. Offering to TELL them about something is fine; offering to SEND
# an artefact is not, so the verb must be a send-verb and the object a deliverable.
_MEDIA_DELIVERY_RE = re.compile(
    r"\b(?:kirim(?:in|kan)?|share|bagikan|forward)\b[^.!?\n]{0,40}"
    r"\b(?:video|rekaman|file|dokumen|pdf|brosur|katalog|deck|materi|modul|silabus|"
    r"screenshot|foto|gambar|contoh\s+modul|sampel)\b",
    re.IGNORECASE)


def media_delivery_offers(reply: str) -> list[str]:
    """A promise to send a file, video or material into the chat — Stepan can only send text.

    Separate from whatsapp_delivery_offers: that one needs WhatsApp named, this one fires on
    the promise itself regardless of channel."""
    return [m.group(0) for m in _MEDIA_DELIVERY_RE.finditer(reply or "")]


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


# Denying a service is the opposite of inventing one, but the pattern above cannot tell them
# apart: "nggak ada trial gratis atau konsultasi gratis" matched on "gratis atau konsultasi"
# and the gate escalated the one honest answer to "is there a free trial?" — a direct question
# every price-sensitive lead asks. A negation anywhere in the clause before the match means the
# bot is refusing the service, not offering it.
_SERVICE_NEGATION_RE = re.compile(
    r"\b(?:nggak|ngga|gak|ga|tidak|tak|belum|bukan|tanpa|no|not|never|hanya|cuma|satu-satunya)\b",
    re.IGNORECASE)
_NEGATION_WINDOW = 60


def invented_service_offers(reply: str) -> list[str]:
    """A promised service/session/document that is not part of the offering (threads 5018,
    5063). Only a campus visit is free; the Demo Event is a paid, carded offer. Everything
    else here — free consultations, business/marketing strategy sessions, bespoke
    cost/break-even analyses — is invented and must not reach the lead.

    A match preceded by a negation in the same clause is the bot DENYING the service, which
    is exactly what facts_policy tells it to do — never an offer."""
    out: list[str] = []
    for m in _INVENTED_SERVICE_RE.finditer(reply or ""):
        clause_start = max(
            (reply or "").rfind(sep, 0, m.start()) for sep in (".", "!", "?", "\n", "|||"))
        window = (reply or "")[max(clause_start + 1, m.start() - _NEGATION_WINDOW):m.start()]
        if _SERVICE_NEGATION_RE.search(window):
            continue
        out.append(m.group(0))
    return out


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


# A RESULT stated as a percentage — "50% more customers", "sales up 30%". The knowledge base
# bans these outright ("ЧУЖИЕ КЕЙСЫ И ПРОЦЕНТЫ — ЗАПРЕЩЕНЫ … процент результата"), and the
# model produced one anyway, twice in one thread (4799): first as an outside brand, "contoh
# nyata Design Pickle, brand asal Amerika, berhasil dapat 50% pelanggan baru", then eight
# hours later re-attributed to "alumni SMM Intensive" — an invented outside case turned into
# an invented graduate of ours.
#
# The figure came from the prohibition itself: "50%" appears exactly once in the whole
# knowledge base, inside the sentence forbidding it. A rule that names its own counter-example
# hands the model a ready phrase, and nothing downstream caught it — there is no price, no
# link and no rupiah figure, so every existing money check passed it.
#
# Discounts are excluded: ours are real, carded and stated in percent.
_DISCOUNT_CONTEXT_RE = re.compile(
    r"\b(diskon|discount|potongan|hemat|promo|referral|reveral|off)\w*", re.IGNORECASE)
_RESULT_PERCENT_RE = re.compile(
    r"\b\d{1,3}\s*%[^.!?\n]{0,40}?\b(pelanggan|klien|customer|penjualan|sales|omzet|revenue|"
    r"pendapatan|income|follower|engagement|konversi|conversion|leads?|pertumbuhan|growth)\w*"
    r"|\b(pelanggan|klien|customer|penjualan|sales|omzet|revenue|pendapatan|follower|"
    r"engagement|konversi|conversion|leads?)\w*[^.!?\n]{0,40}?\b(?:naik|bertambah|meningkat|"
    r"tumbuh|up)\w*\s*\d{1,3}\s*%",
    re.IGNORECASE)


# A start date given as a WINDOW instead of a date — "kelas mulai akhir Juli", "batch bulan
# depan", "start awal Agustus". stale_dates catches an explicit day that has passed; this
# catches the vaguer promise that never had a day to begin with, and it has now cost two
# threads on two different products: 5366 (SMM Intensive, "kelas mulai akhir Juli ini" on
# 26 July) and 5431 (Vibe Coding, the same words on 27 July). Both times the card carried the
# window and the model repeated it faithfully.
#
# A start date must be a DATE. If the knowledge base has no day, the honest answer is that the
# group is being scheduled — never a month the lead will count the days against.
#
# Scoped to a start/class word within the same clause, so ordinary payment language survives:
# "bayar sisanya sebelum kelas mulai" has no window, "cicilan tiap akhir bulan" has no start.
_VAGUE_START_RE = re.compile(
    r"\b(kelas|batch|angkatan|program|grup|group|start|mulai)\w*\b[^.!?\n]{0,40}?"
    r"\b(akhir|awal|pertengahan|end of|early|mid)\b\s*"
    r"(bulan ini|bulan depan|minggu ini|minggu depan|januari|februari|maret|april|mei|juni|"
    r"juli|agustus|september|oktober|november|desember|jan|feb|mar|apr|jun|jul|aug|agt|sep|"
    r"okt|oct|nov|des|dec)"
    r"|\b(kelas|batch|angkatan|grup|group)\w*\b[^.!?\n]{0,25}?\b(bulan depan|minggu depan)\b",
    re.IGNORECASE)


def vague_start_window(reply: str) -> list[str]:
    """A course start promised as a month or a week rather than a date — never grounded."""
    return [m.group(0).strip() for m in _VAGUE_START_RE.finditer(reply or "")]


def fabricated_result_claim(reply: str) -> list[str]:
    """An outcome quoted as a percentage — always invented, never in the knowledge base.

    Checked per sentence so a discount elsewhere in the message doesn't excuse a result claim
    (and vice versa: "diskon 10%" in its own clause is never flagged)."""
    out: list[str] = []
    for part in re.split(r"[.!?\n]", reply or ""):
        if _DISCOUNT_CONTEXT_RE.search(part):
            continue
        if m := _RESULT_PERCENT_RE.search(part):
            out.append(m.group(0).strip())
    return out


_EARN_WORD_RE = re.compile(
    r"\b(dapat|dapet|penghasilan|gaji|gajih|hasilkan|menghasilkan|income|earning|raup|cuan|"
    r"freelance|proyek|fee|omzet|profit|untung)\w*", re.IGNORECASE)
_PAY_WORD_RE = re.compile(
    r"\b(cicil|angsur|bayar|investasi|dp\b|biaya|harga|total|uang muka|pembayaran)\w*",
    re.IGNORECASE)
_PER_MONTH_MONEY_RE = re.compile(
    r"\d[\d.,]*\s*(?:juta|jt|ribu|rb|k)\b[^.!?\n]{0,18}?(?:per\s*bulan|/?\s*bulan|sebulan"
    r"|perbulan|tiap bulan|sebulannya)", re.IGNORECASE)
# A hedge turns a figure from a PROMISE into a market REFERENCE — the exact framing SALARY_NOTE
# asks for. A salary question must be answerable with the facts_market range ("kisaran gaji
# junior 5-9 juta, tergantung…", thread 5049); grounding it by number is unreliable because the
# KB writes ranges in Russian "млн" while the reply says "juta", so gate on framing instead.
_INCOME_HEDGE_RE = re.compile(
    r"\b(kisaran|sekitar|tergantung|rata-rata|biasanya|umumnya|bervariasi|"
    r"estimasi|range|nggak|ga|gak|tidak|belum)\s*(?:pasti|tentu)?\b", re.IGNORECASE)
# A claim specifically about OUR graduates' earnings is a training-outcome PROMISE — always
# blocked, hedged or not (thread review: "alumni kami rata-rata dapat 8jt" is a liability).
_OUR_GRADS_RE = re.compile(
    r"\b(?:alumni|lulusan|siswa|peserta|murid)\b[^.!?\n]{0,15}\b(?:kami|kita)\b"
    r"|\b(?:kami|kita)\b[^.!?\n]{0,15}\b(?:alumni|lulusan|siswa|peserta|murid)\b",
    re.IGNORECASE)


def is_hedged_salary_reference(bubble: str) -> bool:
    """A per-month money figure framed as a hedged MARKET range about a profession — not a
    course price and not a promise about our graduates. This is the legitimate answer to a
    salary question (thread 5049): 'kisaran gaji … 5-9 juta/bulan, tergantung …'. Its numbers
    must be exempt from BOTH the course-price grounding and the income-promise check, because
    a salary RANGE can never number-match the KB exactly (the KB writes ranges in Russian
    'млн', and 5-8 in the reply won't equal 5-9 in the source)."""
    if _PAY_WORD_RE.search(bubble):
        return False  # a course-payment figure, handled by the normal price grounding
    return bool(
        _PER_MONTH_MONEY_RE.search(bubble) and _EARN_WORD_RE.search(bubble)
        and _INCOME_HEDGE_RE.search(bubble) and not _OUR_GRADS_RE.search(bubble))


def fabricated_income_figure(reply: str) -> list[str]:
    out = []
    for bubble in (reply or "").split("|||"):
        if _PAY_WORD_RE.search(bubble) or is_hedged_salary_reference(bubble):
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
# normalize_address (a blind "kamu" → "Kakak" substitution on every outgoing bubble) was
# removed 2026-07-25. It was written for a weaker model that drifted between the two forms
# mid-chat, but it rewrote the model's words with no way to opt out for a turn: it also hit
# quotes of the lead's own message and phrasings where the swap reads wrong. The address form
# belongs to the persona, which states it plainly, and this class of fix — code editing the
# model's sentences — is what free mode exists to end.


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


def is_risky(reply: str, lang: str = "id") -> bool:
    """Cheap gate: does the reply look like it might hand out an offer/resource/link,
    state a concrete price (chat-452 shape), tell a specific alumni/success story
    (chat-1827 shape), or promise an Open-House experience the cards forbid (chat-2879)?

    The price half reads the branch's own money vocabulary — this is what routes a reply to
    the LLM fabrication verify, and on an English branch an English figure used to slip past
    it silently, taking the whole verify with it."""
    text = reply or ""
    return bool(
        _URL_RE.search(text) or _RISKY_RE.search(text) or quotes_price(text, lang)
        or _STORY_RE.search(text) or _PROHIBITION_TOPIC_RE.search(text))


# Price-vocabulary risky words — when these are the ONLY risky trigger and every quoted
# figure is verbatim in the KB context, the draft merely repeats a grounded fact.
_PRICE_WORDS = frozenset({"harga", "biaya", "tarif", "cicilan", "angsuran"})


def price_claims_grounded(reply: str, context: str, lang: str = "id") -> bool:
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
    prices = canonical_prices(text, lang=lang)
    if not prices:
        return False  # price words but no figure — nothing to string-match, let the LLM judge
    return prices <= canonical_prices(context, liberal=True, lang=lang)


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
