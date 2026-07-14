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
PROJECT_VERSION = "1.4.0"

# A short teaser for the big thing currently rolling out — shown as a highlighted card above
# the shipped history. Set to None when there's nothing meaningful in flight.
COMING_NEXT = {
    "title": "Seller Persona Library",
    "blurb": "Pick a proven, versioned sales persona for each brand or location instead of "
             "writing one from scratch. Track which persona sells best and roll the winner "
             "out everywhere — your product catalog stays yours, the selling craft is shared.",
}

# Newest first. Each: version, date (DD Mon YYYY), tag (one word), title, blurb (buyer-facing).
RELEASES = [
    {
        "version": "1.4.0", "date": "12 Jul 2026", "tag": "New",
        "title": "Smarter, more human conversations",
        "blurb": "Stepan now sounds even more like your best rep: no repetitive scripts, no "
                 "robotic loops when a question is unusual, always aware of today's date so it "
                 "never offers a class that already passed, and one warm, consistent tone from "
                 "the first hello to the close.",
    },
    {
        "version": "1.3.0", "date": "29 Jun 2026", "tag": "New",
        "title": "Live demo — let Stepan sell you",
        "blurb": "Chat with Stepan right on this page and watch it qualify and pitch in real "
                 "time. The moment a visitor is ready to buy and leaves a contact, your team "
                 "gets an instant hand-off.",
    },
    {
        "version": "1.2.0", "date": "20 Jun 2026", "tag": "Channels",
        "title": "WhatsApp & Messenger, not just Instagram",
        "blurb": "One brain across Instagram, WhatsApp and Messenger DMs — every lead, every "
                 "channel, answered in their own language.",
    },
    {
        "version": "1.1.0", "date": "10 Jun 2026", "tag": "Insight",
        "title": "Operator-grade analytics & ad attribution",
        "blurb": "See your full funnel, your peak hours, and exactly which ad drives which "
                 "sale — with conversions pushed back so your ad algorithm learns who buys.",
    },
    {
        "version": "1.0.0", "date": "15 May 2026", "tag": "Launch",
        "title": "Stepan is live",
        "blurb": "Your AI sales agent that greets, qualifies and closes leads in your DMs — "
                 "24/7, in any language, grounded only in your own facts.",
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
