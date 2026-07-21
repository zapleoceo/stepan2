"""Write Stepan's warm leads into the itstep CRM funnel over its MCP.

CRM is the source of truth for calls/money; Stepan is the messenger. We join by phone E.164
and push a funnel event (`crm_lead_add_event`) so a manager sees a warm lead as a task, with
the bot's context in `managerComment`. Streamable-HTTP MCP; url + city alias from branch
settings (crm_mcp_url, crm_mcp_city_alias). The transport is behind CrmPusherPort so the push
logic is unit-testable without a live CRM."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# Warm-but-stalled lead the manager should call back. From crm_lead_event_types (jakarta):
# wait_call | thinking | contract | reject | event | material | waiting_registration | ...
EVENT_WAIT_CALL = "wait_call"
EVENT_THINKING = "thinking"


class CrmPusherPort(Protocol):
    async def add_lead_event(
        self, phone: str, event_type: str, *, comment: str, name: str | None,
    ) -> tuple[bool, str]: ...


@dataclass
class LeadToPush:
    lead_id: int
    phone: str
    name: str | None
    stage: str
    product: str | None
    days_idle: int
    last_msg: str


class CrmMcpPusher:
    """Real transport: one MCP round-trip per event (connect → initialize → call_tool)."""

    def __init__(self, url: str, city_alias: str, timeout_s: float = 30.0) -> None:
        self.url = url
        self.city_alias = city_alias
        self.timeout_s = timeout_s

    async def add_lead_event(
        self, phone: str, event_type: str, *, comment: str, name: str | None,
    ) -> tuple[bool, str]:
        from mcp import ClientSession  # noqa: PLC0415
        from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

        args: dict[str, Any] = {
            "cityAlias": self.city_alias, "phone": phone, "eventType": event_type,
            "managerComment": comment,
        }
        if name:
            args["name"] = name
        try:
            async with streamablehttp_client(self.url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("crm_lead_add_event", args)
                    detail = _content_text(res)
                    if getattr(res, "isError", False):
                        logger.warning("crm push failed phone=%s: %s", phone, detail)
                        return False, detail
                    return True, detail
        except Exception as exc:  # noqa: BLE001 — external MCP transport; log + report, never raise
            logger.exception("crm push transport error phone=%s", phone)
            return False, str(exc)


def _content_text(res: Any) -> str:
    parts = [getattr(c, "text", "") for c in (getattr(res, "content", None) or [])]
    return " ".join(p for p in parts if p)[:300]


def _comment_for(lead: LeadToPush) -> str:
    prod = lead.product or "belum jelas"
    last = (lead.last_msg or "").strip()[:120] or "-"
    return (
        f"[Stepan IG] Lead hangat, stage={lead.stage}, minat={prod}, diam {lead.days_idle} hari. "
        f"Pesan terakhir lead: \"{last}\". Perlu di-follow up (telepon/WA)."
    )


async def fetch_leads_with_phone(
    session: AsyncSession, branch_id: int, limit: int = 100,
) -> list[LeadToPush]:
    """Non-closed leads (not ready/manager/handed_off) that have a phone — pushable by phone."""
    rows = (await session.execute(text(
        "SELECT l.id, l.phone_e164,"
        " coalesce(nullif(l.display_name,''), nullif(l.ig_username,''), '') AS nm,"
        " l.stage, ct.product_slug,"
        " round(EXTRACT(EPOCH FROM now()-GREATEST(l.created_at,"
        "   coalesce(l.last_active_at,l.created_at)))/86400)::int AS days_idle,"
        " coalesce((SELECT m.text FROM message m WHERE m.thread_id=ct.id AND m.direction='in'"
        "   ORDER BY m.occurred_at DESC LIMIT 1),'') AS last_msg"
        " FROM lead l JOIN channel_thread ct ON ct.lead_id=l.id"
        " WHERE l.branch_id=:bid AND l.stage NOT IN ('ready','manager','handed_off')"
        "   AND l.phone_e164 IS NOT NULL AND l.phone_e164 <> '' AND length(l.phone_e164) >= 9"
        " ORDER BY days_idle LIMIT :lim"),
        {"bid": branch_id, "lim": limit})).all()
    return [
        LeadToPush(lead_id=r[0], phone=r[1], name=r[2] or None, stage=str(r[3]),
                   product=r[4], days_idle=int(r[5] or 0), last_msg=r[6] or "")
        for r in rows
    ]


async def push_leads(
    pusher: CrmPusherPort, leads: list[LeadToPush], event_type: str = EVENT_WAIT_CALL,
) -> dict[str, Any]:
    """Push each lead as one funnel event with the bot's context. Returns a summary."""
    pushed, failed, errors = 0, 0, []
    for lead in leads:
        ok, detail = await pusher.add_lead_event(
            lead.phone, event_type, comment=_comment_for(lead), name=lead.name)
        if ok:
            pushed += 1
        else:
            failed += 1
            errors.append({"lead_id": lead.lead_id, "phone": lead.phone, "error": detail})
    return {"pushed": pushed, "failed": failed, "errors": errors}
