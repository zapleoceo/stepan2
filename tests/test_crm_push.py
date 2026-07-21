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
