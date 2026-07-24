"""Shipped-release history — data only, no public page.

Lived in app/api/_changelog.py behind the public /whats-new page until 2026-07-25, when that
page was removed: a public changelog told visitors (and LLM crawlers) how young the product is
and which vertical it grew out of. The data itself still earns its place — the daily owner
digest lists recent changes so the reviewer does not re-propose what already shipped.
"""
from __future__ import annotations

# Bump this together with a new RELEASES[0] entry (tests keep them in sync).
PROJECT_VERSION = "1.22.0"

# A short teaser for the big thing currently rolling out — shown as a highlighted card above
# the shipped history. Set to None when there's nothing meaningful in flight.
COMING_NEXT = {
    "title": "Event reminders that actually reach people",
    "blurb": "Phone reminders reach barely a quarter of event sign-ups. Stepan will nudge "
             "them in the chat they already answer — and write the confirmation back into "
             "your CRM.",
}

# Newest first. Each: version, date (DD Mon YYYY), tag (one word), title, blurb (buyer-facing).
RELEASES = [
    {
        "version": "1.22.0", "date": "19 Jul 2026", "tag": "Reach",
        "title": "Answers comments under your posts — and pulls the warm ones into DMs",
        "blurb": "A post that takes off fills with questions no one has time to answer, and "
                 "every unanswered 'how much?' is a buyer walking away. Once an hour Stepan "
                 "now reads the new comments under your own posts, replies to the real "
                 "questions with a short public answer straight from your knowledge base, and "
                 "invites the interested ones to continue in DMs — where the full sales "
                 "conversation takes over. It stays quiet on the noise: praise, tags and "
                 "'first!' are left alone, spam and abuse are hidden, and — because a public "
                 "mistake is the one everyone screenshots — it will never post a price or "
                 "fact it can't ground in your knowledge base; when unsure it simply invites "
                 "a DM. A new Comments tab shows exactly what it replied to and why, just "
                 "like the chat view. Off until you switch it on, per account.",
    },
    {
        "version": "1.21.0", "date": "17 Jul 2026", "tag": "Selling",
        "title": "Reads the buying moment — and stops interrogating it",
        "blurb": "A fresh audit of 100 conversations showed where warm leads still cooled "
                 "off, and this release fixes each spot. A client who says 'I want to "
                 "join' now gets one small, warm next step — the seat-holding deposit and "
                 "a WhatsApp confirmation — instead of a price-list wall that scared off a "
                 "real buyer the same morning. A client who answers the choice menu with a "
                 "'1' gets value for that exact choice, not another survey question — "
                 "that loop had asked one lead the same question three times in a day. "
                 "Saying 'the budget feels heavy' now brings the affordable entry option "
                 "right away. Someone sharing our own Instagram post is greeted as an "
                 "interested visitor instead of being told the content can't be opened. "
                 "A message with several questions gets every one of them answered, in "
                 "order. And follow-ups are now held to the same honesty bar as live "
                 "replies: no invented success stories, percentages or deadlines — ever.",
    },
    {
        "version": "1.20.0", "date": "17 Jul 2026", "tag": "Integration",
        "title": "Two-way CRM sync is live — and it rescues the leads your phone can’t reach",
        "blurb": "Stepan now reads your CRM before every message he sends. A client whose "
                 "contract is already signed, or who spoke to a manager this week, is left "
                 "alone — automatically, no list to maintain. And the same wire works the "
                 "other way: in the branch we audited, 41% of a month's phone leads never "
                 "picked up, and every future buyer had sat through unanswered calls on the "
                 "way to a contract. Stepan now reads the CRM call log, finds the people "
                 "the phone keeps missing, and quietly continues them in the chat they "
                 "actually answer — a steady trickle during working hours, one polite "
                 "message per lead per week, never someone a human is already handling.",
    },
    {
        "version": "1.19.0", "date": "17 Jul 2026", "tag": "Selling",
        "title": "Closes at the checkout, never invents a number",
        "blurb": "An audit of 300 live conversations found the moments Stepan lost real "
                 "buyers — and this release closes them. A client who asks where to send "
                 "the money now gets the payment details, the amount and a confirmation "
                 "step in one message (one buyer literally asked for the bank account and "
                 "got a sales pitch instead). The reverse is now impossible too: bank "
                 "details never go to someone who hasn't asked to pay, and a price can "
                 "never be a made-up number — if the real figure isn't in front of him, "
                 "Stepan says he'll confirm it rather than guessing. The dead-end reply "
                 "'could you be more specific?' became a tappable menu of the four things "
                 "people actually want: price, schedule, curriculum, how to enrol. "
                 "Freeloaders and spammers get a polite close and a quiet note to the "
                 "team instead of days of pitching. And two delivery bugs are gone: the "
                 "same message can no longer arrive twice in a row, and a follow-up can "
                 "no longer say 'let me check with the team' to someone who asked "
                 "nothing.",
    },
    {
        "version": "1.18.0", "date": "17 Jul 2026", "tag": "Selling",
        "title": "A price question gets a price",
        "blurb": "The hottest moment in any chat is the client asking how much — and in "
                 "roughly half of those moments Stepan used to reply with ‘may I have your "
                 "WhatsApp first?’ instead of the number. Measured on two weeks of live "
                 "conversations, a clearly framed price keeps clients talking just as well "
                 "as holding it back — the silence after a dodged question is what loses "
                 "them. Stepan now always answers a direct price question in that same "
                 "message: he leads with the smallest real step — the seat-holding down "
                 "payment or the monthly instalment — gives the full amount as context, and "
                 "only then continues the conversation. The contact ask still happens, just "
                 "never INSTEAD of the answer. And a brand-new client who only clicked an ad "
                 "can no longer be greeted with a request for their phone number.",
    },
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
