"""Branch isolation: a branch-scoped read or write acts on the branch ON SCREEN.

The bug these pin: `writable = writable_branch_ids(request); bid = writable[0] if writable
else 1`. writable_branch_ids is None for a super_admin, so the fallback fired for the owner
every single time and sent the write to branch 1 — live Indonesia, 37k messages. Saving the
TEST branch's alert group, greeting or daily budget rewrote Indonesia's, and the test branch
could not be configured at all. The settings panel had the mirror-image defect on the read
side: no branch predicate at all when the filter was off, so five tenants' rows collapsed
into one key→value map.
"""
from __future__ import annotations

import contextlib
import os
import re
import tokenize
from pathlib import Path

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.adapters.db.models import AppSetting, Branch  # noqa: E402
from app.admin._branch import (  # noqa: E402
    selected_branch_id,
    writable_selected_branch_id,
)


def _req(cookie: str = "", *, allowed=..., writable=...) -> Request:
    """A request carrying the branch-filter cookie and, optionally, an auth identity.

    No state at all = auth disabled / super_admin: allowed and writable are both None,
    which is exactly the shape that used to collapse to branch 1.
    """
    scope = {"type": "http", "headers": [(b"cookie", f"stepan2_branch={cookie}".encode())]}
    req = Request(scope)
    if allowed is not ...:
        req.state.allowed_branch_ids = allowed
        req.state.writable_branch_ids = allowed if writable is ... else writable
    elif writable is not ...:
        req.state.writable_branch_ids = writable
    return req


@contextlib.asynccontextmanager
async def _scope_of(session):
    yield session


def _patch_scope(monkeypatch, module: str, session) -> None:
    monkeypatch.setattr(f"{module}.session_scope", lambda: _scope_of(session))


async def _branches(session, n: int = 8) -> None:
    """Branch ids 1..n, so a test can name branch 7 and mean the seventh tenant."""
    for i in range(1, n + 1):
        session.add(Branch(id=i, name=f"B{i}", lang="id"))
    await session.flush()


async def _setting(session, branch_id: int, key: str) -> str | None:
    row = (await session.execute(
        text("SELECT value FROM app_setting WHERE branch_id=:b AND key=:k"),
        {"b": branch_id, "k": key})).first()
    return row[0] if row else None


# ─── the resolver ─────────────────────────────────────────────────────────────

def test_super_admin_resolves_the_branch_they_are_viewing() -> None:
    """The owner has writable_branch_ids=None. Viewing branch 7 must resolve 7, not 1."""
    assert writable_selected_branch_id(_req("7")) == 7
    assert selected_branch_id(_req("7")) == 7


def test_no_branch_in_view_resolves_to_nothing_rather_than_branch_one() -> None:
    """An all-branches view has no single write target — the old code answered 1 anyway."""
    assert writable_selected_branch_id(_req("")) is None
    assert selected_branch_id(_req("")) is None


def test_multi_branch_view_resolves_to_nothing() -> None:
    """Two branches on screen is not a target; `writable[0]` silently picked the first."""
    assert writable_selected_branch_id(_req("3,7")) is None
    assert writable_selected_branch_id(_req(allowed=[3, 7])) is None


def test_branch_admin_gets_the_branch_they_are_viewing_not_the_first_one() -> None:
    """A two-branch admin viewing branch 7 wrote to branch 3 under `writable[0]`."""
    assert writable_selected_branch_id(_req("7", allowed=[3, 7])) == 7
    assert writable_selected_branch_id(_req("3", allowed=[3, 7])) == 3


def test_branch_outside_the_writable_set_resolves_to_nothing() -> None:
    """Read on 3 and 7, write only on 3: viewing 7 must not yield a write target."""
    assert writable_selected_branch_id(_req("7", allowed=[3, 7], writable=[3])) is None
    assert selected_branch_id(_req("7", allowed=[3, 7], writable=[3])) == 7  # still viewable


def test_viewer_has_no_write_target_anywhere() -> None:
    """branch_viewer: writable=[] denies every branch, including the one on screen."""
    assert writable_selected_branch_id(_req("3", allowed=[3], writable=[])) is None


# ─── settings save ────────────────────────────────────────────────────────────

