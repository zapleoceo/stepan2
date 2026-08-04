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

import ast
import contextlib
import os
from pathlib import Path

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    AppSetting,
    Branch,
    KnowledgeDoc,
    KnowledgeRevision,
    Product,
)
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
    assert writable_selected_branch_id(_req("7")) == 7
    assert writable_selected_branch_id(_req("")) is None
    assert writable_selected_branch_id(_req("7", allowed=[3, 7], writable=[3])) is None


async def test_coach_say_refuses_without_a_branch(db_session, monkeypatch) -> None:
    from app.api._routes_coach import coach_say

    _patch_scope(monkeypatch, "app.api._routes_coach", db_session)
    resp = await coach_say(_req(""), request_text="сделай ответы короче")

    assert resp.status_code == 400
    assert (await db_session.execute(text("SELECT count(*) FROM coaching_edit"))).scalar() == 0


# ─── revision restore ─────────────────────────────────────────────────────────

async def test_restore_refuses_a_revision_with_no_branch_even_for_the_owner(db_session) -> None:
    """A branch-less revision is not "restorable by anyone with write-anywhere" — restoring
    one runs `UPDATE ... WHERE slug=:slug` with no branch predicate at all, so every tenant's
    doc of that slug is overwritten, live Indonesia included. writable is None for a
    super_admin (the owner's normal state) and that used to skip the ownership check entirely.
    """
    from app.modules.knowledge.history import restore_revision_scoped

    await _branches(db_session)
    db_session.add(KnowledgeDoc(branch_id=1, slug="faq", content="INDONESIA_LIVE"))
    db_session.add(KnowledgeDoc(branch_id=7, slug="faq", content="TEST_LIVE"))
    rev = KnowledgeRevision(branch_id=None, entity_type="doc", slug="faq",
                            old_content="", new_content="RESTORED_EVERYWHERE", actor="me")
    db_session.add(rev)
    await db_session.flush()

    status, out = await restore_revision_scoped(
        db_session, rev.id, writable=None, actor="me")

    assert (status, out) == ("forbidden", None)
    live = dict((await db_session.execute(
        text("SELECT branch_id, content FROM knowledge_doc WHERE slug='faq'"))).all())
    assert live == {1: "INDONESIA_LIVE", 7: "TEST_LIVE"}


async def test_restore_reads_back_the_branch_it_restored_into(db_session, monkeypatch) -> None:
    """slug is unique PER BRANCH, so the post-restore `SELECT ... WHERE slug=:s` returned an
    arbitrary tenant's product and rendered its id into the editor's save/delete buttons —
    handing the operator another branch's row as the live edit target."""
    from app.api._routes_products import products_restore

    await _branches(db_session)
    indonesia = Product(branch_id=1, slug="vibe", title="ID", content="INDONESIA_LIVE")
    test_branch = Product(branch_id=7, slug="vibe", title="T7", content="old")
    db_session.add(indonesia)
    db_session.add(test_branch)
    rev = KnowledgeRevision(branch_id=7, entity_type="product", slug="vibe",
                            old_content="old", new_content="TEST_RESTORED", actor="me")
    db_session.add(rev)
    await db_session.flush()
    _patch_scope(monkeypatch, "app.api._routes_products", db_session)

    body = (await products_restore(_req("7"), rev_id=rev.id)).body.decode()

    assert f"/ui/products/{test_branch.id}/delete" in body
    assert f"/ui/products/{indonesia.id}/delete" not in body
    assert "TEST_RESTORED" in body
    assert (await _product_content(db_session, 1, "vibe")) == "INDONESIA_LIVE"


async def _product_content(session, branch_id: int, slug: str) -> str | None:
    row = (await session.execute(
        text("SELECT content FROM product WHERE branch_id=:b AND slug=:s"),
        {"b": branch_id, "s": slug})).first()
    return row[0] if row else None


# ─── the shape itself must not come back ──────────────────────────────────────
#
# This gate reads the AST, not the text. The first version matched identifiers — it looked
# for the literal words `writable` and `allowed` next to `[0]` — and so it missed
# `w = writable_branch_ids(request); if w: return w[0]`, which is exactly the code this wave
# deleted from _routes_personas.py. A gate that only catches the variable names the author
# happened to use guards nothing; renaming the local defeats it. What is actually forbidden
# is a SHAPE: taking element 0 of a list of branch ids.

_SCANNED = ("app/api", "app/admin")

# Functions that answer with a LIST of branch ids. Which branches a caller may see or write
# is a set — its ordering is whatever the DB or the cookie happened to produce — so element 0
# is never the branch anyone is looking at.
_BRANCH_LIST_CALLS = frozenset({
    "writable_branch_ids", "allowed_branch_ids", "branch_ids_from_request"})


def _python_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return sorted(p for d in _SCANNED for p in (root / d).rglob("*.py"))


def _callee_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_branch_list_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _callee_name(node.func) in _BRANCH_LIST_CALLS


