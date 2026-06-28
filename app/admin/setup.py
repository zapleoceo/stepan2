"""SQLAdmin ModelViews + mount helper.

BranchScopedModelView is the DRY base for every model that carries branch_id:
  - list_query / count_query filter by the branch cookie when one is selected
  - no branch selected → super-admin sees all data across branches

ChannelSession (holds secret_enc) is intentionally absent.
"""
from __future__ import annotations

import os
from typing import Any

from sqladmin import Admin, ModelView
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.expression import Select, select
from starlette.applications import Starlette
from starlette.requests import Request
from wtforms import TextAreaField
from wtforms.widgets.core import Input as _WFInput

from app.adapters.db.models import (
    AppSetting,
    Branch,
    Channel,
    CoachingNote,
    KnowledgeDoc,
    Lead,
    ManagerAlert,
    Membership,
    Outbox,
    Product,
    User,
)

from ._branch import branch_ids_from_request

# WTForms ≥3.2 added validation_attrs on specific Input subclasses but not on
# the base Input class, while Input.__call__ references self.validation_attrs.
# sqladmin's BooleanInputWidget(Input) hits AttributeError on any boolean field.
if not hasattr(_WFInput, "validation_attrs"):
    _WFInput.validation_attrs = []  # type: ignore[attr-defined]

_TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


# ── shared helpers ────────────────────────────────────────────────────────────

def _trunc(text: str | None, n: int = 70) -> str:
    if not text:
        return ""
    return text[:n] + "…" if len(text) > n else text


_TEXTAREA: dict[str, Any] = {
    "rows": 18,
    "style": (
        "min-height:280px;resize:vertical;"
        "font-family:ui-monospace,monospace;"
        "font-size:.875rem;line-height:1.75"
    ),
}


# ── base class: branch-scoped list / count ────────────────────────────────────

class BranchScopedModelView(ModelView):
    """DRY base for models with a branch_id FK.

    When the sidebar branch filter cookie is set, list and count queries
    automatically scope to that branch. No branch selected = show all.
    Subclasses only need to set model/column/form config; no per-class filtering.
    """

    def list_query(self, request: Request) -> Select:
        stmt = select(self.model)
        bids = branch_ids_from_request(request)
        if bids is not None:
            stmt = stmt.where(self.model.branch_id.in_(bids))  # type: ignore[attr-defined]
        return stmt

    def count_query(self, request: Request) -> Select:
        stmt = select(func.count(self.pk_columns[0])).select_from(self.model)
        bids = branch_ids_from_request(request)
        if bids is not None:
            stmt = stmt.where(self.model.branch_id.in_(bids))  # type: ignore[attr-defined]
        return stmt


# ── model views ───────────────────────────────────────────────────────────────

class BranchAdmin(ModelView, model=Branch):
    """Branches are the root; no branch_id — plain ModelView."""
    name = "Branch"
    name_plural = "Branches"
    icon = "fa-solid fa-building"
    column_list = [Branch.id, Branch.name, Branch.lang, Branch.tz_offset_h, Branch.is_active]
    column_details_list = [
        Branch.id, Branch.name, Branch.lang,
        Branch.tz_offset_h, Branch.is_active, Branch.created_at,
    ]
    column_searchable_list = [Branch.name]
    column_sortable_list = [Branch.id, Branch.name]
    column_labels = {
        "id": "ID", "name": "Name", "lang": "Language code",
        "tz_offset_h": "UTC offset (h)", "is_active": "Active", "created_at": "Created",
    }
    form_columns = ["name", "lang", "tz_offset_h", "is_active"]
    page_size = 25


class ChannelAdmin(BranchScopedModelView, model=Channel):
    name = "Channel"
    name_plural = "Channels"
    icon = "fa-solid fa-tower-broadcast"
    column_list = [
        Channel.id, Channel.branch_id, Channel.kind,
        Channel.handle, Channel.account_id, Channel.is_active,
    ]
    column_sortable_list = [Channel.id, Channel.branch_id, Channel.kind]
    column_labels = {
        "id": "ID", "branch_id": "Branch", "kind": "Kind",
        "handle": "Handle / Username", "account_id": "External Account ID", "is_active": "Active",
    }
    form_columns = ["branch_id", "kind", "handle", "account_id", "is_active"]
    page_size = 25


