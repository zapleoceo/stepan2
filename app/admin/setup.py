"""SQLAdmin ModelViews + mount helper.

ChannelSession (holds secret_enc) is intentionally absent.
Every view has: sensible column_list, formatters for long text in list,
TextAreaField for multi-line content, explicit form field order.
"""
from __future__ import annotations

import os
from typing import Any

from sqladmin import Admin, ModelView
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from wtforms import TextAreaField

from app.adapters.db.models import (
    Branch,
    Channel,
    KnowledgeDoc,
    Lead,
    ManagerAlert,
    Membership,
    Outbox,
    Product,
    User,
)

_TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


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


class BranchAdmin(ModelView, model=Branch):
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
        "id": "ID", "name": "Name", "lang": "Lang",
        "tz_offset_h": "UTC offset (h)", "is_active": "Active", "created_at": "Created",
    }
    form_columns = ["name", "lang", "tz_offset_h", "is_active"]
    page_size = 25


class ChannelAdmin(ModelView, model=Channel):
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


class KnowledgeDocAdmin(ModelView, model=KnowledgeDoc):
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


class ProductAdmin(ModelView, model=Product):
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


class LeadAdmin(ModelView, model=Lead):
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


class OutboxAdmin(ModelView, model=Outbox):
    """Read-only view for monitoring the outgoing message queue."""
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


class ManagerAlertAdmin(ModelView, model=ManagerAlert):
    """Read-only view for monitoring manager handoff alerts."""
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
    OutboxAdmin,
    ManagerAlertAdmin,
]


def mount_admin(app: Starlette, engine: AsyncEngine) -> Admin:
    """Register the SQLAdmin dashboard. No DB I/O here."""
    admin = Admin(app, engine=engine, title="Stepan2 Admin", templates_dir=_TEMPLATES)
    for view in _VIEWS:
        admin.add_view(view)
    return admin
