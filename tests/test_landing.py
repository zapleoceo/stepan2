"""Landing redesign: hero WebGL shader + scroll reveals, and the two new content sections
(insights cloud + seller persona library). Guards the render and the em-dash discipline."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._landing import (  # noqa: E402
    landing_html,
)
from app.api.main import app  # noqa: E402

_EM = "—"  # em-dash
_EN = "–"  # en-dash


def test_hero_has_webgl_shader_and_reveal_boot() -> None:
    html = landing_html()
    assert 'id="herofx"' in html and "getContext('webgl2'" in html
    assert "has-reveal" in html                      # scroll-reveal system present
    assert 'class="hero-scrim"' in html              # contrast scrim over the shader


def test_new_sections_present() -> None:
    html = landing_html()
    assert "goals, pains and fears" in html and "Goals" in html and "Fears" in html
    assert "A library of proven sales personas" in html
    assert "The Consultative Closer" in html


def test_whole_page_has_no_em_or_en_dash() -> None:
    # zero em/en dashes anywhere in the rendered page: the #1 AI-tell
    html = landing_html()
    assert _EM not in html and _EN not in html


def test_insights_cloud_carries_no_client_vertical() -> None:
    """The examples must not hint at any real client's industry. Education-course phrasing
    (jobs, careers, tutorials, students, hired) pointed straight at the actual client."""
    html = landing_html()
    for trace in ("data job", "careers into tech", "get me hired", "tutorials",
                  "Students", "full course"):
        assert trace not in html, trace


def test_trust_strip_lives_below_the_hero_not_inside() -> None:
    html = landing_html()
    hero = html[html.find('<header class="hero">'):html.find("</header>")]
    assert 'class="trust"' not in hero      # hero = value prop + CTA only
    assert 'class="trust"' in html          # the strip still exists, in its own section


def test_eyebrow_budget() -> None:
    # max ~1 kick per 3 sections across the ~15 sections (How it works, Account safety,
    # Works with your stack, vs Meta, Pricing); headlines carry the rest.
    html = landing_html()
    assert html.count('class="kick"') <= 5


def test_faq_section_with_schema() -> None:
    html = landing_html()
    assert "Questions owners actually ask" in html
    assert "How fast can we go live?" in html
    assert "Will it put my account at risk?" in html
    assert '"FAQPage"' in html              # quotable by search engines and AI assistants
    assert html.count("<details") == 6      # one per question, no empties


def test_nav_links_to_pricing() -> None:
    html = landing_html()
    assert 'href="#pricing"' in html and 'id="pricing"' in html


def test_account_safety_section_answers_the_ban_fear() -> None:
    """The #1 objection for a Meta-savvy buyer is 'will this get my account banned?'. The page
    must name it and answer with the two real paths + the anti-ban engineering, all grounded."""
    html = landing_html()
    assert 'id="safety"' in html and 'href="#safety"' in html   # section + nav anchor
    assert "banned" in html.lower()
    # both honest paths are shown
    assert "official" in html.lower() and "Graph API" in html
    assert "direct DM integration" in html
    # the concrete, code-grounded safety measures (paced/caps/quiet/session/backoff/control)
    for claim in ("Paces like a person", "Sends within safe caps", "Sleeps on quiet hours",
                  "One steady session", "Backs off at first friction", "your switch"):
        assert claim in html, claim
    # no false 'never banned' guarantee
    assert "never get banned" not in html.lower()
    assert "no tool" in html.lower()                            # the honest disclaimer


def test_section_labels_are_legible_not_tiny_caps() -> None:
    # the 'kick' labels used to render at .74rem; bumped + given a leading accent rule so they
    # read as a real label (a repeat complaint that AI subheads are unreadably small)
    html = landing_html()
    assert ".kick{display:inline-flex" in html and "font-size:.82rem" in html
    assert ".kick::before" in html                             # the accent rule


def test_stats_strip_under_hero_with_honest_label() -> None:
    """Real production totals (rounded down), never unlabeled: the page marks them as live
    totals with a date so they read as measured numbers, not marketing invention."""
    html = landing_html()
    assert 'class="stats' in html
    assert "3,600+" in html and "29,000+" in html and "200+" in html
    assert "Live production totals" in html and "July 2026" in html
    # placed right after the hero, before everything else
    assert html.find('class="stats') < html.find('class="trust"')


def test_og_meta_points_at_png_for_messengers() -> None:
    # Telegram/WhatsApp skip SVG og:images entirely; the preview must be the PNG
    html = landing_html()
    assert 'og:image" content="https://stepan2.zapleo.com/og.png"' in html \
        or '/og.png">' in html
    assert 'og:image:width" content="1200"' in html
    assert 'og:image:height" content="630"' in html
    assert "og.svg" not in html


def test_og_png_renders_a_real_image() -> None:
    from app.api._og import og_png
    b = og_png()
    assert b[:4] == b"\x89PNG"
    assert len(b) > 20_000                    # a drawn card, not a stub


def test_root_route_serves_landing() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="herofx"' in resp.text


def test_landing_has_seo_and_structured_data() -> None:
    html = landing_html()
    assert 'rel="canonical"' in html
    assert 'name="robots"' in html and "index,follow" in html
    assert 'property="og:title"' in html and 'name="twitter:card"' in html
    assert 'application/ld+json' in html and '"SoftwareApplication"' in html


def test_seo_endpoints_render() -> None:
    from app.api._seo import og_svg, robots_txt, sitemap_xml
    r = robots_txt()
    assert "Sitemap:" in r and "Disallow: /ui/" in r and "GPTBot" in r and "ClaudeBot" in r
    sm = sitemap_xml()
    assert "/whats-new" in sm and "<urlset" in sm
    assert og_svg().startswith("<svg") and "Stepan" in og_svg()


# ─── Meta pixel on the landing (selling Stepan) ───────────────────────────────
# Off by default so the public page stays clean; on when a pixel id is set, it fires Lead on
# demo-open and Contact on the first message — turning a cold-traffic ad from optimise-for-
# clicks into optimise-for-engagement, the thing this very page advertises.

def test_no_pixel_without_a_configured_id() -> None:
    from app.config import settings
    settings.cache_clear()
    html = landing_html()
    # The guarded fbq('track',...) calls are always in the widget JS (inert no-ops without a
    # pixel). What must be ABSENT is the base loader that actually installs tracking.
    assert "connect.facebook.net" not in html
    assert "fbq('init'" not in html


def test_pixel_injected_and_events_wired_when_id_set(monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setenv("STEPAN2_LANDING_PIXEL_ID", "111222333444555")
    settings.cache_clear()
    try:
        html = landing_html()
        assert "fbq('init','111222333444555')" in html   # base code with the id
        assert "fbq('track','PageView')" in html
        assert "fbq('track','Lead'" in html              # fired on demo open
        assert "fbq('track','Contact'" in html           # fired on first message
        assert "window.fbq&&" in html                    # every call guarded — never throws
    finally:
        settings.cache_clear()


def test_pixel_id_is_escaped() -> None:
    from app.config import settings
    monkeypatch_id = '99"><script>x'
    import os
    os.environ["STEPAN2_LANDING_PIXEL_ID"] = monkeypatch_id
    settings.cache_clear()
    try:
        html = landing_html()
        assert "<script>x" not in html.split("fbevents")[0][-200:]  # not injected raw
    finally:
        del os.environ["STEPAN2_LANDING_PIXEL_ID"]
        settings.cache_clear()
