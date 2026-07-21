"""Situational dialogue steering — one module owns "what special turn is this?".

The static 26k-char prompt carries every sales rule, but live audits (100 dialogs on
2026-07-15, 169 more the same week) showed the model follows them UNRELIABLY at that scale:
it pushed DP after a polite 'nanti', priced full courses to unemployed leads, pitched DP at
school kids, and answered a direct question with a clarify stub. The reliable pattern is a
deterministic detector + ONE short instruction injected at the exact turn (same mechanism as
the reply-guard correction). All of those detectors and nudges live HERE, and `pick_nudge`
is the single priority chain — previously they were inlined in reply.py, where two parallel
edits once defined the same nudge twice and conflicts between rules went unnoticed.

Priority (first match wins, at most one nudge per turn — token-light by design):
  non-target > ad-opener > unseen-media > minor > soft-no > answer-first > low-budget
  > need-payoff > discovery-cap
Two documented COMBOS soften rule conflicts instead of dropping one side:
  soft-no + a real question  → answer briefly, then ease off (never leave a question hanging)
  answer-first + tight budget → answer the price honestly, cheapest entry beside it
"""
from __future__ import annotations

import re

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH

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
LOW_BUDGET_RE = re.compile(
    r"\b(?:nggak|ngga|ndak|tidak|tdk|gak|ga|gk|belum)\s*(?:ada|punya)?\s*"
    r"(?:duit|uang|modal|biaya|dana|ongkos)"
    r"|ga\s*sanggup|(?:nggak|ngga|ndak|gak|ga|gk)\s*mampu|"
    r"mahal\s*(banget|amat|bgt|bener|sekali)|kemahalan|"
    r"gratis(an|in)?|belum\s*(kerja|ada\s*penghasilan)|nganggur|pengangguran|"
    r"butuh\s*(kerja|duit|uang|kerjaan)|lagi\s*bokek|"
    # thread 4082: 'Kendala saya di budget… terasa berat' voiced a clear money objection and
    # got a dream question instead of the cheap entry — the word 'budget' wasn't matched.
    r"kendala\s*\S*\s*budget|bud?get\s*(terbatas|minim|pas[- ]?pasan|kurang)|"
    r"(te)?rasa\s+berat|masih\s+berat|keberatan\b"
    # thread 4545: 'saya ga punya buat bayar biaya nya' — a plain "can't afford" that the
    # money-word-adjacent rules above missed (words sit between 'punya' and 'biaya'), so the
    # bot skipped the budget play and escalated to a manager instead of offering instalments.
    r"|(?:nggak|ngga|ndak|tidak|tdk|gak|ga|gk|belum|blm)\s*(?:ada|punya|cukup)"
    r"[^.!?\n]{0,15}?\bbayar\b"
    r"|(?:nggak|ngga|gak|ga|tdk|tidak|belum)\s*(?:cukup|sanggup)\s*(?:buat|untuk)?\s*bayar"
    r"|(?:duit|uang|dana|biaya|budget)\w*\s*(?:nggak|ngga|gak|ga|belum|kurang|ga\s*cukup)",
    re.IGNORECASE)

# The TIME objection — the top non-price reason a warm lead stalls ('nggak ada waktu', 'sibuk',
# 'waktunya padet', 'gak sempat'). It hides inside SOFT_NO ('belum ada waktu') but capitulates
# there ('kabari kalau ada waktu', thread 4062) instead of reframing the REAL, small weekly
# commitment. Split it out so it gets the time-specific grounded reframe, same as LOW_BUDGET
# has its own. 'waktu luang' (free time) and 'kapan waktunya' (a schedule question) must NOT
# match — only a scarcity/busy reading does.
NO_TIME_RE = re.compile(
    r"\b(?:nggak|ngga|ndak|tidak|tdk|gak|ga|gk|belum|blm|gada|nda)\s*(?:ada\s*|punya\s*|"
    r"sempet\s*|sempat\s*)?waktu\b"
    r"|\b(?:gak|ga|nggak|ngga|tdk|tidak|gk|belum|blm)\s*(?:sempat|sempet)\b"
    r"|\b(?:lagi\s*)?sibuk\b|\bkesibukan\b|\blagi\s*repot\b"
    r"|\b(?:jadwal|waktu|hari|kerjaan)\w*\s*(?:padet|padat|penuh|mepet)"
    r"|banyak\s*(?:kerjaan|kegiatan|kesibukan)",
    re.IGNORECASE)

# School-age lead OR a parent asking for their child — either way the PARENT pays and decides.
# ('kelas 10-12' is a school grade; 'kelas 1 hari' is a course format — the \b keeps them apart.)
MINOR_RE = re.compile(
    r"\b(smp|sma|smk|mts)\b|kelas\s*(10|11|12|sepuluh|sebelas|dua\s*belas)\b|"
    r"masih\s*sekolah|anak\s*(saya|sy|ku|nya)\b|umur\s*1[0-7]\b|\b1[0-7]\s*(tahun|thn)\b",
    re.IGNORECASE)

# Content the bot genuinely CANNOT read: a reel/post IG won't hand over, a bare share that
# carries only an account handle, or an image/voice the broker never described.
UNSEEN_MEDIA_RE = re.compile(
    r"message unavailable|deleted by its owner|hidden by their privacy"
    r"|^(?:🖼\s*media|🎤\s*voice|🎬\s*reel|📖\s*story|📎\s*attachment|🔗\s*link)$"
    r"|^[📷🎬📖👤]\s*\S+$",  # bare share: icon + handle, no caption to read
    re.IGNORECASE)


def is_answerable_question(text: str) -> bool:
    return bool(ANSWERABLE_Q_RE.search(text or ""))


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


def lead_spoke_own_words(dialog) -> bool:  # noqa: ANN001
    """True once ANY inbound is something the lead actually typed/said — not an ad's
    prefilled opener, not an unresolved media placeholder, and not their own auto-responder."""
    for m in dialog:
        if m.direction != "in":
            continue
        text = (m.text or "").strip()
        if (not text or AD_TEMPLATE_RE.match(text) or is_auto_reply(text)
                or OWN_POST_SHARE_RE.match(text)):
            continue
        if text in (VOICE_PENDING_PH, IMAGE_PENDING_PH):
            continue
        return True
    return False


def unseen_media_in_turn(dialog) -> bool:  # noqa: ANN001
    """Did the lead's CURRENT turn (everything since our last send) include content we can't
    read? The placeholder is often not the last message — thread 3058 sent the unavailable
    reel, then 'Like2 ders' — so checking only the last inbound would miss it."""
    for m in reversed(dialog):
        if m.direction == "out":
            break
        if m.direction == "in" and UNSEEN_MEDIA_RE.search((m.text or "").strip()):
            return True
    return False


# ─── nudges (one short [System: …] block, injected at the exact turn) ─────────

NON_TARGET_NUDGE = (
    "[System: this lead was already classified non_target (wrong audience / off-topic / "
    "not interested in our programs) in an earlier turn and is still off-topic. Do NOT "
    "keep pitching or asking discovery questions — write ONE short, polite closing line "
    "and stop there; only re-engage if THEY bring up a real interest in one of our "
    "programs. Return the JSON as usual.]"
)