def _scopes(tree: ast.AST) -> list[list[ast.AST]]:
    """The nodes of each function body, and of the module outside any function.

    Per function, because both the taint and the exemption below are statements about what
    one function has established — a `len(x) == 1` proof three functions away proves nothing
    here.
    """
    out: list[list[ast.AST]] = []

    def visit(node: ast.AST, own: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                nested: list[ast.AST] = []
                visit(child, nested)
                out.append(nested)
            else:
                own.append(child)
                visit(child, own)

    module: list[ast.AST] = []
    visit(tree, module)
    out.append(module)
    return out


def _branch_list_names(nodes: list[ast.AST]) -> set[str]:
    """Every name in this scope that holds a list of branch ids, however it was spelled.

    Iterated to a fixed point so aliasing (`w = writable_branch_ids(r)` then `ids = w`) is
    tracked too — the whole point is that the NAME must not matter.
    """
    names: set[str] = set()
    while True:
        before = len(names)
        for node in nodes:
            if isinstance(node, ast.Assign):
                value, targets = node.value, node.targets
            elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
                value, targets = node.value, [node.target]
            else:
                continue
            if not (_is_branch_list_call(value)
                    or (isinstance(value, ast.Name) and value.id in names)):
                continue
            names.update(t.id for t in targets if isinstance(t, ast.Name))
        if len(names) == before:
            return names


def _proven_single(nodes: list[ast.AST]) -> set[str]:
    """Names this scope has compared against `len(...) == 1` (or `!= 1`).

    That comparison is what separates unwrapping from picking. `selected_branch_id`,
    `_ad_editor_data` and `ad_product_map` all index element 0 legitimately — each first
    proves the list holds exactly one branch, so element 0 IS the branch on screen. The bug
    shape never does: `writable[0] if writable else 1` tests truthiness, which says the list
    is non-empty and nothing whatever about which tenant is on screen.

    Scope-wide rather than flow-sensitive, so a function that proves `len(a) == 1` and then
    indexes an unrelated `b[0]` slips through. That is the deliberate limit — the alternative
    is a dataflow engine in a test file, and every real instance of this bug had no length
    check anywhere in the function.
    """
    proven: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Compare) or not isinstance(node.ops[0], ast.Eq | ast.NotEq):
            continue
        left, right = node.left, node.comparators[0]
        if (isinstance(left, ast.Call) and _callee_name(left.func) == "len" and left.args
                and isinstance(left.args[0], ast.Name)
                and isinstance(right, ast.Constant) and right.value == 1):
            proven.add(left.args[0].id)
    return proven


def _is_zero_index(node: ast.AST) -> bool:
    return (isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant) and node.slice.value == 0)


def _offences(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for nodes in _scopes(tree):
        names = _branch_list_names(nodes) - _proven_single(nodes)
        for node in nodes:
            # Shape A: element 0 of a branch-id list, under any name, in any statement form,
            # with nothing establishing that the list holds only one branch.
            if _is_zero_index(node):
                target = node.value
                if _is_branch_list_call(target) or (
                        isinstance(target, ast.Name) and target.id in names):
                    found.append((node.lineno, ast.unparse(node)))
            # Shape B: `<anything>[0] if <anything> else <int>` — "and if there is no list,
            # tenant number 1" — regardless of what the list is called or where it came from.
            # This is the literal line that sent every super_admin's write to Indonesia.
            if (isinstance(node, ast.IfExp) and _is_zero_index(node.body)
                    and isinstance(node.orelse, ast.Constant)
                    and isinstance(node.orelse.value, int)
                    and not isinstance(node.orelse.value, bool)):
                found.append((node.lineno, ast.unparse(node)))
    return found


def test_no_route_resolves_a_branch_by_positional_fallback() -> None:
    """Regression gate. Every branch-scoped route must resolve its target through
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
        for lineno, code in _offences(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert not offenders, "positional branch fallback is back:\n" + "\n".join(offenders)


@pytest.mark.parametrize("bad", [
    # the exact statement removed from _routes_admin.settings_save_by_key
    "writable = writable_branch_ids(request)\nbid = writable[0] if writable else 1\n",
    # the exact shape removed from _routes_personas._acting_branch — the one the
    # identifier-matching version of this gate did NOT catch
    "def f(request):\n"
    "    w = writable_branch_ids(request)\n"
    "    if w:\n"
    "        return w[0]\n"
    "    return None\n",
    # renamed local, and one alias deep — the name must not be what saves it
    "def f(request):\n"
    "    perms = allowed_branch_ids(request)\n"
    "    mine = perms\n"
    "    return mine[0]\n",
    # no local at all
    "branch_id = branch_ids_from_request(request)[0]",
    # a list this gate has never heard of, defaulting to tenant 1
    "bid = whatever_ids[0] if whatever_ids else 1",
    "return ids[0] if ids else 1",
    # the old fallback with None instead of 1 — still a positional pick
    "def f(request):\n"
    "    writable = writable_branch_ids(request)\n"
    "    return writable[0] if writable else None\n",
])
def test_the_regression_gate_actually_catches_the_shape(bad: str) -> None:
    """A gate that matches nothing is a test that passes forever — pin it against the exact
    code this wave removed, plus the renames that used to walk straight through it."""
    assert _offences(ast.parse(bad))


def test_the_gate_still_allows_unwrapping_a_proven_single_branch() -> None:
    """selected_branch_id's own body must stay legal: indexing after `len(...) == 1` is
    unwrapping, not picking. Without this the gate would force the resolver into a contortion
    and the next author would delete the gate instead."""
    ternary = ("def f(request):\n"
               "    view = branch_ids_from_request(request)\n"
               "    return view[0] if view and len(view) == 1 else None\n")
    early_return = ("def f(request):\n"
                    "    branch_ids = branch_ids_from_request(request)\n"
                    "    if not branch_ids or len(branch_ids) != 1:\n"
                    "        return None\n"
                    "    return branch_ids[0]\n")
    assert not _offences(ast.parse(ternary))
    assert not _offences(ast.parse(early_return))
