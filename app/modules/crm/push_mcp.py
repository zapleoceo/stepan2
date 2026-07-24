"""Write Stepan's warm leads into the itstep CRM funnel over its MCP.

CRM is the source of truth for calls/money; Stepan is the messenger. We join by phone E.164
and push a funnel event (`crm_lead_add_event`) so a manager sees a warm lead as a task, with
the bot's context in `managerComment`. Streamable-HTTP MCP; url + city alias from branch
settings (crm_mcp_url, crm_mcp_city_alias). The transport is behind CrmPusherPort so the push
logic is unit-testable without a live CRM."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.conversation.dossier import parse_dossier

logger = logging.getLogger(__name__)

# Warm-but-stalled lead the manager should call back. From crm_lead_event_types (jakarta):
# wait_call | thinking | contract | reject | event | material | waiting_registration | ...
EVENT_WAIT_CALL = "wait_call"

# StageEvent marker so a lead pushed once is never re-pushed — idempotency across cron runs.
# A FAILED push is NOT marked, so it retries next run (and auto-drains once the CRM endpoint
# is fixed). Keyed the same way reactivation keys its cap/gap off a StageEvent reason.
PUSHED_REASON = "crm_pushed"
# Separate marker for the HAND-OFF push (ready/manager exit): the warm-lead PUSHED_REASON may
# already be set from a drain that ran BEFORE the hand-off (thread 4529: pushed as warm on
# 23.07, RSVP'd + escalated on 24.07 — the escalation is a materially new CRM event, not a
# re-push of the same state), so hand-off idempotency needs its own key.
PUSHED_HANDOFF_REASON = "crm_pushed_handoff"
DRAIN_BATCH = 25


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
    job_to_be_done: str = ""
    pains: list[str] = field(default_factory=list)
    desired_state: list[str] = field(default_factory=list)


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

        try:
            async with streamablehttp_client(self.url, timeout=self.timeout_s) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    # crm_lead_add_event assumes the phone is ALREADY a CRM contact — for an
                    # IG-ad lead that never existed in CRM, its internal add-contact call
                    # 404s (confirmed with itstep CRM devs, 2026-07-23). crm_client_search
                    # first tells us which of the two tools this phone actually needs.
                    known = await self._is_known_client(s, phone)
                    if known:
                        return await self._add_event(s, phone, event_type, comment, name)
                    return await self._create_internet_request(s, phone, comment, name)
        except Exception as exc:  # noqa: BLE001 — external MCP transport; log + report, never raise
            logger.exception("crm push transport error phone=%s", phone)
            return False, str(exc)

    async def _is_known_client(self, s: Any, phone: str) -> bool:
        res = await s.call_tool(
            "crm_client_search", {"cityAlias": self.city_alias, "search": phone})
        full = _content_text(res, limit=None)  # count_all can sit past a truncated 300 chars
        if getattr(res, "isError", False):
            # Search itself failing shouldn't block the push — fall through to the create
            # path, which is the safer default for a lead we can't confirm exists yet.
            logger.warning("crm client search failed phone=%s: %s", phone, full[:300])
            return False
        try:
            return int(json.loads(full).get("count_all", 0)) > 0
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("crm client search unparseable phone=%s: %s", phone, full[:300])
            return False  # unparseable → treat as unknown, safer to create than to 404

    async def _add_event(
        self, s: Any, phone: str, event_type: str, comment: str, name: str | None,
    ) -> tuple[bool, str]:
        args: dict[str, Any] = {
            "cityAlias": self.city_alias, "phone": phone, "eventType": event_type,
            "managerComment": comment,
        }
        if name:
            args["name"] = name
        res = await s.call_tool("crm_lead_add_event", args)
        detail = _content_text(res)
        if getattr(res, "isError", False):
            logger.warning("crm push failed phone=%s: %s", phone, detail)
            return False, detail
        return True, detail

    async def _create_internet_request(
        self, s: Any, phone: str, comment: str, name: str | None,
    ) -> tuple[bool, str]:
        args: dict[str, Any] = {
            "cityAlias": self.city_alias, "phone": phone, "name": name or "Kak",
            "type": "ai_bot", "comment": comment,
        }
        res = await s.call_tool("crm_internet_request_create", args)
        detail = _content_text(res)
        if getattr(res, "isError", False):
            logger.warning("crm lead create failed phone=%s: %s", phone, detail)
            return False, detail
        return True, detail


def _content_text(res: Any, limit: int | None = 300) -> str:
    parts = [getattr(c, "text", "") for c in (getattr(res, "content", None) or [])]
    full = " ".join(p for p in parts if p)
    return full if limit is None else full[:limit]


def _coerce_dt(v: Any) -> datetime | None:
    """Timestamp columns come back as datetime on Postgres but as a string via raw text() on
    SQLite — normalize to a naive datetime for the days-idle math."""
    if v is None or isinstance(v, datetime):
        return v.replace(tzinfo=None) if isinstance(v, datetime) and v.tzinfo else v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "").split("+")[0].strip())
    except ValueError:
        return None


def _comment_for(lead: LeadToPush) -> str:
    """SPIN-shaped summary for managerComment — job_to_be_done/pains/desired_state straight
    from the dossier, so a manager reads why the lead is here before ever opening the chat.
    Falls back to the last inbound line when discovery hasn't landed yet (dossier empty),
    same as before this dossier data existed — never leaves the comment blank."""
    prod = lead.product or "belum jelas"
    spin = [
        f"Tujuan: {lead.job_to_be_done}" if lead.job_to_be_done else "",
        f"Kendala: {'; '.join(lead.pains)}" if lead.pains else "",
        f"Ingin: {'; '.join(lead.desired_state)}" if lead.desired_state else "",
    ]
    body = " | ".join(p for p in spin if p)
    if not body:
        last = (lead.last_msg or "").strip()[:120] or "-"
        body = f"Belum ada discovery. Pesan terakhir lead: \"{last}\""
    return (
        f"[Stepan IG] {body}. Minat: {prod}, stage={lead.stage}, diam {lead.days_idle} hari. "
        f"Perlu di-follow up (telepon/WA)."
    )


async def fetch_leads_with_phone(
    session: AsyncSession, branch_id: int, limit: int = 100, *, exclude_pushed: bool = True,
    now: datetime | None = None,
) -> list[LeadToPush]:
    """Non-closed leads (not ready/manager/handed_off) that have a phone — pushable by phone.
    exclude_pushed skips leads already synced to the CRM (the PUSHED_REASON marker). Portable
    SQL (SQLite + Postgres): days-idle is computed in Python, not with now()/EXTRACT/GREATEST."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    not_pushed = (
        " AND NOT EXISTS (SELECT 1 FROM stage_event se WHERE se.lead_id=l.id"
        "   AND se.reason=:pushed)" if exclude_pushed else "")
    rows = (await session.execute(text(
        "SELECT l.id, l.phone_e164,"  # noqa: S608 — not_pushed is a fixed fragment, values bound
        " coalesce(nullif(l.display_name,''), nullif(l.ig_username,''), '') AS nm,"
        " l.stage, ct.product_slug, l.created_at, l.last_active_at, l.dossier, l.needs,"
        " coalesce((SELECT m.text FROM message m WHERE m.thread_id=ct.id AND m.direction='in'"
        "   ORDER BY m.occurred_at DESC LIMIT 1),'') AS last_msg"
        " FROM lead l JOIN channel_thread ct ON ct.lead_id=l.id"
        " WHERE l.branch_id=:bid AND l.stage NOT IN ('ready','manager','handed_off')"
        "   AND l.phone_e164 IS NOT NULL AND l.phone_e164 <> '' AND length(l.phone_e164) >= 9"
        + not_pushed +
        " ORDER BY l.created_at DESC LIMIT :lim"),
        {"bid": branch_id, "lim": limit, "pushed": PUSHED_REASON})).all()
    out = []
    seen: set[int] = set()
    for r in rows:
        if r[0] in seen:
            # A cross-channel-merged lead has several threads → the JOIN yields one row per
            # thread, which used to push N duplicate wait_call events (and N PUSHED markers)
            # for one person. First row wins — created_at DESC puts the newest thread first.
            continue
        seen.add(r[0])
        created, last_active = _coerce_dt(r[5]), _coerce_dt(r[6])
        cands = [x for x in (created, last_active) if x is not None]
        recency = max(cands) if cands else now
        days_idle = max(0, (now - recency).days)
        dossier = parse_dossier(r[7], legacy_needs=r[8])
        out.append(LeadToPush(
            lead_id=r[0], phone=r[1], name=r[2] or None, stage=str(r[3]),
            product=r[4], days_idle=days_idle, last_msg=r[9] or "",
            job_to_be_done=dossier.job_to_be_done,
            pains=dossier.pains, desired_state=dossier.desired_state))
    return out


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