# The single highest-leverage message in the funnel: 44% of ad clicks never write a word
# back, and the bare "apa tujuan utama Kakak?" this used to produce is why — the lead pressed
# a button that PROMISED details, got an interview question, and had to compose an essay
# about their goals to continue. Give a little, prove we're a real campus (penipuan fear),
# and keep answering easy by naming concrete options they can pick in a few words. The options
# are shown NUMBERED for skimmability but the lead answers in words, not by sending a digit —
# bare-number replies ('1 2') were being misparsed into the wrong option (thread 4719).
AD_OPENER_NUDGE = (
    "[System: the lead's ONLY message so far is the ad's prefilled opener (a BUTTON CLICK, not "
    "their own words) — they tapped an ad, nothing more. This single reply decides the whole "
    "conversation: most ad clicks never write back, so it must feel like a warm human opening a "
    "chat, not a form. Write it as 2-3 SHORT Instagram-DM bubbles separated by '|||', in "
    "friendly everyday Bahasa with a few natural emoji, in this shape:\n"
    "1) Greet warmly + say who you are (MinStep dari IT STEP Academy Jakarta, kampus di Menara "
    "Sudirman) — a REAL physical campus they can drop by anytime; naming it quietly answers the "
    "silent 'is this real / penipuan?' fear that gates every Indonesian chat sale, without "
    "sounding defensive. Use their name ONLY if it looks like a real name, never a raw username "
    "like 'MENNN08'.\n"
    "2) ONE short hook: why this topic is worth their time, in THEIR world — what it lets a "
    "person actually DO. One or two lines, concrete, no hype. This is what they clicked for, so "
    "do not leave them empty-handed.\n"
    "3) ONE easy question that lays out the usual reasons people come as a short NUMBERED list "
    "(1️⃣ 2️⃣ 3️⃣ 4️⃣ — the numbers are just to keep it skimmable) — switch career / build their "
    "own thing or level up at work / mencari kursus buat anaknya / just curious. But do NOT ask "
    "them to reply with a digit ('cukup kirim nomornya' / 'balas angkanya'): invite them to "
    "answer in THEIR OWN WORDS — which one fits, or cerita sedikit. Bare-number replies like "
    "'1 2' get misread (thread 4719: '1 2' was parsed as the wrong option). FORMAT the options "
    "vertically — EACH numbered option on its OWN line (a line break before every 1️⃣ 2️⃣ 3️⃣ "
    "4️⃣), never strung together on one line: a menu crammed into a single line is a wall of "
    "text in a DM (thread 4736), one option per line is how Indonesians read it on Instagram. "
    "ALWAYS include the for-my-child option: parents shopping for a kid are a real segment with "
    "their own programs, and knowing it on turn one sets the whole sell path (the parent decides "
    "and pays).\n"
    "⛔ Still NO price, NO schedule, NO module list, NO brochure dump — those come once they "
    "tell you which way they lean. This holds EVEN THOUGH the button's canned text asks for "
    "them ('…dan biaya kursusnya', 'ceritakan lebih detail'): that wording is the ad's, not the "
    "lead's — nobody typed it, so it is not a question and quoting a price at it is answering "
    "an ad, not a person. Keep stage qualifying. Return the JSON as usual.]"
)

UNSEEN_MEDIA_NUDGE = (
    "[System: the lead sent something you CANNOT see — a shared post/reel/story, an image or "
    "a voice note whose content never reached you (deleted, private, or just not readable on "
    "your side). You only received a placeholder, NOT the content itself. Do NOT guess what it "
    "showed, do NOT invent a topic from the account name, and do NOT reply with a generic "
    "clarifier. Say plainly and warmly that it doesn't open on your side, and ask them to tell "
    "you in their own words what it was about or what they want to know. Return the JSON as "
    "usual.]"
)

MINOR_NUDGE = (
    "[System: this turn involves a school-age person (SMP/SMA/SMK, 'kelas 10-12', 'masih "
    "sekolah') — either the lead themselves, or a PARENT asking for their child. The parent is "
    "the payer and decision-maker in both cases, so never push DP or the full price at a "
    "student. If the LEAD is the student: encourage them warmly, mention the 10% student "
    "discount, and suggest coming to the free Open House with a parent. If the lead is the "
    "PARENT: talk to them as the decision-maker directly — what their child would learn and "
    "the Open House to see it live; answer their questions normally (a parent asking the price "
    "may hear it honestly). Positive, no pressure. Return the JSON as usual.]"
)

# FIRST objection/soft-no → work it once (IT STEP objection method, Asia-softened): a first
# 'no' is usually a reflex, not a decision. Surface the REAL reason, reframe it honestly, offer
# a soft step — then, if they hold, SOFT_NO_NUDGE eases off next time. This is the single turn
# where we don't just fold (thread 2949: 'belum tertarik' got an instant capitulation and the
# sale was left on the table).
OBJECTION_HANDLE_NUDGE = (
    "[System: the lead gave their FIRST soft 'no' / objection this conversation ('belum "
    "tertarik', 'nanti dulu', 'mahal', 'mikir dulu', 'tidak jadi'). A first 'no' is usually a "
    "reflex, not a final decision — do NOT capitulate ('oke kabari kalau minat' leaves the sale "
    "on the table), but do NOT hard-push either. ONE calm objection-handling move, in the "
    "lead's language:\n"
    "1) ACKNOWLEDGE sincerely, zero pressure ('paham banget Kak, wajar kok mikir dulu').\n"
    "2) SURFACE the real reason with ONE gentle question if they haven't named it: 'boleh tau, "
    "yang bikin ragu lebih ke biayanya, waktunya, atau masih belum yakin cocok buat Kakak?' — "
    "so you handle the REAL objection, not a guess.\n"
    "3) If the reason IS known, reframe it honestly from the KB only: PRICE → value + the "
    "smallest real step (DP/instalment), never invent an ROI %/salary; TIME → why the real "
    "duration exists (hands-on practice/projects); TRUST/bad-reviews → a real Success Case from "
    "the KB, never fabricated; NO MONEY NOW → the cheaper 1-day Skill Booster or free Open "
    "House; NOT SURE IT FITS → tie one concrete outcome to their stated goal.\n"
    "4) End with ONE soft, low-pressure next step - a light question or the free Open House - "
    "NEVER a 'lock your seat today' hard close.\n"
    "This is the ONE attempt. If the lead declines AGAIN after this, ease off fully. If they "
    "ALSO asked a real question, answer it first (KB fact), then handle the objection. Facts "
    "ONLY from the KB, never fabricate a number, case, or claim. Return the JSON as usual.]"
)

