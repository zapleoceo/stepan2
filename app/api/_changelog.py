"""Public 'What's New' page — the customer-facing changelog + project version.

The single source of truth for the Stepan project version and its public release notes.
RULE (enforced by tests/test_changelog.py): every meaningful, buyer-relevant change bumps
PROJECT_VERSION and adds a matching RELEASES entry at the top. Keep entries about what a
BUYER cares about — new capabilities, reliability, reach — never internal refactors.
Served at /whats-new, linked from the landing. Self-contained HTML (own inline CSS)."""
# ruff: noqa: E501 — inline CSS/HTML string; long lines are inherent
from __future__ import annotations

import html as _h

from app.config import settings


def _whatsnew_seo() -> str:
    base = (settings().public_url or "https://stepan2.zapleo.com").rstrip("/")
    return (
        f'<link rel="canonical" href="{base}/whats-new">'
        '<meta name="robots" content="index,follow,max-image-preview:large">'
        '<meta property="og:type" content="website">'
        '<meta property="og:site_name" content="Stepan">'
        '<meta property="og:title" content="What\'s New — Stepan">'
        f'<meta property="og:url" content="{base}/whats-new">'
        f'<meta property="og:image" content="{base}/og.svg">'
        '<meta name="twitter:card" content="summary_large_image">'
    )


# Bump this together with a new RELEASES[0] entry (tests keep them in sync).
PROJECT_VERSION = "1.17.0"

# A short teaser for the big thing currently rolling out — shown as a highlighted card above
# the shipped history. Set to None when there's nothing meaningful in flight.
COMING_NEXT = {
    "title": "Two-way CRM sync",
    "blurb": "Stepan already writes every qualified lead and stage change into your CRM. "
             "Next it reads back from it too, so a deal your team advances by hand stays "
             "perfectly in step with the conversation.",
}