async def drain_writeback(
    session: AsyncSession, branch_id: int, pusher: CrmPusherPort,
    event_type: str = EVENT_WAIT_CALL, limit: int = DRAIN_BATCH,
) -> dict[str, Any]:
    """One background pass: push a batch of not-yet-pushed warm leads to the CRM, marking each
    SUCCESS with a PUSHED_REASON StageEvent so it's never re-pushed. A FAILURE is left unmarked
    → retried next run, so a broken CRM endpoint (404) just logs and auto-drains once fixed."""
    from app.adapters.db.models import StageEvent  # noqa: PLC0415

    leads = await fetch_leads_with_phone(session, branch_id, limit=limit, exclude_pushed=True)
    pushed, failed = 0, 0
    for lead in leads:
        ok, detail = await pusher.add_lead_event(
            lead.phone, event_type, comment=_comment_for(lead), name=lead.name)
        if ok:
            pushed += 1
            session.add(StageEvent(
                branch_id=branch_id, lead_id=lead.lead_id, thread_id=None,
                from_stage=lead.stage, to_stage=lead.stage,
                actor="system", reason=PUSHED_REASON))
        else:
            failed += 1
            logger.warning("crm writeback failed lead=%d: %s", lead.lead_id, detail)
    if pushed:
        await session.flush()
    return {"eligible": len(leads), "pushed": pushed, "failed": failed}