class KnowledgeDocAdmin(BranchScopedModelView, model=KnowledgeDoc):
    name = "Knowledge Doc"
    name_plural = "Knowledge Docs"
    icon = "fa-solid fa-book-open"
    column_list = [
        KnowledgeDoc.id, KnowledgeDoc.branch_id,
        KnowledgeDoc.slug, KnowledgeDoc.title, KnowledgeDoc.content,
    ]
    column_details_list = [
        KnowledgeDoc.id, KnowledgeDoc.branch_id,
        KnowledgeDoc.slug, KnowledgeDoc.title, KnowledgeDoc.content,
    ]
    column_searchable_list = [KnowledgeDoc.slug, KnowledgeDoc.title]
    column_sortable_list = [KnowledgeDoc.id, KnowledgeDoc.branch_id, KnowledgeDoc.slug]
    column_labels = {
        "id": "ID", "branch_id": "Branch",
        "slug": "Slug", "title": "Title", "content": "Content",
    }
    column_formatters = {KnowledgeDoc.content: lambda m, a: _trunc(m.content)}
    form_overrides = {"content": TextAreaField}
    form_widget_args = {"content": _TEXTAREA}
    form_columns = ["branch_id", "slug", "title", "content"]
    page_size = 25
    can_export = True


class ProductAdmin(BranchScopedModelView, model=Product):
    name = "Product"
    name_plural = "Products"
    icon = "fa-solid fa-tag"
    column_list = [
        Product.id, Product.branch_id, Product.slug, Product.title,
        Product.content, Product.is_active, Product.sort_order,
    ]
    column_details_list = [
        Product.id, Product.branch_id, Product.slug, Product.title,
        Product.content, Product.is_active, Product.sort_order,
    ]
    column_searchable_list = [Product.slug, Product.title]
    column_sortable_list = [Product.id, Product.branch_id, Product.sort_order, Product.is_active]
    column_labels = {
        "id": "ID", "branch_id": "Branch", "slug": "Slug", "title": "Title",
        "content": "Description", "is_active": "Active", "sort_order": "Sort",
    }
    column_formatters = {Product.content: lambda m, a: _trunc(m.content)}
    form_overrides = {"content": TextAreaField}
    form_widget_args = {"content": _TEXTAREA}
    form_columns = ["branch_id", "slug", "title", "content", "is_active", "sort_order"]
    page_size = 25
    can_export = True


class LeadAdmin(BranchScopedModelView, model=Lead):
    name = "Lead"
    name_plural = "Leads"
    icon = "fa-solid fa-user-tag"
    column_list = [
        Lead.id, Lead.branch_id, Lead.display_name,
        Lead.phone_e164, Lead.email, Lead.stage, Lead.created_at,
    ]
    column_details_list = [
        Lead.id, Lead.branch_id, Lead.display_name,
        Lead.phone_e164, Lead.email, Lead.stage, Lead.ready_subtype, Lead.created_at,
    ]
    column_searchable_list = [Lead.display_name, Lead.phone_e164]
    column_sortable_list = [Lead.id, Lead.branch_id, Lead.stage, Lead.created_at]
    column_labels = {
        "id": "ID", "branch_id": "Branch", "display_name": "Name",
        "phone_e164": "Phone (E.164)", "email": "Email",
        "stage": "Stage", "ready_subtype": "Subtype", "created_at": "Created",
    }
    form_columns = ["branch_id", "display_name", "phone_e164", "email", "stage", "ready_subtype"]
    page_size = 25
    can_export = True


class UserAdmin(ModelView, model=User):
    """Users are platform-wide — no branch_id, plain ModelView."""
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.telegram_id, User.name, User.created_at]
    column_searchable_list = [User.name]
    column_sortable_list = [User.id, User.created_at]
    column_labels = {
        "id": "ID", "telegram_id": "Telegram ID", "name": "Name", "created_at": "Created",
    }
    form_columns = ["telegram_id", "name"]
    page_size = 50


class MembershipAdmin(ModelView, model=Membership):
    """Memberships bridge users ↔ branches. branch_id is nullable (NULL = platform).
    Plain ModelView: the branch filter cookie doesn't apply here."""
    name = "Membership"
    name_plural = "Memberships"
    icon = "fa-solid fa-id-badge"
    column_list = [Membership.id, Membership.user_id, Membership.branch_id, Membership.role]
    column_sortable_list = [Membership.id, Membership.user_id, Membership.branch_id]
    column_labels = {
        "id": "ID", "user_id": "User ID",
        "branch_id": "Branch (NULL = platform-wide)", "role": "Role",
    }
    form_columns = ["user_id", "branch_id", "role"]
    page_size = 50