# Newest first. Each: version, date (DD Mon YYYY), tag (one word), title, blurb (buyer-facing).
RELEASES = [
    {
        "version": "1.17.0", "date": "17 Jul 2026", "tag": "Integration",
        "title": "Plugs into your CRM — and into your team’s AI assistant",
        "blurb": "Stepan now speaks MCP — the open standard AI assistants use to plug into "
                 "other systems — and the wire runs both ways. Your team can work him from "
                 "Claude or any MCP-capable assistant in plain language: who is this lead, "
                 "show me the whole conversation, grade this chat against our knowledge base, "
                 "this one bought — close it, we couldn’t reach them by phone — pick it back "
                 "up in chat. Access is per person and revocable in a click: a reviewer gets "
                 "a key that can only ever read, and any key can be pinned to a single "
                 "branch, so nobody sees or moves what they shouldn’t. On the other side of "
                 "the wire Stepan now connects out to a CRM that speaks MCP — we are live "
                 "against one, working from its real client cards, calls and contracts "
                 "instead of a nightly export.",
    },
    {
        "version": "1.16.0", "date": "16 Jul 2026", "tag": "Selling",
        "title": "A polite no is a later, not a dead end",
        "blurb": "When a client says ‘not now’ — next time, let me think about it, "
                 "maybe next month — Stepan used to close the file on the spot: the funnel "
                 "let him mark a lead dead the moment they hesitated, and dozens never heard "
                 "from us again. Those are the cheapest leads you have; they are already warm "
                 "and they did not actually refuse. He now keeps them as an open objection and "
                 "checks back exactly once, about five days later, instead of either "
                 "forgetting them or firing off four reminders at someone who just said no. "
                 "A clear ‘stop contacting me’ still ends it immediately — that "
                 "line is not a maybe.",
    },
    {
        "version": "1.15.0", "date": "16 Jul 2026", "tag": "Conversation",
        "title": "Answers the question in front of it",
        "blurb": "Three ways a good conversation used to stall, closed. A client who asked a "
                 "plain question — how much, which days, how do I sign up — and happened to "
                 "have no number on file could get the same 'send me your WhatsApp' back "
                 "every time instead of an answer; now the question gets answered from the "
                 "catalogue first, and the number is asked for alongside, not in place of it. "
                 "A client who only says how they'd like to study — online, from home — no "
                 "longer gets the full price dropped on them before anyone has asked what "
                 "they're actually after; the course price waits until it has something to "
                 "stand against. And an offer to send a brochure over WhatsApp, which the bot "
                 "can't actually do, is caught the same way its other impossible promises "
                 "already were.",
    },
    {
        "version": "1.14.0", "date": "16 Jul 2026", "tag": "Conversation",
        "title": "The rules that cost you money are no longer optional",
        "blurb": "A client who has only tapped your ad and not said a word yet should never "
                 "be answered with a price — it's the fastest way to lose them. Stepan was "
                 "asked not to, and mostly obliged, but 'mostly' isn't a rule: an hour after "
                 "a textbook opener, a follow-up would sometimes lead with the full figure. "
                 "So the ban stopped being a request and became a check he cannot get "
                 "around. The same treatment went to dates: a course card outlives the "
                 "course, and he was still offering a batch that had already started — now "
                 "anything already in the past is caught before it's sent. He also stays on "
                 "the program a conversation is actually about instead of trying a different "
                 "one each nudge, and he no longer holds conversations with other "
                 "businesses' auto-responders.",
    },
    {
        "version": "1.13.0", "date": "16 Jul 2026", "tag": "Conversation",
        "title": "Texts like a person, not a brochure",
        "blurb": "A lead types four words; Stepan was answering with four hundred characters. "
                 "On Instagram that reads as a leaflet, and a leaflet gets skimmed or "
                 "reported — several leads said so in as many words. He now matches the "
                 "register he's given: a one-line question gets a one-line answer, and a lead "
                 "who writes a paragraph still gets the full one. The nudges he sends into "
                 "quiet chats are held shorter still, because nobody asked for those — they "
                 "were quietly the longest messages he sent, and there were more of them than "
                 "live replies. The numbered opener that greets a fresh ad click is left "
                 "alone: its three bubbles are doing a job.",
    },
    {
        "version": "1.12.0", "date": "15 Jul 2026", "tag": "Reporting",
        "title": "Nearly every lead now has a price tag",
        "blurb": "One ad runs in feed, stories and reels — and Meta quietly renders a "
                 "separate post for each, so the version a lead actually saw is rarely the "
                 "one the API admits to. That mismatch meant less than half your leads could "
                 "be tied to the ad that bought them, and the spend behind the rest simply "
                 "went unaccounted. Every placement is rendered from the same source image, "
                 "and that turned out to be the thread that ties them back together. "
                 "Coverage went from 45% to 94%: the cost of a lead, per campaign, is now "
                 "computed from nearly all of the money rather than half of it.",
    },
    {
        "version": "1.11.0", "date": "15 Jul 2026", "tag": "Reporting",
        "title": "Spend and funnel, one tree",
        "blurb": "Ad spend and your funnel used to be two tables you had to join by eye. "
                 "They are now one tree, grouped by campaign — the unit your budget is "
                 "actually planned in. Open a campaign and you see what it cost and what it "
                 "brought, ad by ad, with cost per lead you actually hold. Ads we could not "
                 "match to a campaign are not swept away: they keep their own group, so the "
                 "lead count never quietly shrinks to make the spend look tidier. The "
                 "match rate is printed right on the panel — today it is 38%, and the "
                 "reason is worth knowing: the missing ads are not in the ad account our "
                 "access points at.",
    },
    {
        "version": "1.10.0", "date": "15 Jul 2026", "tag": "Reporting",
        "title": "What each ad really costs you",
        "blurb": "Your reports now show real Meta spend next to your own funnel, ad by ad. "
                 "Not the headline 'cost per conversation' — the cost of a lead you actually "
                 "hold, and of a lead that reached a hand-off. The two rarely match: Meta "
                 "bills for people who tapped, you only bank the ones who talked, and the gap "
                 "between those columns is where a budget quietly leaks. Meta's own "
                 "conversation-depth counts sit alongside your stages as a second opinion, "
                 "and the number of people who blocked you is finally visible. Every table "
                 "shows how many of its leads it could match to an ad and when the numbers "
                 "were last synced — a spend report that hides its own gaps is worse than "
                 "none.",
    },
    {
        "version": "1.9.0", "date": "15 Jul 2026", "tag": "Selling",
        "title": "Earns the price before naming it",
        "blurb": "When a lead finally admits what is holding them back, Stepan no longer "
                 "answers with the price list. It first asks what they actually want to "
                 "change, so the number lands against something worth paying for — until then "
                 "the pitch waits. What it learns about each lead is now kept only when the "
                 "lead really said it, so the goals and worries on their card are their own "
                 "words, not the ad's copy or a guess. And your dashboard got honest: it now "
                 "counts the leads whose real concern was uncovered, instead of everyone who "
                 "simply passed through the funnel, and it shows the deals actually closed in "
                 "the period you picked alongside the view by lead cohort — so a good week "
                 "stops reading as a bad one.",
    },
    {
        "version": "1.8.0", "date": "15 Jul 2026", "tag": "Straight answers",
        "title": "Answers the question that was asked",
        "blurb": "Ask Stepan a plain question and you get a plain answer. \"How much is it?\", "
                 "\"which days?\", \"how do I sign up?\" are now answered from your own course "
                 "card in that same reply, with the follow-up question after — never a "
                 "\"could you be more specific?\" or a counter-question, which is exactly how "
                 "an interested lead used to slip away. And when someone shares a post or reel "
                 "that will not open on our side, Stepan says so honestly and asks them to "
                 "describe it, instead of quietly guessing what it was about.",
    },
    {
        "version": "1.7.0", "date": "15 Jul 2026", "tag": "Selling",
        "title": "Sells the way people actually decide",
        "blurb": "Stepan now handles a chat the way a good human rep would. It gets to know "
                 "what a lead really wants before pitching, and reads a polite 'maybe later' or "
                 "'let me think about it' as a cue to ease off, not push harder, so fewer leads "
                 "go quiet. When money is tight it opens with an affordable first step instead "
                 "of the full price, and with a school student it brings a parent into the "
                 "conversation. It never promises an income or dresses a public example up as "
                 "its own graduate. The result is a calmer, more human conversation that more "
                 "people actually finish.",
    },
    {
        "version": "1.6.0", "date": "14 Jul 2026", "tag": "New",
        "title": "Seller persona library",
        "blurb": "Give every brand or location a proven, ready-made sales personality instead "
                 "of writing one from scratch. Each persona is versioned and shows who authored "
                 "it and how widely it is used, and any location can layer its own house rules "
                 "on top, section by section. Your product catalog stays yours; the selling "
                 "craft is shared and keeps improving for everyone.",
    },
    {
        "version": "1.5.0", "date": "13 Jul 2026", "tag": "Trust",
        "title": "Never makes things up",
        "blurb": "Every price, date and promise now comes only from your own facts. Stepan will "
                 "not invent a number or a class that does not exist, and a built-in guard "
                 "re-checks each reply before it sends. When something cannot be answered "
                 "safely it asks your team instead of guessing, so its messages survive a "
                 "screenshot.",
    },
    {
        "version": "1.4.0", "date": "12 Jul 2026", "tag": "New",
        "title": "Smarter, more human conversations",
        "blurb": "Stepan sounds even more like your best rep. It uncovers the real goal and the "
                 "pain behind it before pitching, holds one warm, consistent tone from the "
                 "first hello to the close, stays aware of today's date so it never offers a "
                 "class that already passed, and no longer slips into repetitive loops on an "
                 "unusual question.",
    },
    {
        "version": "1.3.0", "date": "11 Jul 2026", "tag": "New",
        "title": "Stepan sees and hears",
        "blurb": "Leads rarely type everything. Stepan now reads the images they send, a "
                 "screenshot, a price list, a payment proof, and understands voice notes, then "
                 "answers what was actually shown or said. It is all translated into your "
                 "team's language in the chat log.",
    },
    {
        "version": "1.2.0", "date": "08 Jul 2026", "tag": "New",
        "title": "Smart follow-ups and a clean hand-off",
        "blurb": "Stepan brings back leads who went quiet with fresh, natural angles that never "
                 "repeat and stay safe for your account. When a lead turns hot it captures a "
                 "phone first, then passes it to your team at exactly the right moment, never a "
                 "dead-end bot.",
    },
    {
        "version": "1.1.0", "date": "30 Jun 2026", "tag": "Insight",
        "title": "Operator dashboard, funnel and ad attribution",
        "blurb": "See your whole funnel, your peak hours and exactly which ad drives which "
                 "sale, with conversions pushed back so your ad algorithm learns who buys. "
                 "Coach Stepan in plain words and it updates its own playbook, with your "
                 "approval.",
    },
    {
        "version": "1.0.0", "date": "29 Jun 2026", "tag": "Launch",
        "title": "Stepan is live",
        "blurb": "Your AI sales agent that greets, qualifies and closes leads in your DMs, "
                 "24/7 and in any language, across Instagram, WhatsApp and Messenger. Grounded "
                 "only in your own facts, with a live demo you can try right on this page.",
    },
]

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#08090c;--panel:#0e1014;--panel2:#15171d;--line:#20232b;--line2:#2b2f38;--ink:#f2f4f7;--mut:#9aa3b2;--faint:#666e7d;--acc:#ff5c35;--acc-soft:rgba(255,92,53,.12);--ok:#4cc38a;--sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;--disp:'Space Grotesk',var(--sans)}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6;-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:inherit;text-decoration:none}
.wrap{max-width:820px;margin:0 auto;padding:0 24px}
nav{position:sticky;top:0;z-index:20;background:rgba(8,9,12,.72);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.nav{display:flex;align-items:center;justify-content:space-between;height:64px;max-width:1120px;margin:0 auto;padding:0 24px}
.brand{display:flex;align-items:center;gap:.6rem;font-family:var(--disp);font-weight:700;font-size:1.16rem;letter-spacing:-.01em}
.logo{width:29px;height:29px;border-radius:8px;background:var(--ink);color:#000;display:flex;align-items:center;justify-content:center;font-family:var(--disp);font-weight:700}
.back{font-size:.9rem;color:var(--mut);border:1px solid var(--line2);padding:.44rem .95rem;border-radius:9px;transition:.16s}
.back:hover{color:var(--ink);border-color:var(--faint)}
.hero{padding:4rem 0 1.5rem;text-align:center}
.eyebrow{display:inline-flex;align-items:center;gap:.5rem;font-size:.72rem;color:var(--mut);border:1px solid var(--line2);background:var(--panel);padding:.34rem .8rem;border-radius:999px;margin-bottom:1.4rem}
.eyebrow .d{width:6px;height:6px;border-radius:50%;background:var(--acc)}
h1{font-family:var(--disp);font-size:clamp(2rem,5vw,3rem);line-height:1.05;font-weight:700;letter-spacing:-.03em}
.sub{max-width:560px;margin:1.1rem auto 0;color:var(--mut);font-size:1.05rem}
section{padding:1.5rem 0 4rem}
.next{border:1px solid var(--acc);background:linear-gradient(180deg,var(--acc-soft),var(--panel) 70%);border-radius:18px;padding:1.7rem;margin-bottom:2.5rem}
.next .k{color:var(--acc);font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;margin-bottom:.7rem}
.next h2{font-family:var(--disp);font-size:1.45rem;font-weight:700;letter-spacing:-.02em;margin-bottom:.5rem}
.next p{color:var(--mut);font-size:.98rem}
.tl{position:relative;padding-left:1.6rem}
.tl::before{content:"";position:absolute;left:5px;top:.4rem;bottom:.4rem;width:1px;background:var(--line2)}
.rel{position:relative;padding:0 0 2rem}
.rel::before{content:"";position:absolute;left:-1.6rem;top:.35rem;width:11px;height:11px;border-radius:50%;background:var(--acc);box-shadow:0 0 0 4px var(--bg),0 0 0 5px var(--line2);margin-left:-.05rem}
.rel .meta{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.5rem}
.ver{font-family:var(--disp);font-weight:700;font-size:1.15rem;letter-spacing:-.01em}
.tag{font-size:.6rem;letter-spacing:.1em;text-transform:uppercase;color:var(--acc);border:1px solid var(--acc-soft);background:var(--acc-soft);padding:.14rem .5rem;border-radius:6px}
.date{font-size:.78rem;color:var(--faint)}
.rel h3{font-family:var(--disp);font-size:1.08rem;font-weight:600;margin:.1rem 0 .35rem;letter-spacing:-.01em}
.rel p{color:var(--mut);font-size:.95rem}
footer{border-top:1px solid var(--line);padding:2rem 0;color:var(--faint);font-size:.82rem;text-align:center}
footer a{color:var(--mut)}footer a:hover{color:var(--ink)}
"""


def _release_html(r: dict) -> str:
    return (
        '<div class="rel">'
        '<div class="meta">'
        f'<span class="ver">v{_h.escape(r["version"])}</span>'
        f'<span class="tag">{_h.escape(r["tag"])}</span>'
        f'<span class="date">{_h.escape(r["date"])}</span>'
        '</div>'
        f'<h3>{_h.escape(r["title"])}</h3>'
        f'<p>{_h.escape(r["blurb"])}</p>'
        '</div>'
    )


def changelog_html() -> str:
    releases = "".join(_release_html(r) for r in RELEASES)
    next_card = ""
    if COMING_NEXT:
        next_card = (
            '<div class="next">'
            '<div class="k">Coming next</div>'
            f'<h2>{_h.escape(COMING_NEXT["title"])}</h2>'
            f'<p>{_h.escape(COMING_NEXT["blurb"])}</p>'
            '</div>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>What\'s New — Stepan</title>'
        '<meta name="description" content="The latest improvements to Stepan, the AI sales agent that closes in your DMs.">'
        + _whatsnew_seo() +
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">'
        '<link rel="icon" href="data:image/svg+xml,'
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<rect width='32' height='32' rx='8' fill='%23f2f4f7'/>"
        "<text x='16' y='23' font-size='20' font-weight='700' fill='black' text-anchor='middle' font-family='Arial'>S</text></svg>\">"
        f'<style>{_CSS}</style></head><body>'
        '<nav><div class="nav">'
        '<a class="brand" href="/"><span class="logo">S</span>Stepan</a>'
        '<a class="back" href="/">← Home</a>'
        '</div></nav>'
        '<header class="hero"><div class="wrap">'
        f'<span class="eyebrow"><span class="d"></span>Version {_h.escape(PROJECT_VERSION)}</span>'
        "<h1>What's new in Stepan</h1>"
        '<p class="sub">The improvements that help Stepan sell more of your leads — no fluff, '
        'just what matters for your business.</p>'
        '</div></header>'
        '<section><div class="wrap">'
        f'{next_card}'
        f'<div class="tl">{releases}</div>'
        '</div></section>'
        '<footer><div class="wrap">'
        'Stepan · AI sales agent · <a href="/">Home</a> · <a href="/login">Log in</a>'
        '</div></footer>'
        '</body></html>'
    )
