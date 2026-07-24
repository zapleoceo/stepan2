"""Inbound signals — what a message IS, independent of how we answer it.

Extracted from the retired situational-nudge cascade. These are not reply tactics: they are
classifications other modules legitimately need — ingest deciding a business auto-responder
isn't a lead, the comment triage deciding a public comment is worth answering, the digest
separating ad prefill from the lead's own words, and the follow-up timer honouring an explicit
"ask me in two weeks".

Everything here answers a question about the INCOMING message. Nothing here decides what the
bot says — that is the model's job now.
"""
from __future__ import annotations

import re

# The click-to-message ad prefill families — a button click, not the lead's own words. Three
# canned openers seen at scale (2026-07: 609 threads on the second family, 163 on the third):
# "💻 Ceritakan lebih detail tentang program …", "Halo, saya ingin tahu detail program X dan
# biaya kursusnya 😊", and "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?".
# An emoji prefix is tolerated. Nothing here may ever be treated as the lead speaking: not
# for needs, not for answer-first, not for a price (the unrecognized third family made the
# bot dump a full price block as its FIRST message on thread 4500).
AD_TEMPLATE_RE = re.compile(
    r"^[^a-zA-Z]*(ceritakan lebih detail tentang program"
    r"|(halo[\s,!]*)?(saya |aku )?ingin tahu detail program"
    r"|(halo[\s,!]*)?tertarik kursus\W*boleh info jadwal)",
    re.IGNORECASE)

# A concrete, answerable question from the lead — a "?" or a question/money/enroll keyword.
# Concrete keywords only — NOT the bare "gimana/how", which is the vague dead-end the
# clarify→escalate loop is meant to catch. An explicit "?" still counts ("gimana caranya?").
# 'online'/'offline' are deliberately NOT bare keywords: they read as a FORMAT ANSWER as often
# as a question ("online dari rumah" — thread 4086 — is the lead picking a format, not asking
# anything), and treating that as an answerable question fired answer-first, which the model
# filled with the full price. With a real '?' they still count (the \? alternation).


# ANY reshared Instagram post, not just our own — same structural signature (icon, handle,
# middot, the SAME handle repeated, then the post's own caption). thread 4847: a lead reshared
# an unrelated meme ("mistery219jakarta · mistery219jakarta DI LARANG PANSOS !!" — "no
# showing off", a joke caption) and the caption got judged as the LEAD's own objection to being
# pitched, tripping a false critic rejection — the exact OWN_POST_SHARE_RE bug, just for a
# third-party account this narrower pattern doesn't cover. The caption of someone else's post
# is never the lead's own statement regardless of whose account posted it.
ANY_POST_SHARE_RE = re.compile(r"^[📷🎬📖🎥🎞👤]\s*(\S+)\s*·\s*\1\b", re.IGNORECASE)


# A business account's own auto-responder, not a human. Thread 2503: "Halo, terima kasih
# sudah menghubungi kami. Kami sudah menerima pesan Anda..." arrived as an inbound; Stepan
# took it for the lead speaking, reset the follow-up cycle and politely asked the robot about
# its goals. An auto-reply is not a reply: it must neither restart the timer nor earn an
# answer, and it certainly doesn't unlock the price (that's what lead_spoke_own_words gates).
AUTO_REPLY_RE = re.compile(
    r"(terima kasih (?:sudah|telah) menghubungi (?:kami|kita)"
    r"|kami (?:sudah|telah) menerima pesan"
    r"|pesan (?:ini )?otomatis|balasan otomatis"
    r"|akan (?:segera )?(?:kami )?balas secepatnya"
    r"|thank you for (?:contacting|reaching out)"
    r"|thank(?:s| you) for your message"
    r"|we (?:have )?received your message"
    r"|we(?:'| wi)?ll get back to you"
    r"|this is an? automated|auto[- ]reply)",
    re.IGNORECASE)


def is_auto_reply(text: str) -> bool:
    """The inbound is the lead's own auto-responder firing, not the lead."""
    return bool(AUTO_REPLY_RE.search((text or "").strip()))


