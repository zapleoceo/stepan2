"""Branch-scoped repository — the single point of tenant isolation (DRY).

Every domain query goes through a `BranchScoped` repo bound to one branch_id; modules
never write `branch_id` filters by hand. Swapping the storage = swapping this class.
"""
from __future__ import annotations

from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


class BranchScoped[T: SQLModel]:
    """Repository bound to a branch. All reads filter by branch_id; writes force it.

    Subclass with `model = SomeTable`, or instantiate `BranchScoped(session, branch_id,
    model=SomeTable)`. Isolation lives ONLY here."""

    model: type[T]

    def __init__(self, session: AsyncSession, branch_id: int,
                 model: type[T] | None = None) -> None:
        self.session = session
        self.branch_id = branch_id
        if model is not None:
            self.model = model

    def _q(self) -> select:  # type: ignore[type-arg]
        return select(self.model).where(self.model.branch_id == self.branch_id)  # type: ignore[attr-defined]

    async def list(self) -> list[T]:
        return list((await self.session.exec(self._q())).all())

    async def get(self, id_: int) -> T | None:
        row = await self.session.get(self.model, id_)
        if row is None or getattr(row, "branch_id", None) != self.branch_id:
            return None  # чужой филиал не отдаём
        return row

    async def add(self, obj: T) -> T:
        obj.branch_id = self.branch_id  # type: ignore[attr-defined] — принудительно филиал
        self.session.add(obj)
        await self.session.flush()
        return obj