class OutboxAdmin(BranchScopedModelView, model=Outbox):
    """Read-only queue monitor."""
    name = "Outbox"
    name_plural = "Outbox"
    icon = "fa-solid fa-paper-plane"
    can_create = False
    can_edit = False
    column_list = [
        Outbox.id, Outbox.branch_id, Outbox.status, Outbox.source,
        Outbox.text, Outbox.scheduled_at, Outbox.sent_at, Outbox.error,
    ]
    column_sortable_list = [Outbox.id, Outbox.status, Outbox.scheduled_at, Outbox.sent_at]
    column_labels = {
        "id": "ID", "branch_id": "Branch", "thread_id": "Thread",
        "text": "Text", "source": "Source", "status": "Status",
        "scheduled_at": "Scheduled", "sent_at": "Sent", "error": "Error",
    }
    column_formatters = {
        Outbox.text: lambda m, a: _trunc(m.text, 60),
        Outbox.error: lambda m, a: _trunc(m.error, 60) if m.error else "",
    }
    page_size = 50


class CoachingNoteAdmin(BranchScopedModelView, model=CoachingNote):
    """Bot coaching directives — active manager notes are injected into the prompt."""
    name = "Coaching Note"
    name_plural = "Coaching Notes"
    icon = "fa-solid fa-chalkboard-user"
    column_list = [
        CoachingNote.id, CoachingNote.branch_id, CoachingNote.role,
        CoachingNote.active, CoachingNote.text, CoachingNote.added_by, CoachingNote.created_at,
    ]
    column_details_list = [
        CoachingNote.id, CoachingNote.branch_id, CoachingNote.role,
        CoachingNote.active, CoachingNote.text, CoachingNote.added_by, CoachingNote.created_at,
    ]
    column_searchable_list = [CoachingNote.text]
    column_sortable_list = [CoachingNote.id, CoachingNote.branch_id, CoachingNote.active]
    column_labels = {
        "id": "ID", "branch_id": "Branch", "role": "Role (manager|stepan)",
        "active": "Active", "text": "Text", "added_by": "Added by", "created_at": "Created",
    }
    column_formatters = {CoachingNote.text: lambda m, a: _trunc(m.text)}
    form_overrides = {"text": TextAreaField}
    form_widget_args = {"text": {**_TEXTAREA, "rows": 6}}
    form_columns = ["branch_id", "role", "text", "active", "added_by"]
    page_size = 50


class AppSettingAdmin(ModelView, model=AppSetting):
    """Runtime settings — branch_id=NULL means platform-wide."""
    name = "Setting"
    name_plural = "Settings"
    icon = "fa-solid fa-sliders"
    column_list = [AppSetting.id, AppSetting.branch_id, AppSetting.key, AppSetting.value]
    column_details_list = [AppSetting.id, AppSetting.branch_id, AppSetting.key, AppSetting.value]
    column_searchable_list = [AppSetting.key]
    column_sortable_list = [AppSetting.id, AppSetting.branch_id, AppSetting.key]
    column_labels = {
        "id": "ID", "branch_id": "Branch (NULL=platform)", "key": "Key", "value": "Value",
    }
    form_columns = ["branch_id", "key", "value"]
    page_size = 50


class ManagerAlertAdmin(BranchScopedModelView, model=ManagerAlert):
    """Read-only handoff alert monitor."""
    name = "Manager Alert"
    name_plural = "Manager Alerts"
    icon = "fa-solid fa-bell"
    can_create = False
    can_edit = False
    column_list = [
        ManagerAlert.id, ManagerAlert.branch_id, ManagerAlert.lead_id,
        ManagerAlert.kind, ManagerAlert.actor, ManagerAlert.lead_phone,
        ManagerAlert.synced_at, ManagerAlert.created_at,
    ]
    column_sortable_list = [ManagerAlert.id, ManagerAlert.branch_id, ManagerAlert.created_at]
    column_labels = {
        "id": "ID", "branch_id": "Branch", "lead_id": "Lead",
        "kind": "Kind", "actor": "Actor", "lead_phone": "Phone",
        "summary_en": "Summary (EN)", "summary_ru": "Summary (RU)",
        "synced_at": "Synced", "created_at": "Created",
    }
    page_size = 50


_VIEWS: list[type[ModelView]] = [
    BranchAdmin,
    ChannelAdmin,
    KnowledgeDocAdmin,
    ProductAdmin,
    LeadAdmin,
    UserAdmin,
    MembershipAdmin,
    CoachingNoteAdmin,
    AppSettingAdmin,
    OutboxAdmin,
    ManagerAlertAdmin,
]


def mount_admin(app: Starlette, engine: AsyncEngine) -> Admin:
    """Register the SQLAdmin dashboard. No DB I/O here."""
    admin = Admin(app, engine=engine, title="Stepan2 Admin", templates_dir=_TEMPLATES)
    for view in _VIEWS:
        admin.add_view(view)
    return admin
