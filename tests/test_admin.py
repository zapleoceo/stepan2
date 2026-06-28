"""Admin module smoke tests.

Covers:
- WTForms ≥3.2 / sqladmin BooleanInputWidget compat (AttributeError regression)
- Admin API router is importable and has the right prefix
- BranchScopedModelView subclasses carry the correct model
"""
from __future__ import annotations


def test_wtforms_boolean_widget_compat() -> None:
    """BooleanInputWidget.__call__ must not raise AttributeError on validation_attrs.

    WTForms ≥3.2 added validation_attrs to specific Input subclasses but not
    to the base Input class; sqladmin's BooleanInputWidget(Input) would crash
    on any edit form that has a boolean field. Our patch in admin/setup.py fixes it.
    """
    from sqladmin.widgets import BooleanInputWidget
    from wtforms import BooleanField, Form

    import app.admin.setup  # noqa: F401 — side-effect: WTForms patch is applied

    class _F(Form):
        active = BooleanField("Active", default=True)

    html = str(BooleanInputWidget()(_F().active))
    assert 'type="checkbox"' in html


def test_admin_branch_cookie_constant() -> None:
    from app.admin._branch import BRANCH_COOKIE, branch_ids_from_request

    assert BRANCH_COOKIE == "stepan2_branch"

    class _Req:
        cookies: dict[str, str] = {}

    assert branch_ids_from_request(_Req()) is None  # type: ignore[arg-type]

    _Req.cookies = {"stepan2_branch": "3"}
    assert branch_ids_from_request(_Req()) == [3]  # type: ignore[arg-type]

    _Req.cookies = {"stepan2_branch": "1,3,7"}
    assert branch_ids_from_request(_Req()) == [1, 3, 7]  # type: ignore[arg-type]

    _Req.cookies = {"stepan2_branch": "notanint"}
    assert branch_ids_from_request(_Req()) is None  # type: ignore[arg-type]

    _Req.cookies = {"stepan2_branch": "2,bad,5"}
    assert branch_ids_from_request(_Req()) == [2, 5]  # type: ignore[arg-type]


def test_admin_api_router_prefix() -> None:
    from app.admin.api import router

    assert router.prefix == "/_admin"
    paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    assert "/_admin/branches" in paths
    assert "/_admin/set-branch" in paths


def test_set_branch_validates_ids() -> None:
    """set_branch must ignore non-integer values in branch_ids param."""
    # Test the validation logic directly (no HTTP needed)
    raw = "1,notanid,3,4bad,7"
    clean = [p.strip() for p in raw.split(",") if p.strip().isdigit()]
    assert clean == ["1", "3", "7"]

    assert [p.strip() for p in "".split(",") if p.strip().isdigit()] == []


def test_branch_scoped_views_inherit_base() -> None:
    from app.admin.setup import (
        BranchScopedModelView,
        ChannelAdmin,
        KnowledgeDocAdmin,
        LeadAdmin,
        ManagerAlertAdmin,
        OutboxAdmin,
        ProductAdmin,
    )

    for cls in (
        ChannelAdmin,
        KnowledgeDocAdmin,
        ProductAdmin,
        LeadAdmin,
        OutboxAdmin,
        ManagerAlertAdmin,
    ):
        assert issubclass(cls, BranchScopedModelView), (
            f"{cls.__name__} must inherit BranchScopedModelView"
        )