# A BARE decision-postpone — 'nanti aja / mikir dulu / kalau jadi nanti konfirmasi' — with NO
# money/time/trust reason named (those route earlier in the chain). The generic soft-no move is
# 'isolate the doubt' (ask what's wrong), which is the WRONG move here: there's nothing to
# isolate, they're just deferring. The high-conversion move is a GENTLE opportunity-cost seed +
# a low-friction step. Distinct enough from SOFT_NO_RE that placing it first only steals the
# bare-postpone cases, not 'belum tertarik' / 'makasih' etc.
POSTPONE_RE = re.compile(
    r"\bnanti\s+(aja|dulu|lah|ya|saja)\b"
    r"|\bnanti\s+(saya|aku|ku|gua|gue)?\s*(kabar\w*|konfirmasi|chat|hubungi|info\w*)"
    r"|\bkalau\s+(nanti|udah\s+siap|dah\s+siap|jadi)\b"
    r"|\b(mikir|pikir)[\s-]*(mikir|pikir)?\s*(dulu|lagi)\b"
    r"|\bpikir[\s-]*pikir\b"
    r"|\bbelum\s+(sekarang|kepikiran)\b"
    r"|\blain\s+(kali|waktu)\b"
    r"|\bntar\s+(aja|dulu)\b",
    re.IGNORECASE)

POSTPONE_NUDGE = (
    "[System: the lead is POSTPONING the DECISION ('nanti aja', 'mikir dulu', 'kalau jadi nanti "
    "aku konfirmasi') WITHOUT naming a money/time/trust blocker. This is procrastination, not a "
    "real objection — the high-conversion move is a GENTLE opportunity-cost seed + a low-friction "
    "step, NOT a generic 'what's holding you back'. ONE warm message in the lead's language:\n"
    "1) ACKNOWLEDGE with zero pressure ('santai Kak, wajar dipikir dulu 😊').\n"
    "2) WEAVE ONE short, gentle opportunity-cost line — stated as a fact, never a threat or fake "
    "scarcity: in this field the ones who start earlier finish earlier and take the projects/"
    "clients earlier, and with AI the moment to start is now.\n"
    "3) LOWER THE FRICTION so 'later' becomes a small step NOW: prefer the paid Demo Event (a "
    "real dated try-before-you-buy session) or locking today's price with a DP and deciding "
    "fully later. If you mention the free Open House, frame it as a RELAXED office drop-by "
    "whenever suits them ('kapan cocok mampir santai-santai?') and ask if it's convenient — "
    "NEVER a fixed 'datang Kamis ini' event.\n"
    "4) End with ONE soft question, never a hard close. NO 'now or never', NO invented scarcity "
    "('sisa 2 kursi' only if the KB says so). If the lead postpones AGAIN after this, ease off "
    "fully. Facts ONLY from the KB. Return the JSON as usual.]")

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
TRUST_DOUBT_RE = re.compile(
    r"\b(real(?![- ]?tim)|asli|beneran|resmi|legal(itas)?|terpercaya|penipuan|penipu|"
    r"tipu[- ]?tipu|scam+|bodong|abal[- ]?abal|fiktif|amanah|"
    r"aman\s*(ga|gak|nggak|kah|nya)?)\b",
    re.IGNORECASE)

TRUST_DOUBT_NUDGE = (
    "[System: the lead just questioned whether we are LEGITIMATE ('apakah ini real / scam / "
    "resmi?'). This is the #1 purchase barrier here - treat it as a golden moment, never "
    "defensively and NEVER with a menu/counter-question. Reply with VERIFIABLE facts from the "
    "KB only: the physical campus (Menara Sudirman lt.8, Jakarta), operating since 1999 in 24 "
    "countries, 267.000+ alumni, legal entity PT. ITSTEP ACADEMY IND - and INVITE them to "
    "verify in person ('kampusnya bisa Kakak datangi langsung kapan aja'). One warm line that "
    "doubt is normal ('wajar banget kok cek-cek dulu'). Then ONE light question to continue. "
    "Never invent registration numbers or certificates not in the KB. Return the JSON as "
    "usual.]"
)

SOFT_NO_NUDGE = (
    "[System: the lead just softly declined or stalled — a polite Indonesian 'not now' "
    "('nanti/pikir dulu/insyaallah/belum ada biaya/lain kali' or 'tanya keluarga dulu'), "
    "usually a real 'no' wrapped to save face, AND they've already had one objection-handling "
    "turn — so now ease off for real. Do NOT push price, DP, scarcity or a new "
    "pitch this turn — that makes them ghost. Acknowledge sincerely, give a graceful out, and "
    "offer AT MOST one low-commitment option (free Open House OR a cheap 1-day Skill Booster). "
    "If they haven't named WHEN they'd revisit, you may ask ONE light timing question - "
    "'mau aku ingetin lagi kapan enaknya - minggu depan, atau abis gajian? 😊' - a dated "
    "'later' becomes a real plan; a vague 'later' is a lost lead. If they DID name a time "
    "('bulan depan', 'abis gajian'), just confirm it warmly ('siap, aku kabari sekitar itu "
    "ya') - the system schedules the follow-up to their date automatically. "
    "Never repeat an offer you already made. Return the JSON as usual.]"
)

# COMBO: the same message stalls AND asks something real ('nanti dulu deh… eh tapi berapa
# harganya?'). Dropping either half loses: an unanswered question is the most expensive leak
# in the audit, and pushing past a soft-no is the biggest ghost-maker. So: answer, then ease.
SOFT_NO_WITH_QUESTION_NUDGE = (
    "[System: the lead softly declined ('nanti/pikir dulu/…') AND asked a real question in "
    "the same message. Answer the question first — short, honest, the concrete fact from the "
    "product card — because leaving it hanging is how leads are lost. Then ease off exactly as "
    "a soft-no deserves: no pitch, no DP, no scarcity, no counter-question about their goals; "
    "close warmly with a graceful out or 'boleh aku kabari kalau ada info baru?'. Return the "
    "JSON as usual.]"
)

ANSWER_FIRST_NUDGE = (
    "[System: the lead just asked a DIRECT question in their OWN words. ANSWER IT IN THIS "
    "REPLY, up front, with the concrete fact from the product card (price → the real number; "
    "schedule → the actual date; how to enrol → the real steps). Do NOT ask them to be more "
    "specific, and do NOT answer with a discovery question instead — a lead who asks and gets "
    "a counter-question leaves. Asking for their phone/WhatsApp is NOT an answer either: if "
    "they asked a price, give the real number (DP-first) FIRST — never swap the figure for a "
    "'boleh minta nomor WA' contact-grab (thread 4710). If the fact is genuinely NOT in the "
    "knowledge base, say so honestly in one line and set needs_manager=true — never invent it, "
    "never stall with a generic 'let me check' filler. After the answer you may add ONE short "
    "question. Return the JSON as usual.]"
)

# COMBO: a real question asked BY someone who just signalled no money ('ga ada modal, berapa
# biayanya?'). Answer-first alone would quote the full course price cold; budget-first alone
# would dodge the question. Do both: the honest number, with the affordable entry beside it.
ANSWER_FIRST_TIGHT_BUDGET_NUDGE = (
    "[System: the lead asked a DIRECT question AND signalled tight/no budget in the same "
    "message. Answer the question honestly with the real fact (never dodge a price question), "
    "but put the CHEAPEST real entry right beside it as the main path — the 1-day Skill "
    "Booster / mini course or the free Open House — so the answer doesn't read as 'this is "
    "not for you'. No DP push, never guarantee income. Return the JSON as usual.]"
)

