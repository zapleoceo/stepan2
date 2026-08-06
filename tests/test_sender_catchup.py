"""Recovering the inbound their callback never delivered.

Their side does not retry a failed callback — their own spec says errors are only logged. A
message arriving while we restart is therefore gone: they have it, we never did, and nothing
on either side would ever say so. This sweep is the repair, and these tests pin the two ways
it could quietly fail to be one: fetching the wrong period, and swallowing its own errors so
completely that a broken callback looks like a quiet day.
"""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import select  # noqa: E402

from app.adapters.db.models import SenderInbound  # noqa: E402
from app.modules.sender import catchup  # noqa: E402
from app.modules.sender.catchup import SenderApi  # noqa: E402

# noqa on token: a literal in a fixture, not a credential.
_API = SenderApi(base_url="https://sender.example", token="t",  # noqa: S106
                 project_id="3", branch_id="435")

_NOW = datetime(2026, 8, 5, 10, 30, 0)


def _msg(external_id: str, text: str = "halo") -> dict:
    return {"id": "1", "external_id": external_id, "from": "6281234567890",
            "message": text, "chanel": "whats-app", "project_id": "3",
            "branch_id": "435", "conversation_id": "6281234567890", "chat_id": "9"}


def test_the_window_is_sent_in_their_clock_not_ours() -> None:
    """Their examples carry no timezone. Off by seven hours this fetches a period nobody
    wrote in, finds nothing, and reports success — the failure that hides itself."""
    jakarta = SenderApi(base_url="x", token="t", project_id="3",  # noqa: S106
                        branch_id="435",
                        tz_offset_h=7.0)

    p = catchup.window_params(jakarta, datetime(2026, 8, 5, 3, 0, 0),
                              datetime(2026, 8, 5, 4, 0, 0))

    assert p["date_start"] == "2026-08-05 10:00:00"
    assert p["date_end"] == "2026-08-05 11:00:00"


def test_utc_is_the_default_until_they_say_otherwise() -> None:
    p = catchup.window_params(_API, datetime(2026, 8, 5, 3, 0, 0),
                              datetime(2026, 8, 5, 4, 0, 0))

    assert (p["date_start"], p["date_end"]) == ("2026-08-05 03:00:00", "2026-08-05 04:00:00")


def test_only_inbound_is_asked_for() -> None:
    """An outgoing message is a manager writing. Sweeping those in as if they were lead turns
    is the same mistake type_send exists to prevent on the callback."""
    p = catchup.window_params(_API, _NOW, _NOW)

    assert p["type_send"] == "in"
    assert p["project_id"] == "3"
    assert p["branch_id"] == "435"


def test_the_envelope_is_unwrapped_and_rubbish_is_no_messages() -> None:
    assert catchup.rows_of({"data": [_msg("a")]}) == [_msg("a")]
    assert catchup.rows_of([_msg("a")]) == [_msg("a")]
    # An error page where JSON was expected must be "nothing to rescue", not an exception
    # that takes the safety net down with it.
    assert catchup.rows_of("<html>502</html>") == []
    assert catchup.rows_of({"error": "nope"}) == []
    assert catchup.rows_of({"data": [_msg("a"), "junk", None]}) == [_msg("a")]


async def test_a_message_the_callback_missed_is_stored(db_session, monkeypatch) -> None:  # noqa: ANN001
    async def _fake(api, since, until):  # noqa: ANN001, ANN202, ARG001
        return [_msg("wamid.MISSED", "berapa harganya?")]

    monkeypatch.setattr(catchup, "fetch", _fake)

    rescued = await catchup.sweep(db_session, _API, now=_NOW)

    assert rescued == 1
    rows = (await db_session.execute(select(SenderInbound))).scalars().all()
    assert [(r.external_id, r.arrived_via) for r in rows] == [("wamid.MISSED", "catchup")]


async def test_what_the_callback_already_delivered_is_not_counted_twice(
    db_session, monkeypatch,  # noqa: ANN001
) -> None:
    """Every sweep re-covers the previous one's ground on purpose, so overlap is the normal
    case, not the exception. Counting it as rescued would invent an outage every run."""
    from app.modules.sender.inbound import CALLBACK, store, to_row

    await store(db_session, to_row(_msg("wamid.SEEN"), arrived_via=CALLBACK))

    async def _fake(api, since, until):  # noqa: ANN001, ANN202, ARG001
        return [_msg("wamid.SEEN"), _msg("wamid.NEW")]

    monkeypatch.setattr(catchup, "fetch", _fake)

    assert await catchup.sweep(db_session, _API, now=_NOW) == 1


async def test_an_unconfigured_sweep_does_nothing_instead_of_guessing(
    db_session, monkeypatch,  # noqa: ANN001
) -> None:
    """Nobody has given us the host, the token or their numeric ids yet. Until they do, the
    sweep must stay still rather than invent an address or a tenant."""
    called = False

    async def _fake(api, since, until):  # noqa: ANN001, ANN202, ARG001
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(catchup, "fetch", _fake)

    blank = SenderApi(base_url="", token="", project_id="", branch_id="")
    assert await catchup.sweep(db_session, blank, now=_NOW) == 0
    assert not called, "an unconfigured sweep must not call anything"


async def test_a_dead_list_endpoint_is_survivable(monkeypatch) -> None:  # noqa: ANN001
    """The callback is the primary path; the sweep is only the net. If their list is down the
    next sweep covers the same period again, so a transport failure must come back as "no
    messages" rather than take the net down with it.

    Exercises the REAL fetch against a broken client — patching fetch itself would only prove
    the fake behaves, which is where the first version of this test went wrong.
    """
    import httpx

    class _Dead:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self) -> _Dead:
            return self

        async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
            return False

        async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            raise httpx.ConnectError("connection reset")

    monkeypatch.setattr(httpx, "AsyncClient", _Dead)

    assert await catchup.fetch(_API, _NOW, _NOW) == []


async def test_a_rescue_is_logged_loudly(db_session, monkeypatch) -> None:  # noqa: ANN001
    """A callback quietly dropping traffic is invisible unless something counts it. This
    project met exactly that shape three times in one day."""
    seen: list[str] = []
    monkeypatch.setattr(catchup.logger, "warning",
                        lambda msg, *a: seen.append(msg % a if a else msg))

    async def _fake(api, since, until):  # noqa: ANN001, ANN202, ARG001
        return [_msg("wamid.X")]

    monkeypatch.setattr(catchup, "fetch", _fake)
    await catchup.sweep(db_session, _API, now=_NOW)

    assert seen
    assert "rescued 1" in seen[-1]