async def fetch_unpushed_handoffs(
    session: AsyncSession, branch_id: int, limit: int = DRAIN_BATCH,
    now: datetime | None = None,
) -> list[LeadToPush]:
    """Human-led leads (ready/manager/handed_off) WITH a phone whose hand-off never reached
    the CRM — the phone typically arrives AFTER the escalation muted the bot (thread 4529:
    RSVP escalated at 01:09 with no contact, the number landed 03:41 via ingest's miner, and
    nothing ever told the CRM). The flip-time push (delivery.push_crm_after_commit) covers the
    phone-first case and writes PUSHED_HANDOFF_REASON; this sweep catches the phone-later and
    push-failed leftovers. Windowed to hand-offs from the last 7 days so history that managers
    already worked by hand isn't re-announced."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    rows = (await session.execute(text(
        "SELECT l.id, l.phone_e164,"
        " coalesce(nullif(l.display_name,''), nullif(l.ig_username,''), '') AS nm,"
        " l.stage, ct.product_slug, l.created_at, l.last_active_at, l.dossier, l.needs,"
        " coalesce((SELECT m.text FROM message m WHERE m.thread_id=ct.id AND m.direction='in'"
        "   ORDER BY m.occurred_at DESC LIMIT 1),'') AS last_msg"
        " FROM lead l JOIN channel_thread ct ON ct.lead_id=l.id"
        " WHERE l.branch_id=:bid AND l.stage IN ('ready','manager','handed_off')"
        "   AND l.phone_e164 IS NOT NULL AND l.phone_e164 <> '' AND length(l.phone_e164) >= 9"
        "   AND EXISTS (SELECT 1 FROM stage_event se WHERE se.lead_id=l.id"
        "     AND se.to_stage IN ('ready','manager','handed_off') AND se.created_at >= :since)"
        "   AND NOT EXISTS (SELECT 1 FROM stage_event se WHERE se.lead_id=l.id"
        "     AND se.reason=:pushed)"
        " ORDER BY l.last_active_at DESC NULLS LAST LIMIT :lim"),
        {"bid": branch_id, "lim": limit, "pushed": PUSHED_HANDOFF_REASON,
         "since": now - timedelta(days=7)})).all()
    out = []
    seen: set[int] = set()
    for r in rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        dossier = parse_dossier(r[7], legacy_needs=r[8])
        out.append(LeadToPush(
            lead_id=r[0], phone=r[1], name=r[2] or None, stage=str(r[3]),
            product=r[4], days_idle=0, last_msg=r[9] or "",
            job_to_be_done=dossier.job_to_be_done,
            pains=dossier.pains, desired_state=dossier.desired_state))
    return out


async def drain_handoffs(
    session: AsyncSession, branch_id: int, pusher: CrmPusherPort,
    limit: int = DRAIN_BATCH,
) -> dict[str, Any]:
    """Sweep un-pushed hand-offs into the CRM (see fetch_unpushed_handoffs). Same
    marker-on-success / retry-on-failure contract as drain_writeback."""
    from app.adapters.db.models import StageEvent  # noqa: PLC0415

    leads = await fetch_unpushed_handoffs(session, branch_id, limit=limit)
    pushed, failed = 0, 0
    for lead in leads:
        comment = _comment_for(lead).replace(
            "Perlu di-follow up (telepon/WA).",
            "Lead SUDAH diserahkan ke tim (hand-off) - hubungi segera.")
        ok, detail = await pusher.add_lead_event(
            lead.phone, EVENT_WAIT_CALL, comment=comment, name=lead.name)
        if ok:
            pushed += 1
            session.add(StageEvent(
                branch_id=branch_id, lead_id=lead.lead_id, thread_id=None,
                from_stage=lead.stage, to_stage=lead.stage,
                actor="system", reason=PUSHED_HANDOFF_REASON))
        else:
            failed += 1
            logger.warning("crm handoff sweep failed lead=%d: %s", lead.lead_id, detail)
    if pushed:
        await session.flush()
    return {"eligible": len(leads), "pushed": pushed, "failed": failed}