# A concrete, answerable question from the lead — a "?" or a question/money/enroll keyword.
# Concrete keywords only — NOT the bare "gimana/how", which is the vague dead-end the
# clarify→escalate loop is meant to catch. An explicit "?" still counts ("gimana caranya?").
# 'online'/'offline' are deliberately NOT bare keywords: they read as a FORMAT ANSWER as often
# as a question ("online dari rumah" — thread 4086 — is the lead picking a format, not asking
# anything), and treating that as an answerable question fired answer-first, which the model
# filled with the full price. With a real '?' they still count (the \? alternation).
ANSWERABLE_Q_RE = re.compile(
    r"\?|\b(harus|apakah|berapa|kapan|di\s?mana|modal|bayar|berbayar|gratis|biaya|harga|"
    r"cicil\w*|daftar|syarat|sertif|bnsp|jadwal|lokasi|durasi|gaji|gajih|salary)\b"
    # 'what's taught' questions — Indonesian glues suffixes (kurikulum-nya, di-pelajari), so
    # match the stem with any trailing letters rather than a hard word boundary (bench 3917:
    # "apa aja materi yang dikasih" got a WhatsApp stub instead of the syllabus).
    r"|\b(?:materi|kurikulum|modul|silabus|diajar|dipelajari|pelajari)\w*",
    re.IGNORECASE)

# A polite Indonesian 'not now' — usually a real 'no' wrapped to save face (gengsi). Pushing
# price/DP through it is the single biggest ghost-maker found in the audits.


def is_answerable_question(text: str) -> bool:
    return bool(ANSWERABLE_Q_RE.search(text or ""))


# A business account's own auto-responder, not a human. Thread 2503: "Halo, terima kasih
# sudah menghubungi kami. Kami sudah menerima pesan Anda..." arrived as an inbound; Stepan
# took it for the lead speaking, reset the follow-up cycle and politely asked the robot about
# its goals. An auto-reply is not a reply: it must neither restart the timer nor earn an
# answer, and it certainly doesn't unlock the price (that's what lead_spoke_own_words gates).


# The lead says they WANT IN ('saya ingin bergabung', 'mau daftar') — one notch before the
# how-to-pay question. Live loss (thread 4194, 2026-07-17): 'saya ingin bergabung..' was
# answered with a wall of price + bank account + schedule in one block; two minutes later —
# 'maaf ka kayanya saya gabisa deh'. A buyer needs ONE small step, not an invoice.
# The gas-family ('yaudh gas', 'gaskeun') is chat-Indonesian for 'let's go' — sim s10:
# 'yaudh gas' after an installment question got the clarify MENU at the exact buying moment.
BUYING_SIGNAL_RE = re.compile(
    r"\b(mau|ingin|pengen|pgn|siap|langsung)\s+(daftar|gabung|bergabung|ikut(an)?|join|"
    r"ambil\s+kelas)\b|\bdaftar(kan)?\s+(saya|aku|dong|sekarang)\b|\bberminat\s+daftar\b"
    r"|\bgas(s|keun|kan)?\b|\bgasken\b",
    re.IGNORECASE)


# The lead is asking HOW/WHERE to pay — the strongest buying signal there is. Thread 2821:
# "No rek min" (give me the account number) got a certificate pitch and two WhatsApp asks
# instead of the payment details; the buyer walked at the checkout.
PAYMENT_INTENT_RE = re.compile(
    r"no\.?\s*rek|norek|rekening|qris|virtual\s*account|"
    r"(cara|gimana|ke\s*mana|kemana)\s*(nya)?\s*(bayar|transfer)|"
    r"mau\s+(bayar|transfer)|bayar\s+ke|transfer\s+ke",
    re.IGNORECASE)

# The lead says they WANT IN ('saya ingin bergabung', 'mau daftar') — one notch before the
# how-to-pay question. Live loss (thread 4194, 2026-07-17): 'saya ingin bergabung..' was
# answered with a wall of price + bank account + schedule in one block; two minutes later —
# 'maaf ka kayanya saya gabisa deh'. A buyer needs ONE small step, not an invoice.
# The gas-family ('yaudh gas', 'gaskeun') is chat-Indonesian for 'let's go' — sim s10:
# 'yaudh gas' after an installment question got the clarify MENU at the exact buying moment.


