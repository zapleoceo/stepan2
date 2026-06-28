"""SQLAdmin ModelViews + mount helper. Only non-secret tables are registered:
ChannelSession (holds secret_enc) is intentionally absent from the dashboard."""
from __future__ import annotations

from sqladmin import Admin, ModelView
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette

from app.adapters.db.models import (
    Branch,
    Channel,
    KnowledgeDoc,
    Lead,
    Membership,
    Product,
    User,
)
from app.config import settings


class BranchAdmin(ModelView, model=Branch):
    name = "Branch"
    name_plural = "Branches"
    icon = "fa-solid fa-building"
    column_list = [Branch.id, Branch.name, Branch.lang, Branch.is_active, Branch.created_at]
    column_searchable_list = [Branch.name]
    column_sortable_list = [Branch.id, Branch.name, Branch.created_at]


class ChannelAdmin(ModelView, model=Channel):
    name = "Channel"
    name_plural = "Channels"
    icon = "fa-solid fa-tower-broadcast"
    column_list = [
        Channel.id,
        Channel.branch_id,
        Channel.kind,
        Channel.handle,
        Channel.account_id,
        Channel.is_active,
    ]
    column_sortable_list = [Channel.id, Channel.branch_id]


class KnowledgeDocAdmin(ModelView, model=KnowledgeDoc):
    name = "Knowledge Doc"
    name_plural = "Knowledge Docs"
    icon = "fa-solid fa-book"
    column_list = [KnowledgeDoc.id, KnowledgeDoc.branch_id, KnowledgeDoc.slug, KnowledgeDoc.title]
    column_searchable_list = [KnowledgeDoc.slug, KnowledgeDoc.title]
    column_sortable_list = [KnowledgeDoc.id, KnowledgeDoc.branch_id, KnowledgeDoc.slug]


class ProductAdmin(ModelView, model=Product):
    name = "Product"
    name_plural = "Products"
    icon = "fa-solid fa-tag"
    column_list = [
        Product.id,
        Product.branch_id,
        Product.slug,
        Product.title,
        Product.is_active,
        Product.sort_order,
    ]
    column_searchable_list = [Product.slug, Product.title]
    column_sortable_list = [Product.id, Product.branch_id, Product.sort_order]


class LeadAdmin(ModelView, model=Lead):
    name = "Lead"
    name_plural = "Leads"
    icon = "fa-solid fa-user-tag"
    column_list = [
        Lead.id,
        Lead.branch_id,
        Lead.display_name,
        Lead.phone_e164,
        Lead.stage,
        Lead.created_at,
    ]
    column_searchable_list = [Lead.display_name, Lead.phone_e164]
    column_sortable_list = [Lead.id, Lead.branch_id, Lead.created_at]


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-user"
    column_list = [User.id, User.telegram_id, User.name, User.created_at]
    column_searchable_list = [User.name]
    column_sortable_list = [User.id, User.created_at]


class MembershipAdmin(ModelView, model=Membership):
    name = "Membership"
    name_plural = "Memberships"
    icon = "fa-solid fa-id-badge"
    column_list = [Membership.id, Membership.user_id, Membership.branch_id, Membership.role]
    column_sortable_list = [Membership.id, Membership.user_id, Membership.branch_id]


_VIEWS: list[type[ModelView]] = [
    BranchAdmin,
    ChannelAdmin,
    KnowledgeDocAdmin,
    ProductAdmin,
    LeadAdmin,
    UserAdmin,
    MembershipAdmin,
]


def mount_admin(app: Starlette, engine: AsyncEngine) -> Admin:
    """Register the SQLAdmin dashboard and its views on the app. No DB I/O here."""
    admin = Admin(app, engine=engine, title="Stepan2 Admin", secret_key=settings().secret_key)
    for view in _VIEWS:
        admin.add_view(view)
    return admin
