"""SEO surfaces for the public site: robots.txt, sitemap.xml and a social/OG card.

The marketing pages (/, /whats-new, /privacy) are open to search engines AND to LLM
crawlers so the product can be discovered and cited. Everything behind auth (the /ui app,
/admin, the MCP mounts) is disallowed. Base URL comes from settings().public_url."""
from __future__ import annotations

from app.config import settings

# Public, indexable marketing pages (path, changefreq, priority).
_PUBLIC_PAGES = (
    ("/", "weekly", "1.0"),
    ("/whats-new", "weekly", "0.7"),
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
    for path in ("/whats-new", "/privacy", "/og.svg"):
        lines.append(f"Allow: {path}")
    for path in ("/ui/", "/admin/", "/connector/", "/reader/", "/mcp/",
                 "/webhooks/", "/demo/", "/login", "/api/"):
        lines.append(f"Disallow: {path}")
    # Named AI crawlers — explicit Allow so a future site-wide block doesn't silently
    # exclude them from the marketing pages.
    for agent in _AI_AGENTS:
        lines += ["", f"User-agent: {agent}", "Allow: /$", "Allow: /whats-new",
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