# The engaged lead who hasn't revealed a pain yet AND didn't ask the price. The model's reflex
# is to fill the silence with the fee — thread 4086: a lead who only picked "online dari rumah"
# got the full Rp 15.030.000 total unprompted and went quiet. A price dropped before there's a
# reason to pay it is just a number to balk at. Steer to ONE discovery question instead. Stops
# on its own once a pain is captured (need-payoff takes over) or past the discovery cap
# (discovery-cap presents what we have). Never fires when the lead DID ask — answer-first owns
# that (a direct price question gets the price).
# The single biggest measured leak (100-thread funnel, 2026-07-16): a price was quoted in 65
# chats and in 46 of them (71%) the lead never wrote again. Pain was on record in only 18% of
# chats while a price went out in 65% — so the number lands on someone whose problem we never
# learned, and a bare figure with nothing behind it is just something to reject. Refusing to
# answer is NOT the fix (they asked, and the ad's own button invites the price question) —
# what changes is the framing around the number.
PRICE_QUESTION_RE = re.compile(
    r"\b(berapa|harga|biaya|tarif|cicil\w*|angsuran|murah|mahal|gratis|bayar|berbayar|modal)\b",
    re.IGNORECASE)

# The lead is asking HOW/WHERE to pay — the strongest buying signal there is. Thread 2821:
# "No rek min" (give me the account number) got a certificate pitch and two WhatsApp asks
# instead of the payment details; the buyer walked at the checkout.


# A polite Indonesian 'not now' — usually a real 'no' wrapped to save face (gengsi). Pushing
# price/DP through it is the single biggest ghost-maker found in the audits.
SOFT_NO_RE = re.compile(
    # 'pikir' is also spelt 'fikir', and takes the -kan/-in suffixes: "Nanti saya fikirkan
    # lagi ya kak" (thread 2689) matched nothing here, so the soft-no handling never fired at
    # all and the model dormant-ed the lead instead.
    r"\b(nanti\s*(aja|dulu|ya|lah)|nti\s*dulu|[pf]ikir(kan|in)?[- ]?([pf]ikir\s*)?(dulu|lagi)|"
    # 'mikir mikir dulu' (repeated word), and 'simpen/simpan dulu' + 'ngamanin info dulu'
    # (I'll save it / just securing the info first) — classic warm-postponer soft-declines
    # missed live (thread 4520: the bot pushed DP + a WhatsApp ask instead of easing off).
    # 'dlu'/'dl' is the chat abbreviation of 'dulu' — accept both via du?lu.
    r"mikir[- ]?(mikir\s*)?(in|kan)?\s*d[u]?lu|"
    r"si?mp[ae]n\s*(d[u]?lu|info)|(ngamanin|amankan|simpan)\s*(informasi|info)\s*d[u]?lu|"
    # 'ngumpulin/kumpulin duit dulu' (saving up first) and 'nanti kalo … kabarin/hubungin'
    # (I'll reach out when I'm ready) — graceful postpone/close forms that fell through to the
    # clarify menu (thread 4520). 'nanti aja' already matches; these two didn't.
    r"(ng?umpulin|kumpul(in|kan)?)\s*(duit|uang|dana|modal)|"
    r"(kalo|kalau|klo)[^\n]{0,80}(kabarin|kabari|hubungin|hubungi)\s*(kaka|kakak|lagi|kk)|"
    # 'belum ada/punya' needs a REFUSAL object: bare 'belum ada pengalaman, bisa ikut?' is a
    # warm QUALIFYING question, not a no — matching it sent the whole follow-up cadence into
    # the soft-no snooze (sales-logic audit 2026-07-19, #6).
    r"nabung\s*dulu|belum\s*(ada|punya)\s*(biaya|uang|dana|duit|modal|budget|niat|minat|"
    r"waktu)|belum\s*(siap|kepikiran)|lain\s*kali|next\s*time|nex\s*(aja|kk)|"
    # A polite "not interested (yet)" is the most common face-saving refusal and was missed
    # entirely (thread 2949: "maaf belum tertarik" got a discovery question + a follow-up an
    # hour later, straight over the no, instead of the soft-no easing-off and objection snooze).
    r"(belum|blm|ga|gak|nggak|ngga|ndak|tidak|tdk)\s*(tertarik|tertarikan|minat|berminat)|"
    # 'tidak/gak jadi' = backing out / changed my mind (thread 2811: "maaf KA tidak jadi",
    # "saya tidak jadi") — a clear refusal the soft-no detector was missing entirely.
    r"(tidak|tdk|gak|ga|nggak|ngga|ndak|gk)\s*jadi\b|"
    r"insya\s*allah|liat\s*(nanti|dulu)|kapan[- ]?kapan|(?:nggak|ngga|ndak|gak|ga|gk)\s*dulu|"
    # Blunt slang refusals the polite-postpone forms above all miss — 'ga usah' (no need),
    # 'ga ikutan' (not joining), 'ogah' (a hard no), bare 'g dulu' (the 'ga dulu' chat
    # abbreviation). Thread 4280: a self-identified schoolkid said 'GA USAH' / 'Ga ikutan gw' /
    # 'G dulu makasih' and the bot kept pitching student-discount + Diploma + Open House because
    # none of these registered as a decline, so the soft-no ease-off never fired. The negation
    # prefix keeps 'mau ikutan' (a YES) out; 'males' is deliberately NOT here — 'males kerja
    # gini' is a career PAIN, not a course refusal.
    r"(?:nggak|ngga|ndak|gak|ga|gk|g)\s*usah\b|ga+usah\b|"
    r"(?:nggak|ngga|ndak|gak|ga|gk)\s*ikut(?:an)?\b|\bogah\b|\bg\s+dulu\b|"
    r"(tanya|diskusi|izin|ngobrol)\S*\s*(sama|ke|dulu)?\s*"
    r"(istri|suami|orang\s*tua|ortu|bapak|ibu|keluarga|mama|papa|nyokap|bokap))",
    re.IGNORECASE)

