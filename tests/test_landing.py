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
    # max ~1 kick per 3 sections; the hero eyebrow is the 5th allowed label
    html = landing_html()
    assert html.count('class="kick"') <= 4


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
