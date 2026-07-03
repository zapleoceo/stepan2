"""Knowledge/product repos — thin BranchScoped subclasses; isolation stays in the base."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import KnowledgeDoc, Product
from app.adapters.db.repository import BranchScoped


class KnowledgeRepo(BranchScoped[KnowledgeDoc]):
    """Knowledge docs of one branch (persona / faq / market_facts / stories)."""

    model = KnowledgeDoc

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        super().__init__(session, branch_id)

    async def by_slug(self, slug: str) -> KnowledgeDoc | None:
        """Branch-scoped slug lookup — never leaks another branch's doc."""
        q = self._q().where(KnowledgeDoc.slug == slug)
        return (await self.session.exec(q)).first()

    async def all(self) -> list[KnowledgeDoc]:
        """Every doc of the branch (persona + playbooks + stories) for the prompt block."""
        q = self._q().order_by(KnowledgeDoc.slug)  # type: ignore[arg-type]
        return list((await self.session.exec(q)).all())


class ProductRepo(BranchScoped[Product]):
    """Product cards of one branch — the only source of price/details in answers."""

    model = Product

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        super().__init__(session, branch_id)

    async def by_slug(self, slug: str) -> Product | None:
        """Branch-scoped slug lookup — never leaks another branch's product."""
        q = self._q().where(Product.slug == slug)
        return (await self.session.exec(q)).first()

    async def active(self) -> list[Product]:
        """Active products of the branch, ordered for prompt assembly."""
        q = self._q().where(Product.is_active.is_(True)).order_by(  # type: ignore[union-attr]
            Product.sort_order, Product.id
        )
        return list((await self.session.exec(q)).all())
