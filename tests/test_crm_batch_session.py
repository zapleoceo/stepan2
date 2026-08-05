"""One CRM connection per pull pass, not one per lead.

The MCP handshake measured 7.5s against the live CRM and was paid per lead out of a 25s
budget, so a third of every read went on reconnecting to a server we had just finished
talking to — and the reads timed out with nothing to show. `_list_missed` had paged inside a
single session for exactly this reason since it was written; the lead loop never did.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())


from app.adapters.crm_mcp import CrmMcpReader  # noqa: E402
from app.adapters.mcp_client import McpUnavailable  # noqa: E402
from app.modules.crm.gate import CrmReaderPort  # noqa: E402

_URL = "https://crm.example/mcp?token=x"


class _Recorder:
    """Counts how many sessions were opened."""

    def __init__(self, boom: Exception | None = None) -> None:
        self.opened, self.boom = 0, boom

    def __call__(self, url: str, *, timeout_s: float):  # noqa: ANN204, ARG002
        rec = self

        class _Ctx:
            async def __aenter__(self):  # noqa: ANN204
                if rec.boom:
                    raise rec.boom
                rec.opened += 1
                return object()

            async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
                return False

        return _Ctx()


async def test_a_pass_over_many_leads_opens_one_session(monkeypatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr("app.adapters.crm_mcp.mcp_session", rec)
    r = CrmMcpReader("jakarta")
    monkeypatch.setattr(r, "_exchange", lambda s, phone: _ok())

    async with r.batch(_URL):
        for phone in ("+621", "+622", "+623", "+624", "+625"):
            await r.get_state(_URL, "", phone)

    assert rec.opened == 1, f"{rec.opened} handshakes for five leads"


async def test_without_a_batch_each_read_still_opens_its_own(monkeypatch) -> None:
    """The gate also reads live, one lead at a time, outside any pass."""
    rec = _Recorder()
    monkeypatch.setattr("app.adapters.crm_mcp.mcp_session", rec)
    r = CrmMcpReader("jakarta")
    monkeypatch.setattr(r, "_exchange", lambda s, phone: _ok())

    for phone in ("+621", "+622"):
        await r.get_state(_URL, "", phone)

    assert rec.opened == 2


async def test_a_refused_shared_session_falls_back_instead_of_skipping_everyone(
    monkeypatch,
) -> None:
    """Fail-open, like every other CRM path: one refused connection must not turn into a
    pass that refreshes nobody."""
    rec = _Recorder(boom=McpUnavailable("no route"))
    monkeypatch.setattr("app.adapters.crm_mcp.mcp_session", rec)
    r = CrmMcpReader("jakarta")

    ran = False
    async with r.batch(_URL):
        ran = True
    assert ran, "the pass was skipped entirely"
    assert r._shared is None, "a dead session must not be left behind for later reads"


async def test_the_port_default_is_a_no_op_so_callers_need_no_capability_check() -> None:
    """A REST reader gains nothing from batching and must not have to know the method exists.
    The pull loop calls it unconditionally."""
    ran = False
    async with CrmReaderPort().batch(_URL):
        ran = True
    assert ran


async def _ok() -> dict:
    return {"exists": False, "source": "mcp"}
