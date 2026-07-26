"""SEO surfaces for the public site: robots.txt, sitemap.xml, llms.txt and a social/OG card.

The marketing pages (/, /privacy) are open to search engines AND to LLM crawlers so the
product can be discovered and cited. Everything behind auth (the /ui app, /admin, the MCP
mounts) is disallowed. Base URL comes from settings().public_url."""
from __future__ import annotations

from app.config import settings

# Public, indexable marketing pages (path, changefreq, priority).
_PUBLIC_PAGES = (
    ("/", "weekly", "1.0"),
    ("/privacy", "yearly", "0.3"),
)

# Answer-engine / LLM crawlers we explicitly welcome (in addition to classic search).
_AI_AGENTS = (
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-Web",
    "anthropic-ai", "PerplexityBot", "Perplexity-User", "Google-Extended",
    "Applebot-Extended", "Bytespider", "CCBot", "Amazonbot", "Meta-ExternalAgent",
    "cohere-ai", "YouBot", "DuckAssistBot",
)


def _base() -> str:
    return (settings().public_url or "https://stepan2.zapleo.com").rstrip("/")


def robots_txt() -> str:
    base = _base()
    lines = ["User-agent: *", "Allow: /$"]
    for path in ("/privacy", "/llms.txt", "/og.svg"):
        lines.append(f"Allow: {path}")
    for path in ("/ui/", "/admin/", "/connector/", "/reader/", "/mcp/",
                 "/webhooks/", "/demo/", "/login", "/api/", "/hiw"):
        lines.append(f"Disallow: {path}")
    # Named AI crawlers — explicit Allow so a future site-wide block doesn't silently
    # exclude them from the marketing pages.
    for agent in _AI_AGENTS:
        lines += ["", f"User-agent: {agent}", "Allow: /$", "Allow: /llms.txt",
                  "Allow: /privacy", "Disallow: /ui/", "Disallow: /admin/"]
    lines += ["", f"Sitemap: {base}/sitemap.xml"]
    return "\n".join(lines) + "\n"


def sitemap_xml() -> str:
    base = _base()
    urls = "".join(
        f"<url><loc>{base}{path}</loc>"
        f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>"
        for path, cf, pr in _PUBLIC_PAGES
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>")


def llms_txt() -> str:
    """Plain-fact product summary for answer engines.

    Answer engines quote checkable statements, not adjectives — so this is numbers and
    capabilities, no marketing copy. Every figure here must also appear on the landing page:
    a fact only stated in llms.txt reads as unverifiable and gets dropped."""
    base = _base()
    return f"""# Stepan

> An AI sales agent that answers, qualifies and closes leads inside Instagram Direct,
> WhatsApp and Facebook Messenger — in the customer's own language, 24 hours a day.
> Built for businesses whose leads arrive as direct messages.

## What it does
- Replies to a new direct message in under 60 seconds, day or night.
- Answers only from the business's own uploaded facts (price list, schedule, terms) — it
  cannot invent a price or a promise.
- Qualifies the buyer in conversation (need, timing, fit) instead of a form.
- Captures the phone number and pushes the contact, the conversation and the source ad into
  the business's CRM over an open MCP connector.
- Hands a hot lead to a human the moment it is ready to buy.
- Follows up with silent leads on a human-paced schedule.
- Runs on the official Meta Graph API with per-hour and per-day sending caps and quiet hours.

## Channels
Instagram Direct, WhatsApp, Facebook Messenger. Telegram on request. TikTok planned.

## Languages
Replies in the language the customer writes in, including Bahasa Indonesia — one agent, no
separate setup per market.

## How it differs from a free platform assistant
- It is grounded in the business's own documents, not only a product catalogue and past chats.
- The captured contact is pushed into the business's own CRM.
- Instagram Direct is automated, not only WhatsApp.
- Conversations are never used to train anyone else's model.

## Links
- Product: {base}/
- Pricing: {base}/#pricing
- Safety and account limits: {base}/#safety
- Privacy policy: {base}/privacy
"""


def og_svg() -> str:
    """1200×630 social share card — self-contained SVG, no external assets."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" '
        'viewBox="0 0 1200 630" font-family="Inter,Arial,sans-serif">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0b0d12"/><stop offset="1" stop-color="#161b26"/>'
        '</linearGradient></defs>'
        '<rect width="1200" height="630" fill="url(#g)"/>'
        '<rect x="72" y="72" width="86" height="86" rx="22" fill="#f2f4f7"/>'
        '<text x="115" y="133" font-size="52" font-weight="700" fill="#0b0d12" '
        'text-anchor="middle">S</text>'
        '<text x="176" y="132" font-size="40" font-weight="600" fill="#e7ebf3">Stepan</text>'
        '<text x="72" y="330" font-size="72" font-weight="700" fill="#ffffff">'
        'The AI sales agent</text>'
        '<text x="72" y="416" font-size="72" font-weight="700" fill="#8ea0c4">'
        'that closes in your DMs</text>'
        '<text x="72" y="516" font-size="34" fill="#9aa6bd">'
        'Qualifies &amp; sells in Instagram &amp; WhatsApp — like your best rep, 24/7</text>'
        '</svg>'
    )
