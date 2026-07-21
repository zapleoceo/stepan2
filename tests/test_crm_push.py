"""CRM push over MCP — the push logic (arg mapping, comment build) without a live CRM."""
from __future__ import annotations

from app.modules.crm.push_mcp import (
    EVENT_WAIT_CALL,
    LeadToPush,
    _comment_for,
    push_leads,
)


class _FakePusher:
    def __init__(self, fail_phones: set[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_phones = fail_phones or set()

    async def add_lead_event(self, phone, event_type, *, comment, name):  # noqa: ANN001
        self.calls.append(
            {"phone": phone, "event_type": event_type, "comment": comment, "name": name})
        if phone in self.fail_phones:
            return False, "duplicate"
        return True, "ok"


def _lead(lid, phone, **kw):  # noqa: ANN001
    d = dict(name="Budi", stage="presenting", product="smm_intensive", days_idle=3,
             last_msg="masih mikir dulu kak")
    d.update(kw)
    return LeadToPush(lead_id=lid, phone=phone, **d)


async def test_push_maps_fields_and_builds_context_comment() -> None:
    p = _FakePusher()
    leads = [_lead(1, "+628111"), _lead(2, "+628222", stage="objection", product="vibe_coding")]
    res = await push_leads(p, leads)

    assert res == {"pushed": 2, "failed": 0, "errors": []}
    c0 = p.calls[0]
    assert c0["phone"] == "+628111" and c0["event_type"] == EVENT_WAIT_CALL
    assert c0["name"] == "Budi"
    # the bot's context rides in the comment (managerComment → CRM description)
    assert "stage=presenting" in c0["comment"] and "smm_intensive" in c0["comment"]
    assert "diam 3 hari" in c0["comment"] and "masih mikir dulu kak" in c0["comment"]


async def test_push_reports_failures_without_raising() -> None:
    p = _FakePusher(fail_phones={"+628222"})
    res = await push_leads(p, [_lead(1, "+628111"), _lead(2, "+628222")])
    assert res["pushed"] == 1 and res["failed"] == 1
    assert res["errors"][0]["lead_id"] == 2 and res["errors"][0]["error"] == "duplicate"


def test_comment_handles_missing_product_and_message() -> None:
    c = _comment_for(_lead(1, "+628111", product=None, last_msg=""))
    assert "belum jelas" in c and '"-"' in c  # graceful fallbacks, no crash


async def _seed_lead_with_phone(db_session, phone: str):  # noqa: ANN001
    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
    from app.domain.enums import ChannelKind, Stage
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, is_active=True)
    db_session.add(ch)
    lead = Lead(branch_id=b.id, stage=Stage.PRESENTING, phone_e164=phone)
    db_session.add_all([ch, lead])
    await db_session.flush()
    db_session.add(ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="x",
                                 product_slug="smm_intensive"))
    await db_session.flush()
    return b.id, lead.id


async def test_drain_marks_success_idempotently_and_retries_failure(db_session) -> None:
    from app.modules.crm.push_mcp import drain_writeback

    bid, lid = await _seed_lead_with_phone(db_session, "+628111222333")
    # a FAILING push must NOT mark the lead → it stays eligible next run
    fail = _FakePusher(fail_phones={"+628111222333"})
    r1 = await drain_writeback(db_session, bid, fail)
    assert r1 == {"eligible": 1, "pushed": 0, "failed": 1}
    r2 = await drain_writeback(db_session, bid, fail)
    assert r2["eligible"] == 1  # still eligible — a failed push is retried

    # a SUCCESS marks it → the next run no longer sees it (idempotent)
    ok = _FakePusher()
    r3 = await drain_writeback(db_session, bid, ok)
    assert r3 == {"eligible": 1, "pushed": 1, "failed": 0}
    r4 = await drain_writeback(db_session, bid, ok)
    assert r4["eligible"] == 0 and r4["pushed"] == 0  # already pushed, never again
