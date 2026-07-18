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

# The click-to-message ad prefill families — a button click, not the lead's own words. Two
# canned openers seen at scale (2026-07: 609 threads on the second family alone): "💻
# Ceritakan lebih detail tentang program …" and "Halo, saya ingin tahu detail program X dan
# biaya kursusnya 😊". An emoji prefix is tolerated. Nothing here may ever be treated as the
# lead speaking: not for needs, not for answer-first, not for a price.
AD_TEMPLATE_RE = re.compile(
    r"^[^a-zA-Z]*(ceritakan lebih detail tentang program"
    r"|(halo[\s,]*)?(saya |aku )?ingin tahu detail program)",
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
    r"cicilan|daftar|syarat|sertif|bnsp|jadwal|lokasi|durasi)\b"
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
    r"mikir(in|kan)?\s*(dulu|lagi)|"
    r"nabung\s*dulu|belum\s*(ada|punya|siap|kepikiran)|lain\s*kali|next\s*time|nex\s*(aja|kk)|"
    # A polite "not interested (yet)" is the most common face-saving refusal and was missed
    # entirely (thread 2949: "maaf belum tertarik" got a discovery question + a follow-up an
    # hour later, straight over the no, instead of the soft-no easing-off and objection snooze).
    r"(belum|blm|ga|gak|nggak|ngga|ndak|tidak|tdk)\s*(tertarik|tertarikan|minat|berminat)|"
    r"insya\s*allah|liat\s*(nanti|dulu)|kapan[- ]?kapan|(?:nggak|ngga|ndak|gak|ga|gk)\s*dulu|"
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
    r"(te)?rasa\s+berat|masih\s+berat|keberatan\b",
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
# and make answering cost one tap.
AD_OPENER_NUDGE = (
    "[System: the lead's ONLY message so far is the ad's prefilled opener (a BUTTON CLICK, not "
    "their own words) — they tapped an ad, nothing more. This single reply decides the whole "
    "conversation: most ad clicks never write back, so it must feel like a warm human opening a "
    "chat, not a form. Write it as 2-3 SHORT Instagram-DM bubbles separated by '|||', in "
    "friendly everyday Bahasa with a few natural emoji, in this shape:\n"
    "1) Greet warmly + say who you are (MinStep dari IT STEP Academy Jakarta, kampus di Menara "
    "Sudirman) — that quietly answers 'is this real?'. Use their name ONLY if it looks like a "
    "real name, never a raw username like 'MENNN08'.\n"
    "2) ONE short hook: why this topic is worth their time, in THEIR world — what it lets a "
    "person actually DO. One or two lines, concrete, no hype. This is what they clicked for, so "
    "do not leave them empty-handed.\n"
    "3) ONE easy question with 4 NUMBERED options (1️⃣ 2️⃣ 3️⃣ 4️⃣) covering the usual reasons "
    "people come — switch career / build their own thing or level up at work / mencari kursus "
    "buat anaknya / just curious — and tell them to simply send the number. ALWAYS include the "
    "for-my-child option: parents shopping for a kid are a real segment with their own "
    "programs, and knowing it on turn one sets the whole sell path (the parent decides and "
    "pays). Tapping a number is effortless; composing a sentence about their goals is not — "
    "that gap is where these leads are lost.\n"
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

SOFT_NO_NUDGE = (
    "[System: the lead just softly declined or stalled — a polite Indonesian 'not now' "
    "('nanti/pikir dulu/insyaallah/belum ada biaya/lain kali' or 'tanya keluarga dulu'), "
    "usually a real 'no' wrapped to save face. Do NOT push price, DP, scarcity or a new "
    "pitch this turn — that makes them ghost. Acknowledge sincerely, give a graceful out, and "
    "offer AT MOST one low-commitment option (free Open House OR a cheap 1-day Skill Booster) "
    "or just ask permission to follow up later ('boleh aku kabari kalau ada info baru?'). "
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
    "a counter-question leaves. If the fact is genuinely NOT in the knowledge base, say so "
    "honestly in one line and set needs_manager=true — never invent it, never stall with a "
    "generic 'let me check' filler. After the answer you may add ONE short question. Return "
    "the JSON as usual.]"
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
    "theirs). Re-open warmly instead: one light, concrete hook about what this skill lets a "
    "person actually do, then ONE effortless question with 3-4 NUMBERED options (career "
    "switch / own project / level up at work / mencari kursus buat anaknya / just curious) "
    "and 'cukup balas angkanya'. Return the JSON as usual.]"
)

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
    r"\b(berapa|harga|biaya|tarif|cicilan|angsuran|murah|mahal|gratis|bayar|berbayar|modal)\b",
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
BUYING_SIGNAL_RE = re.compile(
    r"\b(mau|ingin|pengen|pgn|siap|langsung)\s+(daftar|gabung|bergabung|ikut(an)?|join|"
    r"ambil\s+kelas)\b|\bdaftar(kan)?\s+(saya|aku|dong|sekarang)\b|\bberminat\s+daftar\b",
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
MENU_REPLY_RE = re.compile(r"^\s*[1-4]\s*(️?⃣)?\s*$")

MENU_REPLY_NUDGE = (
    "[System: the lead just answered your numbered menu with a choice — that is a "
    "conversion moment, not an invitation to interrogate. Do NOT reply with another open "
    "discovery question (live: '1' was answered with 'apa tantangan terbesar?' three times "
    "and the lead went silent). Give ONE concrete value line matched to their chosen goal, "
    "from the KB, then ONE light next step — e.g. offer to send the syllabus/schedule via "
    "WhatsApp, or the real upcoming event from the KB. At most one short question, and only "
    "if it moves them toward that step.]"
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
    "DP that secures a seat) + instalments/QRIS, full amount only as context.\n"
    "2) Take the next step YOURSELF — an assumptive trial close ('aku bantu amankan seat Kakak "
    "buat batch depan ya?'), NEVER a passive 'gimana, tertarik?' that invites a no.\n"
    "3) If you don't have their WhatsApp yet, offer to send the full details there so a warm "
    "lead who goes quiet stays reachable ('boleh aku kirim rincian lengkap ke WA Kakak?'). A "
    "WA shared just to receive details keeps ready=false and the bot stays on.\n"
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
    """The most recent BOT message actually contained a numbered menu — a bare '2' from the
    lead is only a menu answer if there was a menu to answer."""
    for m in reversed(dialog):
        if m.direction == "out":
            return "1️⃣" in (m.text or "")
    return False


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
    if MINOR_RE.search(last_txt):
        return MINOR_NUDGE
    if PAYMENT_INTENT_RE.search(last_txt) and not AD_TEMPLATE_RE.search(last_txt):
        return PAYMENT_INTENT_NUDGE  # a buyer at the checkout outranks every other signal
    if BUYING_SIGNAL_RE.search(last_txt) and not AD_TEMPLATE_RE.search(last_txt):
        return BUYING_SIGNAL_NUDGE  # 'I want in' — one small step, never an invoice-wall
    asks = is_answerable_question(last_txt) and not AD_TEMPLATE_RE.search(last_txt)
    if SOFT_NO_RE.search(last_txt):
        return SOFT_NO_WITH_QUESTION_NUDGE if asks else SOFT_NO_NUDGE
    if asks:
        if LOW_BUDGET_RE.search(last_txt):
            return ANSWER_FIRST_TIGHT_BUDGET_NUDGE
        # A price question from someone whose pain we still don't know is where the funnel
        # actually dies (71% never reply to a bare number) — answer it, but framed.
        if PRICE_QUESTION_RE.search(last_txt) and not stored_needs.pains:
            return ANSWER_PRICE_NO_PAIN_NUDGE
        return ANSWER_FIRST_NUDGE
    if LOW_BUDGET_RE.search(last_txt):
        return LOW_BUDGET_NUDGE
    if MENU_REPLY_RE.match(last_txt) and _bot_offered_menu(dialog):
        return MENU_REPLY_NUDGE  # a menu answer converts to value+step, not more questions
    if stored_needs.pains and not stored_needs.gains and inbound_count <= DISCOVERY_TURN_CAP:
        return NEED_PAYOFF_NUDGE
    if stored_needs.captured():
        # pain AND gain in hand → discovery is done: present, close assumptively, capture WA
        return PRESENT_AND_CLOSE_NUDGE
    if not stored_needs.pains and inbound_count <= DISCOVERY_TURN_CAP:
        return DISCOVER_BEFORE_PRICE_NUDGE
    if not stored_needs.captured() and inbound_count > DISCOVERY_TURN_CAP:
        return DISCOVERY_CAP_NUDGE.format(n=inbound_count)
    return None