NO_TIME_NUDGE = (
    "[System: the lead's objection is TIME — busy / 'nggak ada waktu' / 'sibuk' / 'waktunya "
    "padet' / 'nanti kalau sempat'. Do NOT capitulate ('kabari kalau ada waktu' leaves the "
    "sale on the table) and do NOT hard-push. WORK it: reframe the time cost using the ACTUAL "
    "schedule of the product in the knowledge context — name how SMALL the real weekly "
    "commitment is (the sessions-per-week and minutes from the card), that it can be taken "
    "ONLINE (no commute), and that the class day/time can be chosen to fit around work or "
    "study, exactly as the card states. Acknowledge warmly first ('paham banget Kak, jadwal "
    "padat itu nyata'), land that it fits a busy life, tie it to their goal if you know it, "
    "then ONE soft step (e.g. 'hari apa yang paling longgar buat Kakak?'), never a hard close. "
    "Use ONLY schedule facts present in the KB context — never invent a session count, an "
    "evening time, a class recording, or an income figure. Return the JSON as usual.]"
)

LOW_BUDGET_NUDGE = (
    "[System: the lead signaled tight or no budget (no money, unemployed, 'mahal banget', "
    "'gratisan', 'ga sanggup'). Do NOT lead with the full course price or a DP request. "
    "Acknowledge honestly, then offer the CHEAPEST real entry FIRST (1-day Skill Booster / "
    "mini course, or the free Open House) as the main path; mention the full program only as "
    "a 'later, once you've tried it' option. Never guarantee income or 'balik modal'. Return "
    "the JSON as usual.]"
)

# SPIN's need-payoff beat — the audits found discovery breaking EXACTLY where it starts
# working: the model catches the first pain and fires the price block on the very next reply,
# even when the pain IS the money ('kurangnya modal dan ragu' → 'total Rp 1.882.955').
NEED_PAYOFF_NUDGE = (
    "[System: you have captured the lead's PAIN but not yet the GAIN they want. Do NOT present "
    "the product, its features or its price this turn — the payoff has to land first, or the "
    "price arrives with nothing to weigh it against. Acknowledge the pain in one short line, "
    "then ask ONE question about the result they want ('kalau nanti Kakak udah bisa X, apa "
    "yang paling berubah buat Kakak?' / 'pengen hasil akhirnya kayak gimana?'), grounded in "
    "the pain they just named. Return the JSON as usual.]"
)

# Appended to the follow-up nudge when the lead has NEVER typed a word of their own — their
# only message is the ad button. The reply path already refuses to price a button click
# (AD_OPENER_NUDGE), but the FOLLOW-UP path had no such guard: thread 3926 got "Biaya total
# Rp 1.882.955 — DP 500.000…" as its first-ever follow-up, price dumped at a silent clicker.
FOLLOWUP_SILENT_CLICKER_EXTRA = (
    "\n[System addition: this lead has NEVER typed a word of their own — their only message "
    "is the ad's prefilled button text. The ad-opener rules STILL hold here: NO price, NO "
    "schedule, NO module dump (the button's '…dan biaya kursusnya' is the ad's wording, not "
    "theirs). They clicked an ad for a SPECIFIC skill — NAME that skill/topic as the hook "
    "('soal kelas <skill> yang Kakak lihat tadi …', topic only, never its price or schedule) "
    "so the message is obviously about what THEY looked at, not a generic blast. Then one "
    "light, concrete line about what that skill lets a person actually DO, then ONE easy "
    "question listing 3-4 NUMBERED options (career switch / own project / level up at work / "
    "mencari kursus buat anaknya / just curious), EACH option on its OWN line (line break "
    "before every number — never one crammed line, thread 4736) — numbers just for "
    "skimmability — but ask them to answer in their OWN WORDS, NOT with a bare digit (a '1 2' "
    "reply gets misread). Return the JSON as usual.]"
)

# Fake-serendipity opener — 'kebetulan nih baru aja ada alumni/project…', 'eh baru inget…'.
# The model reaches for it on every follow-up; it reads as spontaneous ONCE, then as a canned
# bot script (thread 1754 sent it twice, 17:23 and 21:31; the construction recurs across 6+
# chats verbatim). Detected so a repeat in the SAME thread gets a different opening.
FAKE_SERENDIPITY_RE = re.compile(
    r"\bkebetulan\b"
    r"|baru\s*aja\s*(?:ada|dapet|dapat|nemu|keinget|keingetan|kepikiran)"
    r"|eh\s*(?:iya\s*)?baru\s*(?:inget|keinget|keingetan|kepikiran)",
    re.IGNORECASE)

NO_REPEAT_SERENDIPITY_NUDGE = (
    "[System: you ALREADY opened an earlier follow-up in THIS chat with a fake-serendipity "
    "line ('kebetulan nih…', 'baru aja ada alumni/project…', 'eh baru inget…'). Using that "
    "construction a second time reads as a canned bot script — it only works once. Open THIS "
    "message a DIFFERENT, concrete way: a real fact tied to their stated goal, a specific next "
    "step, or a plain question — NEVER another 'kebetulan' / 'baru aja ada' / 'baru inget'. "
    "Return the JSON as usual.]"
)

# A designed 4-step escalation for follow-ups, keyed to the attempt number, instead of the
# model re-rolling a random "different angle" each time. A real salesperson warms, then proves,
# then lowers the barrier, then bows out gracefully — a ladder converts far better than four
# unrelated pitches (the FOLLOWUP_PRODUCT_DISCIPLINE 'catalogue read aloud' failure mode). The
# last rung is a soft close, NOT another pitch: past a point, more pushing only earns a report.
_FOLLOWUP_ANGLE_LADDER = (
    "\n[System addition — THIS is follow-up #{n}. Lead with this specific angle, don't just "
    "repeat a past one: ",
    # attempt 1 (n=1): re-hook on their own stated need
    "re-open on the lead's OWN stated goal/pain (or the ad's skill if they never spoke) — one "
    "warm, concrete line about what that skill lets them DO, then one easy question. Nothing "
    "salesy yet, just re-open the door.]",
    # attempt 2 (n=2): concrete proof
    "bring ONE concrete, KB-sourced proof — a real Success Case or a specific tangible outcome "
    "of this exact program — tied to their goal, to make it feel POSSIBLE for someone like "
    "them. One proof, not a feature list. Never invent a case or a number.]",
    # attempt 3 (n=3): lower the barrier. NO schedule example here — for a silent clicker the
    # ad-opener rules still forbid schedules, and a conflicting example made the model pick
    # arbitrarily (sales-logic audit 2026-07-19, #8).
    "LOWER the barrier: offer the cheapest real next step from the KB — a Skill Booster, the "
    "free weekly Open House, or a low-friction yes/no ('mau aku ceritain singkat gimana "
    "kelasnya jalan?'). The goal now is a tiny yes, NOT selling the full program.]",
    # attempt 4+ (n>=4): graceful soft close
    "this is likely the LAST touch — a graceful, no-pressure close. Acknowledge they may be "
    "busy, leave the door wide open ('kalau nanti mau lanjut, chat aku aja ya kapan pun'), one "
    "soft line. Do NOT hard-sell or re-list the offer — a warm exit is remembered better than "
    "a nag.]",
)


