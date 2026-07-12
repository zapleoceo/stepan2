"""Landing redesign: hero WebGL shader + scroll reveals, and the two new content sections
(insights cloud + seller persona library). Guards the render and the em-dash discipline."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._landing import (  # noqa: E402
    _insights_cloud_section,
    _persona_library_section,
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
    assert "Lead intelligence" in html and "Goals" in html and "Fears" in html
    assert "Seller persona library" in html and "The Consultative Closer" in html


def test_new_sections_and_hero_have_no_em_dash() -> None:
    # the redesigned surfaces must be free of the AI-tell em/en dash
    for chunk in (_insights_cloud_section(), _persona_library_section()):
        assert _EM not in chunk and _EN not in chunk
    html = landing_html()
    hero = html[html.find('<header class="hero">'):html.find("</header>")]
    assert _EM not in hero and _EN not in hero


def test_root_route_serves_landing() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="herofx"' in resp.text