# Money is tight / no income — the full-course price must not lead (UMR context: the course
# is ~3 monthly salaries; the cheap 1-day entries exist exactly for this).


# The lead NAMED a timeframe in their postpone ("bulan depan", "abis gajian", "2 minggu
# lagi") — that's a dated 'later', gold for follow-up timing. Parsed deterministically in
# reply._snooze_on_soft_no so the re-contact lands when THEY said, not on a fixed +7d guess.
POSTPONE_UNTIL_RE = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>hari|minggu|bulan)\b"
    r"|\b(?P<besok>besok)\b|\b(?P<lusa>lusa)\b"
    r"|(?P<mingdep>minggu\s*depan)|(?P<bulandep>bulan\s*depan)"
    r"|(?P<akhirbulan>akhir\s*bulan)|(?P<gajian>(abis|habis|setelah|nunggu)\s*gajian)",
    re.IGNORECASE)


def postpone_days(text: str) -> int | None:
    """Days until the lead's own named re-contact time, or None if none named."""
    m = POSTPONE_UNTIL_RE.search(text or "")
    if not m:
        return None
    if m.group("num"):
        n = int(m.group("num"))
        return n * {"hari": 1, "minggu": 7, "bulan": 30}[m.group("unit").lower()]
    if m.group("besok"):
        return 1
    if m.group("lusa"):
        return 2
    if m.group("mingdep"):
        return 7
    if m.group("bulandep"):
        return 30
    if m.group("akhirbulan") or m.group("gajian"):
        # end of month ≈ payday window (gajian): re-contact lands 25th-ish, when money exists
        from datetime import UTC, datetime  # noqa: PLC0415
        today = datetime.now(UTC).day
        return max(2, 26 - today) if today < 26 else 30
    return None


# Legitimacy doubt — trust is the #1 purchase barrier in Indonesian chat commerce (34% fear
# fraud). 'Apakah ini real?' answered with the clarify MENU (thread 4435, 24h review) is the
# worst possible reply: a doubt met with a form. Answer with VERIFIABLE facts + an invitation
# to check us, never defensiveness.


# After this many lead turns the discovery gate stops forcing more questions and presents on
# what we have — the escape hatch for a lead who won't voice a pain/gain. Was 2 (too
# aggressive: the ad-opener burns turn 1 — thread 1081); 4 gives discovery real room. Used by
# both the stage gate (reply._stage_for) and the need-payoff/discovery-cap nudges below, so a
# non-yielding lead is released by BOTH at the same turn — they used to disagree, leaving the
# need-payoff nudge blocking the pitch forever after the gate had already let go.
DISCOVERY_TURN_CAP = 4

# ─── detectors ───────────────────────────────────────────────────────────────

# The click-to-message ad prefill families — a button click, not the lead's own words. Three
# canned openers seen at scale (2026-07: 609 threads on the second family, 163 on the third):
# "💻 Ceritakan lebih detail tentang program …", "Halo, saya ingin tahu detail program X dan
# biaya kursusnya 😊", and "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?".
# An emoji prefix is tolerated. Nothing here may ever be treated as the lead speaking: not
# for needs, not for answer-first, not for a price (the unrecognized third family made the
# bot dump a full price block as its FIRST message on thread 4500).