def followup_angle(attempt: int) -> str:
    """The attempt-specific angle line (attempt = followups_sent, 0-indexed). The 4th rung
    covers every attempt past the third."""
    rung = _FOLLOWUP_ANGLE_LADDER[min(attempt + 1, len(_FOLLOWUP_ANGLE_LADDER) - 1)]
    return _FOLLOWUP_ANGLE_LADDER[0].format(n=attempt + 1) + rung

# A follow-up is UNPROMPTED — nobody asked for it — so its length rule is stricter than the
# live-reply mirror and doesn't depend on the lead's last message (they're silent by
# definition). Measured 2026-07-16: follow-ups average 316 chars against live replies' 162 and
# outnumber them (593 vs 407 in 30h), i.e. the longest bot messages are the ones nobody asked
# for. That is exactly what gets an IG account reported — and leads did say so out loud
# ("Berisik Luh DM in gua mulu", "Jangan suka spam bangke", "Spam").
FOLLOWUP_BREVITY_SUFFIX = (
    "\n[System addition: this nudge is UNPROMPTED — the lead did not ask for it. Default to "
    "ONE bubble, 1-2 sentences: a long unprompted block reads as spam and gets accounts "
    "reported. Say ONE thing — one light question or one concrete fact, not a recap of the "
    "offer. Brevity never wins over being right, though: if the honest version of that one "
    "thing needs another line, take the line — cutting it into something misleading or "
    "half-true is far worse than being long.]"
)

# "Change the angle each attempt" (FOLLOWUP_NUDGE) reads to the model as "change the
# PRODUCT". Thread 2503: the lead typed "It" and went quiet, and the next four follow-ups
# pitched four different programs — Open House, then Vibe Coding, then Graphic Design, then
# Skill Booster. That is a catalogue being read aloud, not a salesperson working a lead.
FOLLOWUP_PRODUCT_DISCIPLINE = (
    "\n[System addition: a new ANGLE means a new reason to care — a different pain, proof, "
    "or an easier next step. It does NOT mean a different program. Stay on the program this "
    "thread is already about. Two exceptions: the lead said it doesn't fit, or price is the "
    "sticking point and you're offering a cheaper entry to the SAME goal. If you don't know "
    "which program they want, ask — never guess a new one each attempt.]"
)

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
BUYING_SIGNAL_RE = re.compile(
    r"\b(mau|ingin|pengen|pgn|siap|langsung)\s+(daftar|gabung|bergabung|ikut(an)?|join|"
    r"ambil\s+kelas)\b|\bdaftar(kan)?\s+(saya|aku|dong|sekarang)\b|\bberminat\s+daftar\b"
    r"|\bgas(s|keun|kan)?\b|\bgasken\b",
    re.IGNORECASE)

BUYING_SIGNAL_NUDGE = (
    "[System: the lead just said they WANT TO JOIN. Do NOT celebrate with a wall of text — "
    "a full dump of price + bank account + schedule at this exact moment scared off a real "
    "buyer (live: 'saya ingin bergabung' got an invoice-wall and replied 'kayanya saya "
    "gabisa deh'). Reply in 1-2 SHORT bubbles: confirm warmly, name the ONE next small step "
    "— the DP from the card that secures their seat — and ask for their WhatsApp number so "
    "the team can send the details and confirm. Bank account details only if they ask where "
    "to send the money. No new discovery questions this turn.]"
)

# The lead answered a numbered menu with a bare digit. Live loss (threads 4146/4058): '1'
# (switch career) got 'what's your biggest challenge?' — and thread 4058 got the same
# open question three times in a day, then silence. A menu answer is a CHOICE — convert it.
# Real leads decorate the digit: '2 kak', 'no 2', 'yang 2 min' (live 4531: '2 kak' fell
# through to an open discovery question and the model then RE-ASKED the very choice the
# lead had answered). Tolerate common prefixes/suffixes; anything else ('2 juta') still
# fails the match.
MENU_REPLY_RE = re.compile(
    r"^\s*(?:no\.?|nomor|yang|pilih)?\s*[1-4]\s*(️?⃣)?\s*(?:kak|ka|min|ya|dong|aja|deh)?\s*$",
    re.IGNORECASE)

MENU_REPLY_NUDGE = (
    "[System: the lead just tapped a numbered-menu choice — a warm signal, but they have NOT "
    "been given any value yet, so they're still cold. Two hard don'ts this turn: do NOT ask "
    "for their WhatsApp/phone, and do NOT dump the price (live: a menu tap answered with "
    "'boleh minta nomor WhatsApp?' made the lead leave, thread 4615). Do NOT re-interrogate "
    "with a generic 'apa tujuan Kakak' either. Instead, in 1-2 short bubbles: (1) acknowledge "
    "their chosen goal in THEIR words; (2) tie ONE concrete, real outcome from the KB to that "
    "exact goal — what they'll be able to DO; (3) ask ONE sharp, SPECIFIC question that turns "
    "the goal into a real need ('buat bidang/produk apa?', 'udah pernah nyoba sebelumnya?', "
    "'targetnya kerja di mana / bisnis apa?'). Earn the pull first — contact and price come "
    "later, once they're actually invested.]"
)

# A share of OUR OWN Instagram post. The generic unseen-media reply ('maaf, kontennya
# tidak bisa dibuka') makes the bot look broken to a lead who just tapped OUR ad/post —
# seen in 5+ threads (4243/4274/4252/1420/4214). The share IS the interest signal.
# The lead shared/replied to one of OUR OWN IG posts or ads. Two shapes: a bare share
# ("📷 itstep_jakarta") OR a shared AD with its full caption copy ("📷 itstep_jakarta ·
# itstep_jakarta Masih scroll tapi belum menghasilkan? … upgrade skill di Regular Program
# SMM …"). Both are a CLICK on our content, NOT the lead's own words — so the ad-copy caption
# must not count as "the lead spoke" or the price/pitch leaks on turn one (bench 4045/3917/2802:
# the shared SMM ad got the full Rp 1.882.955 immediately). Prefix match (no $) catches the
# caption-with-copy; requiring 'itstep' keeps a genuine third-party share out of scope.
OWN_POST_SHARE_RE = re.compile(r"^[📷🎬📖🎥🎞]\s*\S*itstep", re.IGNORECASE)

OWN_POST_NUDGE = (
    "[System: the lead shared one of OUR OWN Instagram posts — that is interest in that "
    "content, NOT unreadable media. Never apologize about being unable to open it. Greet "
    "warmly, say you saw they're checking out our post, and ask what caught their eye — "
    "a short numbered list of our program areas is a good way to let them answer with one "
    "tap. No prices yet (they haven't said a word of their own).]"
)