async def test_settings_save_writes_to_the_viewed_branch(db_session, monkeypatch) -> None:
    """Super_admin viewing branch 7 saves tg_group_id → it lands on 7 and branch 1 is
    untouched. Before the fix every such save overwrote branch 1's alert group."""
    from app.api._routes_admin import settings_save_by_key

    await _branches(db_session)
    db_session.add(AppSetting(branch_id=1, key="tg_group_id", value="-100_INDONESIA"))
    await db_session.flush()
    _patch_scope(monkeypatch, "app.api._routes_admin", db_session)

    resp = await settings_save_by_key(
        _req("7"), key="tg_group_id", value="-100_TEST", channel_id=None)

    assert resp.status_code == 200
    assert await _setting(db_session, 7, "tg_group_id") == "-100_TEST"
    assert await _setting(db_session, 1, "tg_group_id") == "-100_INDONESIA"


async def test_settings_save_refuses_when_no_branch_is_selected(db_session, monkeypatch) -> None:
    """No branch on screen → refuse. The old fallback wrote to branch 1 instead."""
    from app.api._routes_admin import settings_save_by_key

    await _branches(db_session)
    _patch_scope(monkeypatch, "app.api._routes_admin", db_session)

    resp = await settings_save_by_key(
        _req(""), key="tg_group_id", value="-100_OOPS", channel_id=None)

    assert resp.status_code == 403
    assert await _setting(db_session, 1, "tg_group_id") is None


async def test_settings_save_forbidden_outside_the_writable_set(db_session, monkeypatch) -> None:
    """A branch_admin of branch 3 viewing branch 7 gets 403 — no write, no silent redirect
    of the write to branch 3."""
    from app.api._routes_admin import settings_save_by_key

    await _branches(db_session)
    _patch_scope(monkeypatch, "app.api._routes_admin", db_session)

    resp = await settings_save_by_key(
        _req("7", allowed=[3, 7], writable=[3]),
        key="tg_group_id", value="-100_OOPS", channel_id=None)

    assert resp.status_code == 403
    assert await _setting(db_session, 7, "tg_group_id") is None
    assert await _setting(db_session, 3, "tg_group_id") is None


# ─── settings panel read ──────────────────────────────────────────────────────

async def test_settings_panel_shows_only_the_viewed_branch(db_session, monkeypatch) -> None:
    """Two tenants, same key, different values — the panel must show the one on screen."""
    from app.api._routes_admin import settings_panel

    await _branches(db_session)
    db_session.add(AppSetting(branch_id=1, key="junk_opener", value="INDONESIA_OPENER"))
    db_session.add(AppSetting(branch_id=7, key="junk_opener", value="TEST_OPENER"))
    await db_session.flush()
    _patch_scope(monkeypatch, "app.api._routes_admin", db_session)

    body = (await settings_panel(_req("7"))).body.decode()

    assert "TEST_OPENER" in body
    assert "INDONESIA_OPENER" not in body


async def test_settings_panel_blends_nothing_with_no_branch_selected(
    db_session, monkeypatch,
) -> None:
    """With the filter off the old query had no branch predicate and merged every tenant's
    rows into one key→value map, so the owner read Indonesia's live values under whatever
    branch they thought they were looking at. Now: no branch, no values."""
    from app.api._routes_admin import settings_panel

    await _branches(db_session)
    db_session.add(AppSetting(branch_id=1, key="junk_opener", value="INDONESIA_OPENER"))
    db_session.add(AppSetting(branch_id=7, key="junk_opener", value="TEST_OPENER"))
    await db_session.flush()
    _patch_scope(monkeypatch, "app.api._routes_admin", db_session)

    body = (await settings_panel(_req(""))).body.decode()

    assert "INDONESIA_OPENER" not in body
    assert "TEST_OPENER" not in body


async def test_settings_panel_ignores_connector_tier_rows(db_session, monkeypatch) -> None:
    """A connector-scoped row shares the key namespace; the branch panel must not present
    one channel's value as the branch's."""
    from app.api._routes_admin import settings_panel

    await _branches(db_session)
    db_session.add(AppSetting(branch_id=7, key="junk_opener", value="BRANCH_TIER"))
    db_session.add(
        AppSetting(branch_id=7, channel_id=5, key="junk_opener", value="CHANNEL_TIER"))
    await db_session.flush()
    _patch_scope(monkeypatch, "app.api._routes_admin", db_session)

    body = (await settings_panel(_req("7"))).body.decode()

    assert "BRANCH_TIER" in body
    assert "CHANNEL_TIER" not in body


# ─── product create ───────────────────────────────────────────────────────────

async def test_product_create_lands_in_the_viewed_branch(db_session, monkeypatch) -> None:
    """Every product a super_admin created went into branch 1's catalogue — the one tenant
    whose catalogue must not grow by accident, since it feeds the live sales prompt."""
    from app.api._routes_products import products_create

    await _branches(db_session)
    _patch_scope(monkeypatch, "app.api._routes_products", db_session)

    await products_create(_req("7"), slug="test-course", title="Test", content="x",
                          is_active="on", sort_order=0)

    owner = (await db_session.execute(
        text("SELECT branch_id FROM product WHERE slug='test-course'"))).first()
    assert owner is not None, "product was not created"
    assert owner[0] == 7


