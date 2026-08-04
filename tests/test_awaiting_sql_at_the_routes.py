"""The awaiting filter must be DERIVED at the routes that execute it, not just in the helper.

The helper (awaiting_kind_sql) had its own test, and it passed while the routes pasted a
literal `c.kind <> 'meta_business'` into their SQL — the derivation was proven at the one place
a revert could not reach. These tests capture the SQL the routes actually hand to the database,
silence a DIFFERENT connector, and require the executed statement to follow.
"""
from __future__ import annotations

import contextlib
import dataclasses
import os
from typing import Any

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.connectors.registry import REGISTRY  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402


class _Result:
    """Answers the two shapes the routes ask for — a row list and a single count row."""

    def all(self) -> list[Any]:
        return []

    def first(self) -> tuple[int]:
        return (0,)


class _RecordingSession:
    """Records the statement as given. NOT a stand-in that returns a fixed answer whatever it
    is asked: the SQL text IS the thing under test."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement))
        return _Result()


def _capture(monkeypatch: pytest.MonkeyPatch, module: Any) -> _RecordingSession:
    session = _RecordingSession()

    @contextlib.asynccontextmanager
    async def _scope() -> Any:
        yield session

    monkeypatch.setattr(module, "session_scope", _scope)
    return session


def _respec(monkeypatch: pytest.MonkeyPatch, kind: ChannelKind, **changes: Any) -> None:
    monkeypatch.setitem(REGISTRY, kind, dataclasses.replace(REGISTRY[kind], **changes))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _sql(session: _RecordingSession) -> str:
    assert session.statements, "the route never executed anything"
    return "\n".join(session.statements)


@pytest.mark.parametrize("awaiting", ["1", "queue", "off", "settled"])
def test_thread_list_sql_silences_whichever_connector_opts_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, awaiting: str,
) -> None:
    """All four awaiting views paste the same base into their SQL, so all four must follow the
    registry — one of them re-hardcoded is one inbox counting Meta chats nobody can answer."""
    from app.api import ui

    session = _capture(monkeypatch, ui)
    assert client.get(f"/ui/threads?awaiting={awaiting}").status_code == 200
    assert "'meta_business'" in _sql(session)

    _respec(monkeypatch, ChannelKind.META_BUSINESS, counts_as_awaiting=True)
    _respec(monkeypatch, ChannelKind.WHATSAPP, counts_as_awaiting=False)
    flipped = _capture(monkeypatch, ui)
    assert client.get(f"/ui/threads?awaiting={awaiting}").status_code == 200
    sql = _sql(flipped)
    assert "'whatsapp'" in sql and "'meta_business'" not in sql


def test_inbox_badge_sql_follows_the_registry_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nav badge builds its own count query from the same base. It is the number an
    operator trusts to mean "nothing is waiting on us"."""
    from app.api import _routes_admin

    session = _capture(monkeypatch, _routes_admin)
    assert client.get("/ui/inbox/awaiting-count").status_code == 200
    assert "'meta_business'" in _sql(session)

    _respec(monkeypatch, ChannelKind.META_BUSINESS, counts_as_awaiting=True)
    _respec(monkeypatch, ChannelKind.WHATSAPP, counts_as_awaiting=False)
    flipped = _capture(monkeypatch, _routes_admin)
    assert client.get("/ui/inbox/awaiting-count").status_code == 200
    sql = _sql(flipped)
    assert "'whatsapp'" in sql and "'meta_business'" not in sql


def test_the_channel_row_must_exist_in_every_awaiting_query(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With NOBODY opted out the kind predicate goes away, but the EXISTS it rode on must not:
    a thread whose channel row is gone was never "awaiting reply" and must not become so on the
    day Meta's connector is finished."""
    from app.api import ui

    for kind in list(REGISTRY):
        _respec(monkeypatch, kind, counts_as_awaiting=True)
    session = _capture(monkeypatch, ui)
    assert client.get("/ui/threads?awaiting=1").status_code == 200
    sql = _sql(session)
    assert "c.id = ct.channel_id" in sql
    assert "c.kind" not in sql