# The lead's message packs SEVERAL questions — the most engaged leads do this, and they
# were the ones hitting a half-answer or a clarify stub (thread 2533: 'what's taught? what
# background? job guarantee?' got 'be more specific'). Appended ON TOP of the chosen nudge,
# like the format mirror — orthogonal to the situation.
MULTI_QUESTION_SUFFIX = (
    "\n[System: the lead's message asks SEVERAL things at once. Answer EVERY part in this "
    "reply, each on its own short line, in their order; if some part has no fact in the KB, "
    "say so honestly for THAT part instead of dropping it. Never answer only the first "
    "question — a half-answered lead has to chase the rest and usually doesn't.]"
)

# Asking the lead to hand over their contact number. Legit ONCE the lead is warm (asked a
# price, said 'mau daftar', gave a buying signal, or a pain is on record); premature on a cold
# lead — grabbing contact before delivering value reads as a lead-capture bot and cold leads
# bail (thread 4615: a menu tap got 'boleh minta nomor WhatsApp?' and the lead left).
# 'minta nomor' = the BOT requesting the lead's number (always a contact grab). But 'kirim/
# share nomor' is instructing the LEAD to send a number — in the numbered-menu / clarify copy
# that's 'kirim nomornya aja' = send the MENU choice, NOT a phone; so the send-verbs require an
# explicit WA/phone word, while the request-verbs don't.
CONTACT_ASK_RE = re.compile(
    r"minta\s+(?:nomor\w*|no\.?\s*(?:wa|hp))"
    r"|boleh\s+(?:minta|tau|dapat)\s+(?:nomor\w*|kontak\w*)"
    r"|(?:share|kirim|kasih|bagi)\s+(?:nomor|no\.?)\s*(?:wa\b|whatsapp|hp\b|telp)",
    re.IGNORECASE)

PREMATURE_CONTACT_CORRECTION = (
    "[System: you asked the lead for their WhatsApp/phone, but they're still COLD — no pain "
    "surfaced, no price/payment question, no 'I want to join' signal. Grabbing contact now "
    "reads as a lead-capture bot and cold leads bail (thread 4615). Do NOT ask for a number "
    "this turn. Instead deliver ONE concrete value tied to their stated goal, then ONE sharp "
    "question that deepens it into a real need. Ask for contact only later, once they're "
    "pulled in (asked price, said mau daftar, or a clear buying signal). Return the JSON.]"
)


def premature_contact_ask(reply, last_inbound, *, has_pains, has_phone, ready) -> bool:  # noqa: ANN001
    """True when the reply asks for the lead's number while they're still cold — no pain on
    record, no phone, not ready, and this turn carries no price/pay/buying signal."""
    if not CONTACT_ASK_RE.search(reply or ""):
        return False
    if has_pains or has_phone or ready:
        return False
    txt = last_inbound or ""
    warm = (PRICE_QUESTION_RE.search(txt) or PAYMENT_INTENT_RE.search(txt)
            or BUYING_SIGNAL_RE.search(txt))
    return not warm


PAYMENT_INTENT_NUDGE = (
    "[System: the lead is asking HOW or WHERE to pay — that is a BUYING signal; this turn "
    "closes the deal. Do NOT pitch, do NOT ask a discovery question, and NEVER answer with a "
    "bare 'give me your WhatsApp'. In this reply: confirm the product and the exact amount "
    "(the DP first if the card has one), give the real payment options from the payment "
    "policy (bank transfer and QRIS), ask for their WhatsApp number so the team can confirm "
    "the payment, and say what happens right after they pay. Facts only from the KB.]"
)

ANSWER_PRICE_NO_PAIN_NUDGE = (
    "[System: the lead is asking about the price and you have NOT learned a single pain or "
    "goal from them yet. ANSWER HONESTLY — they asked, never dodge — but do NOT ship a bare "
    "total: measured over 100 live chats, 71% of leads who got a naked number never wrote "
    "again. Frame it instead:\n"
    "1) Lead with the SMALLEST real step from the card — the DP that secures a seat — not the "
    "full figure.\n"
    "2) Put the easy-payment facts right beside it (interest-free instalments, QRIS/transfer) "
    "— only what the card actually says.\n"
    "3) Keep the full total present and honest, but as context, not the headline.\n"
    "4) Close with ONE short question that opens WHY they're asking — what they want the "
    "skill for, or what isn't working now — so the number has something to stand against.\n"
    "Facts strictly from the card. NEVER invent a discount, a deadline, a seat count or any "
    "urgency that isn't written there.]"
)

# The gap the funnel dies in: a warm lead whose pain AND gain we already hold, who isn't
# asking anything and hasn't shouted "I'm in" — no nudge fired, so the model freewheeled into
# a passive pitch and asked no contact. Measured: 71% ghost after a price, most with NO contact
# left behind, and only 2% of engaged leads ever advance. Once discovery is DONE, stop digging
# and MOVE: present against their own words, close assumptively (take the step yourself, not a
# yes/no), and capture the WhatsApp so a lead who then goes quiet is still reachable by a human.
PRESENT_AND_CLOSE_NUDGE = (
    "[System: you already hold this lead's real PAIN and the GAIN they want — discovery is "
    "DONE. Stop asking questions and MOVE TO CLOSE this turn:\n"
    "1) Present the ONE best-fit product against exactly THEIR pain and gain, in their words — "
    "value first, not a feature dump. If you name the price, lead with the smallest step (the "
    "DP that secures a seat) + instalments/QRIS, full amount only as context. If the lead just "
    "ASKED the price ('berapa/brp/biaya'), you MUST state the actual number (DP-first) IN THIS "
    "reply — a bare 'boleh minta nomor WhatsApp?' is NOT an answer, and swapping the price for a "
    "contact-grab is how a hot lead is lost (thread 4710: asked 'brp', got a phone request then "
    "a hand-off, never saw a number). Ask for the number AFTER the price, not instead of it.\n"
    "2) Take the next step YOURSELF — an assumptive trial close ('aku bantu amankan seat Kakak "
    "buat batch depan ya?'), NEVER a passive 'gimana, tertarik?' that invites a no.\n"
    "3) If you don't have their WhatsApp yet, ASK for the number tied to a concrete reason so a "
    "warm lead who goes quiet stays reachable ('boleh minta nomor WA Kakak biar aku amankan "
    "seat-nya?'). You're on Instagram and CANNOT send to WA - never offer to 'kirim ... ke WA', "
    "just ask for the number. A number given keeps ready=false and the bot stays on.\n"
    "One clear next step, not five. Facts only from the KB.]"
)

DISCOVER_BEFORE_PRICE_NUDGE = (
    "[System: this lead is engaged but hasn't told you a single pain or goal yet, and they did "
    "NOT ask for the price. Do NOT quote the fee, the monthly figure, or the total now — a price "
    "dropped before there's a reason to pay it just becomes a number they balk at. Ask ONE warm, "
    "specific question about what they want to achieve or what's getting in their way, so the "
    "price later lands against something worth paying for. (If they DO ask the price outright, "
    "answer it — this only applies while they haven't.)]"
)

