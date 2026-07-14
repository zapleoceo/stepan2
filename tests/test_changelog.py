"""Public 'What's New' changelog + project version, and its links from the landing."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._changelog import PROJECT_VERSION, RELEASES, changelog_html  # noqa: E402
from app.api._landing import landing_html  # noqa: E402
from app.api.main import app  # noqa: E402


def test_project_version_matches_latest_release() -> None:
    """Enforced discipline: bumping the version and adding a release note stay in lockstep, so
    every deploy that changes the version ships a matching customer-facing note (and vice-versa)."""
    assert RELEASES, "at least one release must be listed"
    assert PROJECT_VERSION == RELEASES[0]["version"]


def test_every_release_is_complete() -> None:
    seen = set()
    for r in RELEASES:
        for k in ("version", "date", "tag", "title", "blurb"):
            assert r.get(k), f"release {r.get('version')} missing {k}"
        assert r["version"] not in seen, "duplicate version"
        seen.add(r["version"])


def test_changelog_page_renders_version_and_entries() -> None:
    html = changelog_html()
    assert f"Version {PROJECT_VERSION}" in html
    assert RELEASES[0]["title"] in html
    assert "Seller persona library" in html            # shipped feature, not just a teaser
    assert "Two-way CRM sync" in html                  # the 'coming next' teaser


def test_releases_read_as_features_not_bugfixes() -> None:
    # The public changelog is a product story, not a fix log: every entry leads with a
    # capability a buyer cares about, and none is worded as a raw bug fix.
    titles = " ".join(r["title"].lower() for r in RELEASES)
    assert "fix" not in titles and "bug" not in titles
    assert any("persona" in r["title"].lower() for r in RELEASES)      # newest big feature
    assert any("sees and hears" in r["title"].lower() for r in RELEASES)  # vision + voice


def test_whats_new_route_is_public() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/whats-new")
    assert resp.status_code == 200
    assert "What's new in Stepan" in resp.text


def test_landing_links_to_whats_new() -> None:
    assert '/whats-new' in landing_html()
