"""The api container must actually receive the Meta webhook secrets.

app/api/webhooks.py reads STEPAN2_META_VERIFY_TOKEN_<branch> and
STEPAN2_META_APP_SECRET_<branch> straight from os.environ, and the signature check is
fail-closed — a missing secret rejects every payload. So a value written to infra/.env but not
passed into the container looks exactly like a webhook Meta refuses to deliver to, with nothing
in the logs pointing at the cause. That already happened once with the landing pixel.

The names are per branch, so they cannot be listed under `environment:` without an infra edit
for every new branch. env_file is what carries them; this pins it.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_COMPOSE = Path(__file__).resolve().parents[1] / "infra" / "docker-compose.yml"


def _api_service() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]["api"]


def test_api_passes_the_whole_env_file_through() -> None:
    env_file = _api_service().get("env_file")
    assert env_file, "api must pass infra/.env through or per-branch webhook secrets never land"
    assert ".env" in (env_file if isinstance(env_file, list) else [env_file])


def test_explicit_entries_are_kept() -> None:
    """env_file cannot rename or default a value; those entries still earn their place."""
    env = _api_service()["environment"]
    assert "STEPAN2_DATABASE_URL" in env
    assert "STEPAN2_SECRET_KEY" in env