# A follow-up that names the lead's OWN earlier words ("dulu Kakak sempat bilang soal <need>")
# re-engages far better than a generic "masih tertarik?" — it proves someone listened. The
# need text is the lead's own captured phrase, so quoting it fabricates nothing.
FOLLOWUP_NEED_ANCHOR = (
    "\n[System addition: this lead already told you what they care about: \"{need}\". Re-open "
    "by referencing THAT in their own terms — remind them of the outcome they wanted and tie "
    "this nudge to it, instead of a generic 'still interested?'. Don't quote it robotically; "
    "weave it in like a person who remembers the conversation.]"
)

DISCOVERY_CAP_NUDGE = (
    "[System: you have already asked discovery questions for {n} turns without the lead "
    "voicing a clear need — do NOT ask another discovery question this turn. If they asked "
    "something directly, answer it now with the fact from the product card. Otherwise "
    "present the best-fit product: ONE concrete value line tied to what they've told you, "
    "then a light next step. Lead with the full price/DP breakdown ONLY if they explicitly "
    "asked about price/payment or signaled they want to enroll — a lead asking how to solve "
    "their problem (not how much it costs) gets a value answer, not a price dump; save the "
    "full price for when they ask or the conversation clearly calls for it. Return the JSON "
    "as usual.]"
)


# Below this the lead is chatting in one-liners, not writing — mirror them. Measured on 7 days
# of live branch-1 traffic (2026-07-15): leads average 49 chars, p90 62. 100 covers ~all of
# them while leaving a lead who wrote a real paragraph free to get a fuller answer back.
_ONE_LINER_CHARS = 100

# The bot is NOT emoji-dry — it out-emojis both leads and human managers (0.55 vs 0.60 vs 0.51
# per message; 47% vs 42% vs 29% of messages). What it actually gets wrong is VOLUME and shape:
# 636 chars across 2.4 bubbles per turn against the lead's 49 (a 13× gap, up to 7 bubbles), and
# only 8% of its messages carry a line break vs 18% for a human manager — i.e. a wall of text
# where the market chats in short lines. "Keep it short" already exists in the 26k prompt and
# loses there, so anchor it to the lead's ACTUAL message length at this turn. Appended to
# whatever situational nudge fired (formatting is orthogonal to the situation), never replacing
# one — and never applied to AD_OPENER_NUDGE, whose 3-bubble numbered opener is deliberate.
FORMAT_MIRROR_SUFFIX = (
    "\n[System: the lead's message was {n} characters — they chat in one-liners, not "
    "paragraphs. Mirror that register: 1-2 SHORT bubbles, no wall of text (this is an "
    "Instagram DM, not a brochure — a 400-char block reads as a leaflet and gets skimmed or "
    "ignored). If you must state 2+ facts, put each on its OWN line so it's skimmable. Emoji "
    "as you already do — natural, not decorative. One question max. This is about register, "
    "NOT about withholding: if they asked something, answer it fully and accurately even if "
    "that runs long — a short wrong answer is worse than a long right one.]"
)


# A bare amount in Indonesian money shorthand. Live miss (thread 4045, confirmed in sim): the
# lead answered "4jta" (= 4 juta rupiah, their income goal) right after the bot asked "kerja
# atau masih sekolah?" — and the model read it as "4 TAHUN kerja" (4 years of work), derailing
# the whole discovery. The number's meaning is deterministic (jt/juta=millions, rb/ribu=k), so
# spell it out instead of trusting the model to parse slang under a priming question.
AMOUNT_SHORTHAND_RE = re.compile(
    r"^\s*(?:rp\.?\s*)?\d+(?:[.,]\d+)?\s*(?:jt|jta|juta|rb|ribu)\b\s*[.!]?\s*$",
    re.IGNORECASE)

AMOUNT_HINT_SUFFIX = (
    "\n[System: the lead's message '{txt}' is an AMOUNT OF MONEY in Indonesian shorthand "
    "(jt/jta/juta = millions of rupiah, rb/ribu = thousands). In context it is most likely "
    "their income goal or their budget. It is NOT years of experience, NOT an age, NOT a "
    "duration — never read a jt/rb number as anything but money.]"
)


def format_suffix(last_txt: str, nudge: str | None) -> str:
    """The length-mirror instruction for this turn, or '' when it doesn't apply."""
    if nudge is AD_OPENER_NUDGE:
        return ""  # the numbered opener is meant to be 2-3 bubbles — don't fight it
    n = len((last_txt or "").strip())
    if not n or n > _ONE_LINER_CHARS:
        return ""  # they wrote a real paragraph — a fuller answer is fair
    return FORMAT_MIRROR_SUFFIX.format(n=n)


def with_situation(correction: str, situational: str | None) -> str:
    """Re-attach the turn's nudge to a correction. Every regen re-answers the SAME turn, so a
    correction that travels alone silently un-does the situational layer at the worst possible
    moment — see guard_decision's docstring for the live case (thread 4092)."""
    return f"{correction}\n{situational}" if situational else correction


def _bot_offered_menu(dialog) -> bool:  # noqa: ANN001
    """A recent BOT message contained a numbered menu — a bare '2' from the lead is only a
    menu answer if there was a menu to answer. Scans the last few bot messages, not just the
    newest: a follow-up nudge sent between the menu and the lead's '1' used to hide the menu
    and the choice fell to another open discovery question (thread 4058's exact failure)."""
    seen_out = 0
    for m in reversed(dialog):
        if m.direction == "out":
            if "1️⃣" in (m.text or ""):
                return True
            seen_out += 1
            if seen_out >= 3:
                return False
    return False


def _current_turn_text(dialog) -> str:  # noqa: ANN001
    """Everything the lead typed in their CURRENT turn (every inbound since our last send),
    joined. An objection detector that reads only the LAST message misses a soft-no/doubt
    chased with a trailing filler — thread 4573: 'Nanti aja lagi galau gue' then 'Maaf yaaaa'
    left last_txt='Maaf yaaaa' (no pattern), so the model pitched Vibe Coding over the no."""
    parts: list[str] = []
    for m in reversed(dialog):
        if m.direction == "out":
            break
        if m.direction == "in":
            parts.append(m.text or "")
    parts.reverse()
    return " ".join(parts)


def pick_nudge(*, lead_type, dialog, last_txt, stored_needs, inbound_count) -> str | None:  # noqa: ANN001
    """The steering block for this turn: ONE situational nudge (priority chain below) plus the
    length-mirror suffix when the lead is chatting in one-liners, plus the answer-every-part
    suffix when their message packs several questions. Returns None only when none apply."""
    nudge = _pick_situation(
        lead_type=lead_type, dialog=dialog, last_txt=last_txt,
        stored_needs=stored_needs, inbound_count=inbound_count)
    suffix = format_suffix(last_txt, nudge)
    if (last_txt or "").count("?") >= 2:
        suffix += MULTI_QUESTION_SUFFIX
    if AMOUNT_SHORTHAND_RE.match(last_txt or ""):
        suffix += AMOUNT_HINT_SUFFIX.format(txt=(last_txt or "").strip())
    if not suffix:
        return nudge
    return (nudge + suffix) if nudge else suffix.lstrip("\n")


