"""Sales-sim: real reply path drives a sandbox, isolated from the worker and from IG."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import func, select  # noqa: E402

from app.adapters.db.models import Branch, Channel, Lead, Message, Outbox  # noqa: E402
from app.modules.conversation.sim import SimService  # noqa: E402


class _FakeLLM:
    """Returns a fixed decision JSON — stands in for the broker so no network/cost."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        raw = ('{"reply":"Halo! Vibe Coding 13 juta, cicilan bisa.","stage":"qualifying",'
               '"jobs":[],"pains":[],"gains":[]}')
        return raw, {"model": "cerebras/gpt-oss-120b", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def test_sim_say_uses_real_path_and_stays_sandboxed(db_session) -> None:
    bid = await _branch(db_session)
    out = await SimService(db_session, _FakeLLM()).say(bid, "s1", "berapa harga vibe coding?")
    assert out["ok"] and "13 juta" in out["reply"] and out["stage"] == "qualifying"

    # sandbox isolation — worker never touches it, nothing can be sent
    ch = (await db_session.execute(
        select(Channel).where(Channel.branch_id == bid))).scalars().first()
    assert ch.is_active is False                       # ingest skips inactive channel
    lead = (await db_session.execute(
        select(Lead).where(Lead.branch_id == bid))).scalars().first()
    assert lead.agent_enabled is False                 # reply_pending skips it
    outbox_n = (await db_session.execute(
        select(func.count()).select_from(Outbox))).scalar()
    assert outbox_n == 0                                # send_outbox has nothing → no IG send

    # the turn is persisted as messages (1 in + 1 out) so context accrues
    msgs = (await db_session.execute(
        select(Message).where(Message.thread_id == out["thread_id"]))).scalars().all()
    assert sorted(m.direction for m in msgs) == ["in", "out"]


async def test_sim_multiturn_and_reset(db_session) -> None:
    svc = SimService(db_session, _FakeLLM())
    bid = await _branch(db_session)
    await svc.say(bid, "s2", "halo")
    r2 = await svc.say(bid, "s2", "ada diskon?")
    tid = r2["thread_id"]
    n = (await db_session.execute(
        select(func.count()).select_from(Message).where(Message.thread_id == tid))).scalar()
    assert n == 4  # two turns → 2 in + 2 out

    await svc.reset(bid, "s2")
    n2 = (await db_session.execute(
        select(func.count()).select_from(Message).where(Message.thread_id == tid))).scalar()
    assert n2 == 0  # conversation wiped