async def test_product_create_refuses_with_no_branch_selected(db_session, monkeypatch) -> None:
    from app.api._routes_products import products_create

    await _branches(db_session)
    _patch_scope(monkeypatch, "app.api._routes_products", db_session)

    resp = await products_create(_req(""), slug="test-course", title="Test", content="x",
                                  is_active="on", sort_order=0)

    assert resp.status_code == 403
    assert (await db_session.execute(text("SELECT count(*) FROM product"))).scalar() == 0


# ─── coach ────────────────────────────────────────────────────────────────────

def test_coach_targets_the_viewed_branch_and_nothing_otherwise() -> None:
    """The coach rewrites a branch's knowledge base. Its old last-resort fallback was
    branch 1, so a coaching turn started from an all-branches view edited Indonesia's KB."""
    from app.api._routes_coach import coach_branch

    assert coach_branch(_req("7")) == 7
    assert coach_branch(_req("")) is None
    assert coach_branch(_req("7", allowed=[3, 7], writable=[3])) is None


async def test_coach_say_refuses_without_a_branch(db_session, monkeypatch) -> None:
    from app.api._routes_coach import coach_say

    _patch_scope(monkeypatch, "app.api._routes_coach", db_session)
    resp = await coach_say(_req(""), request_text="сделай ответы короче")

    assert resp.status_code == 400
    assert (await db_session.execute(text("SELECT count(*) FROM coaching_edit"))).scalar() == 0


# ─── the shape itself must not come back ──────────────────────────────────────

_SCANNED = ("app/api", "app/admin")

# `x[0] if x else 1` — the literal shape that made branch 1 the default tenant.
_INDEX_OR_ONE = re.compile(r"\[\s*0\s*\]\s*if\s+\w+\s+else\s+1\b")
# Indexing a PERMISSION list positionally: which branches a caller may write is a set, and
# its ordering carries no meaning, so element 0 is never the branch anyone is looking at.
_PERMISSION_INDEX = re.compile(
    r"\b(writable|allowed|writable_branch_ids\s*\([^)]*\)|allowed_branch_ids\s*\([^)]*\))"
    r"\s*\[\s*0\s*\]")


def _python_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return sorted(p for d in _SCANNED for p in (root / d).rglob("*.py"))


def _code_statements(path: Path) -> list[tuple[int, str]]:
    """(line, source) per logical statement, with comments and string literals dropped.

    The gate has to read code, not prose: the docstrings that explain what it forbids quote
    the forbidden shape verbatim, and a plain-text grep flags them. Tokens are joined with
    spaces, so the patterns must tolerate `writable [ 0 ]`.
    """
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    with path.open(encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL):
                continue
            if tok.type == tokenize.NEWLINE:
                if buf:
                    out.append((start, " ".join(buf)))
                buf, start = [], 0
                continue
            if not buf:
                start = tok.start[0]
            buf.append(tok.string)
    return out


def test_no_route_resolves_a_branch_by_positional_fallback() -> None:
    """Grep-level regression gate. Every branch-scoped route must resolve its target through
    selected_branch_id / writable_selected_branch_id, which answer None when the view is
    ambiguous. Any reintroduction of `writable[0]` or `... else 1` silently re-aims writes at
    branch 1 (live Indonesia) and would otherwise only be noticed in production data.

    If this fails on a new line: the branch you want is the one on screen. If there isn't
    one, say so — do not pick.
    """
    sources = _python_sources()
    assert sources, "scanned no files — the glob is broken, not the code"

    offenders = [
        f"{path.name}:{lineno}: {code}"
        for path in sources
        for lineno, code in _code_statements(path)
        if _INDEX_OR_ONE.search(code) or _PERMISSION_INDEX.search(code)
    ]

    assert not offenders, "positional branch fallback is back:\n" + "\n".join(offenders)


@pytest.mark.parametrize("bad", [
    "bid = writable[0] if writable else 1",
    "branch_id = branch_ids[0] if branch_ids else 1",
    "return ids[0] if ids else 1",
    "bid = writable[0] if writable else None",
    "branch_id = allowed[0]",
])
def test_the_regression_gate_actually_catches_the_shape(bad: str) -> None:
    """A grep test that matches nothing is a test that passes forever — pin the patterns
    against the exact lines this wave removed."""
    assert _INDEX_OR_ONE.search(bad) or _PERMISSION_INDEX.search(bad)