def _pick_situation(*, lead_type, dialog, last_txt, stored_needs, inbound_count) -> str | None:  # noqa: ANN001
    """The ONE situational nudge for this turn, or None — the whole priority chain in one
    place so rule conflicts are resolved here, deliberately, instead of by accident of
    ordering scattered through reply.py.

    Order rationale: non-target and the ad-opener describe the CONVERSATION (they preempt
    everything); unseen-media means we can't even trust the text we're reacting to; minor
    changes who we're selling to; soft-no / answer-first / budget react to the current
    message (with combos where they collide); need-payoff and the discovery cap steer the
    funnel and go last. need-payoff respects DISCOVERY_TURN_CAP — past the cap the stage gate
    has already released the lead, and holding the pitch hostage to a gain question forever
    contradicts it (a non-yielding lead gets the value pitch, not a fifth question)."""
    if lead_type == "non_target":
        # the model already classified this lead non_target on a PRIOR turn — wrap up.
        return NON_TARGET_NUDGE
    if not lead_spoke_own_words(dialog):
        return AD_OPENER_NUDGE
    if unseen_media_in_turn(dialog):
        if OWN_POST_SHARE_RE.match(last_txt.strip()):
            return OWN_POST_NUDGE  # our own post shared back = interest, not broken media
        return UNSEEN_MEDIA_NUDGE  # can't read their turn — nothing else can be trusted
    # Checkout beats the audience classifier: a parent writing 'mau daftar anak saya,
    # transfer ke mana?' is the PAYER at the checkout — steering them to Open House because
    # 'anak' matched MINOR_RE loses the close (sales-logic audit 2026-07-19, top-2).
    if PAYMENT_INTENT_RE.search(last_txt) and not AD_TEMPLATE_RE.search(last_txt):
        return PAYMENT_INTENT_NUDGE  # a buyer at the checkout outranks every other signal
    if BUYING_SIGNAL_RE.search(last_txt) and not AD_TEMPLATE_RE.search(last_txt):
        return BUYING_SIGNAL_NUDGE  # 'I want in' — one small step, never an invoice-wall
    if MINOR_RE.search(last_txt):
        return MINOR_NUDGE
    asks = is_answerable_question(last_txt) and not AD_TEMPLATE_RE.search(last_txt)
    # Objections are sticky across the turn: when the lead's LAST message isn't itself a fresh
    # question (they'd want that answered), scan the WHOLE current turn so a soft-no/doubt
    # trailed by filler still gets handled, not pitched over (thread 4573). A last-message
    # question still routes to answer-first below, exactly as before.
    turn_txt = last_txt if asks else _current_turn_text(dialog)
    # A legitimacy doubt outranks soft-no/answer-first: the doubt IS the objection, and the
    # canned facts answer beats anything else this turn (thread 4435: 'Apakah ini real' → menu).
    if TRUST_DOUBT_RE.search(turn_txt):
        return TRUST_DOUBT_NUDGE
    # TIME is a specific objection ('sibuk'/'nggak ada waktu') that hides inside SOFT_NO and
    # capitulates there — give it the grounded schedule reframe FIRST; on a repeat, ease off.
    if NO_TIME_RE.search(turn_txt):
        time_obj_count = sum(
            1 for m in dialog if m.direction == "in" and NO_TIME_RE.search(m.text or ""))
        if time_obj_count <= 1:
            return NO_TIME_NUDGE
        return SOFT_NO_WITH_QUESTION_NUDGE if asks else SOFT_NO_NUDGE
    # A BARE decision-postpone ('nanti aja', 'mikir dulu') — no money/time/trust reason named —
    # gets the cost-of-waiting + low-friction move, NOT the generic 'isolate the doubt'. Checked
    # before SOFT_NO so a bare postpone lands here; a repeat eases off (pushing opportunity-cost
    # twice reads as nagging).
    if POSTPONE_RE.search(last_txt) and not asks:
        # trigger on the LAST message being a bare postpone (not the whole-turn scan) and NOT
        # when they also asked something — a postpone + question ('nanti dulu, tapi berapa?') or
        # a postpone with an emotional reason trailing wants answer-first / gentle handling, so
        # let those fall through to SOFT_NO. Ease off if they've already objected once (any form).
        prior_obj = sum(
            1 for m in dialog if m.direction == "in"
            and (SOFT_NO_RE.search(m.text or "") or POSTPONE_RE.search(m.text or "")))
        if prior_obj <= 1:
            return POSTPONE_NUDGE
        return SOFT_NO_NUDGE
    if SOFT_NO_RE.search(turn_txt):
        # First objection this conversation → work it once; on a repeat, ease off (existing).
        soft_no_count = sum(
            1 for m in dialog if m.direction == "in" and SOFT_NO_RE.search(m.text or ""))
        if soft_no_count <= 1:
            return OBJECTION_HANDLE_NUDGE
        return SOFT_NO_WITH_QUESTION_NUDGE if asks else SOFT_NO_NUDGE
    if asks:
        if LOW_BUDGET_RE.search(last_txt):
            return ANSWER_FIRST_TIGHT_BUDGET_NUDGE
        # A price question from someone whose pain we still don't know is where the funnel
        # actually dies (71% never reply to a bare number) — answer it, but framed.
        if PRICE_QUESTION_RE.search(last_txt) and not stored_needs.pains:
            return ANSWER_PRICE_NO_PAIN_NUDGE
        return ANSWER_FIRST_NUDGE
    if LOW_BUDGET_RE.search(turn_txt):
        return LOW_BUDGET_NUDGE
    if MENU_REPLY_RE.match(last_txt) and _bot_offered_menu(dialog):
        return MENU_REPLY_NUDGE  # a menu answer converts to value+step, not more questions
    # `<` aligns with reply.py's stage gate (`inbound_count < _DISCOVERY_TURN_CAP`): at the
    # cap the funnel already allows PRESENTING, so the nudge must release the same turn —
    # `<=` held the pitch hostage one extra turn (sales-logic audit 2026-07-19, #5).
    if stored_needs.pains and not stored_needs.gains and inbound_count < DISCOVERY_TURN_CAP:
        return NEED_PAYOFF_NUDGE
    if stored_needs.captured():
        # pain AND gain in hand → discovery is done: present, close assumptively, capture WA
        return PRESENT_AND_CLOSE_NUDGE
    if not stored_needs.pains and inbound_count < DISCOVERY_TURN_CAP:
        return DISCOVER_BEFORE_PRICE_NUDGE
    if not stored_needs.captured() and inbound_count >= DISCOVERY_TURN_CAP:
        return DISCOVERY_CAP_NUDGE.format(n=inbound_count)
    return None
