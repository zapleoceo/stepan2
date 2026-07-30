"""CRM read-gate — the pre-contact check that stops Stepan re-touching a lead the CRM
already moved on (manager owns it, deal closed, next step scheduled, …).

Flow: before an automated send, allow_send() consults the lead's CRM state (a cached
row refreshed by the pull sync, or refetched live when stale). A `hold` verdict stands
the lead down — bot off, stage → manager, journaled — so no more messages generate.

Fail-open by design: gate off, no phone, lead absent from CRM, or an unreachable CRM
all ALLOW the send. A CRM outage must never silence a live sales bot.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import CrmLeadState, Lead, StageEvent
from app.config import settings
from app.domain.clock import naive_utc, utc_now
from app.domain.enums import Stage
from app.modules.crm.service import is_safe_webhook_url
from app.modules.settings.service import get_settings

logger = logging.getLogger(__name__)


@dataclass
class CrmState:
    exists: bool
    verdict: str  # proceed | hold
    reason: str
    status: str | None
    owner: str | None
    raw: dict
    # The CRM has always sent these; nothing read them off the parsed state, so the one
    # question the business actually asks — did this lead buy? — had no answer in our data.
    # `won_at` is not in the contract yet (the MCP returns a bare boolean), so a deal cannot
    # yet be tied to the moment our conversation started; see the note in pull.sync_outcomes.
    deal_won: bool = False
    manager_called: bool = False
    won_at: str | None = None


class CrmReaderPort:
    async def get_state(self, url: str, secret: str, phone: str) -> dict | None: ...


def crm_read_url(cfg) -> str:  # noqa: ANN001
    """The state-source URL: the branch REST contract wins when set, else the CRM's own
    MCP server (platform-level setting the branches inherit)."""
    return (cfg.crm_state_url or "").strip() or (cfg.crm_mcp_url or "").strip()


def build_crm_reader(cfg) -> CrmReaderPort:  # noqa: ANN001
    """Pick the reader matching the configured source: REST (crm_state_url) or the CRM's
    MCP server (crm_mcp_url). Callers (outbox gate, pull sync) stay source-agnostic."""
    if not (cfg.crm_state_url or "").strip() and (cfg.crm_mcp_url or "").strip():
        from app.adapters.crm_mcp import CrmMcpReader  # noqa: PLC0415
        return CrmMcpReader(cfg.crm_mcp_city_alias)
    from app.adapters.crm import CrmReader  # noqa: PLC0415
    return CrmReader()


# CRM fields that, when truthy, mean a human/process already owns the lead's next step —
# Stepan must stand down. Each maps to a short reason token for the journal.
_HOLD_FLAGS = {
    "deal_won": "deal won",
    "contract_signed": "contract signed",
    "paid": "paid",
    "open_task": "open task",
    "manager_called": "manager called",
    "next_contact_at": "next contact scheduled",
}


# Two kinds of hold, because "stop" meant two different things and we shipped the harsher one.
#
# SILENCING — the deal is commercially closed. The bot must not write at all: a chatty bot
# after a signed contract can talk a paying customer back out of it.
#
# INITIATIVE-ONLY — a human is mid-way through working this lead. The bot must not START a
# conversation, but a lead who asks a direct question still gets an answer. Until 30.07.2026
# both classes ran through the same _stand_down: an answered manager call moved the lead to
# MANAGER and set agent_enabled=False for 72 hours, so someone writing "а можно оплатить
# частями?" got silence for three days. Silence in reply to a direct question is the single
# most expensive thing this system does, and it was happening in the most common case there
# is — `wait_call` is 74% of all contacts the branch records.
_SILENCING_FLAGS = ("deal_won", "contract_signed", "paid")
_INITIATIVE_FLAGS = ("manager_called", "next_contact_at", "open_task")

# Outbox sources that ANSWER the lead rather than start something. Kept here rather than
# imported so the gate has no dependency on the chat routes.
REPLY_SOURCES = frozenset({"agent", "manager"})


def hold_kind(raw: dict) -> str:
    """'silence' | 'initiative' | '' — how far a hold reaches. Read from the raw flags, not
    from the joined reason string: the text is for humans and must stay free to change."""
    if str(raw.get("owner") or "").lower() == "manager":
        return "initiative"
    if any(raw.get(k) for k in _SILENCING_FLAGS):
        return "silence"
    if any(raw.get(k) for k in _INITIATIVE_FLAGS):
        return "initiative"
    return ""


def compute_verdict(raw: dict) -> tuple[str, str]:
    """Derive proceed/hold from raw CRM fields. If the CRM already returns a `verdict`,
    trust it; otherwise apply the stand-down rule (any ownership/close/next-step signal
    → hold)."""
    explicit = str(raw.get("verdict") or "").lower()
    if explicit in ("proceed", "hold"):
        return explicit, str(raw.get("reason") or explicit)
    reasons: list[str] = []
    if str(raw.get("owner") or "").lower() == "manager":
        reasons.append("manager owns")
    reasons += [label for key, label in _HOLD_FLAGS.items() if raw.get(key)]
    return ("hold", "; ".join(reasons)) if reasons else ("proceed", "")


def parse_won_at(value: object) -> datetime | None:
    """CRM close timestamp → naive UTC datetime, or None when absent/unparseable.

    Naive UTC because that is what this codebase stores: every DB column is TIMESTAMP WITHOUT
    TIME ZONE and asyncpg refuses a tz-aware value for one. The CRM sends an offset
    ("2025-11-12T10:39:44+07:00"), and returning that aware datetime made every won deal fail
    to save with "can't subtract offset-naive and offset-aware datetimes" — silently, since
    sync_outcomes logs and moves on. A value without an offset is read as UTC.

    The CRM does not guarantee this field, so an unusable value must degrade to "no date"
    rather than raise — a sale with no timestamp is still a sale."""
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return naive_utc(at if at.tzinfo else at.replace(tzinfo=UTC))


def _parse(raw: dict) -> CrmState:
    verdict, reason = compute_verdict(raw)
    return CrmState(
        exists=bool(raw.get("exists", True)),
        verdict=verdict, reason=reason,
        # `status` — то, что видно в админке и лежит колонкой. Читатель по MCP статуса как
        # такового не отдаёт, зато отдаёт последний результат контакта, а это и есть самое
        # близкое к «в каком состоянии лид у менеджера».
        status=raw.get("status") or raw.get("last_result"),
        owner=raw.get("owner"), raw=raw,
        deal_won=bool(raw.get("deal_won", False)),
        manager_called=bool(raw.get("manager_called", False)),
        # Accepted under either name so the day the CRM adds it, nothing here has to change.
        won_at=raw.get("deal_won_at") or raw.get("won_at"),
    )


class CrmGate:
    """Per-branch CRM read-gate: state lookup (cache-aware) + stand-down enforcement."""

    def __init__(
        self, session: AsyncSession, branch_id: int, reader: CrmReaderPort
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.reader = reader

    async def allow_send(self, lead: Lead, source: str) -> tuple[bool, str]:
        """True → Stepan may send. Manager sends always pass (human override). A `hold`
        verdict returns False AND stands the lead down so nothing else generates."""
        cfg = await get_settings(self.session, self.branch_id)
        url = crm_read_url(cfg)
        if not cfg.crm_read_enabled or not url or source == "manager":
            return True, ""
        if not lead.phone_e164:
            return True, "no phone"
        if not is_safe_webhook_url(url):
            logger.warning("crm gate branch=%d: unsafe crm_state_url refused", self.branch_id)
            return True, "unsafe url"
        state = await self._state_for(lead, cfg.crm_read_secret, url)
        if state is None or not state.exists or state.verdict != "hold":
            return True, ""
        # An unclassifiable hold is treated as initiative-only, not as silence: whatever the
        # CRM meant, it is never worth leaving a lead's direct question unanswered on a guess.
        # A real close is reconstructable from the deal_won column even without the raw JSON.
        if hold_kind(state.raw or {}) != "silence":
            # A human is working this lead: we don't start anything, but we do answer.
            # No stand-down either — disabling the agent would kill the replies too, which
            # is exactly the behaviour being fixed here.
            if source in REPLY_SOURCES:
                return True, ""
            logger.info("branch=%d lead=%d CRM hold (initiative only), %s held: %s",
                        self.branch_id, lead.id, source, state.reason)
            return False, state.reason
        await self._stand_down(lead, state.reason)
        return False, state.reason

    async def refresh(self, lead: Lead) -> CrmState | None:
        """Force a live fetch + cache upsert (used by the pull sync)."""
        cfg = await get_settings(self.session, self.branch_id)
        url = crm_read_url(cfg)
        if not cfg.crm_read_enabled or not url or not lead.phone_e164:
            return None
        if not is_safe_webhook_url(url):
            return None
        return await self._fetch(lead, cfg.crm_read_secret, url)

    async def enforce(self, lead: Lead) -> str:
        """Pull-sync path: refresh the lead's CRM state and stand it down if `hold`.
        Returns the verdict ('proceed' | 'hold' | 'unknown' when the CRM is unreachable)."""
        state = await self.refresh(lead)
        if state is None:
            return "unknown"
        # Only a commercial close silences the bot from here. An initiative-only hold is
        # enforced per-send in allow_send, where the source is known — standing the lead down
        # in the background sweep would disable the agent and take the replies with it.
        if state.exists and state.verdict == "hold" and hold_kind(state.raw or {}) == "silence":
            await self._stand_down(lead, state.reason)
        return state.verdict

    async def _state_for(self, lead: Lead, secret: str, url: str) -> CrmState | None:
        cached = await self._cached(lead.id)
        if cached is not None:
            age = utc_now() - cached.fetched_at
            if age < timedelta(seconds=settings().crm_state_ttl_s):
                return _parse(json.loads(cached.raw)) if cached.raw else _from_row(cached)
        return await self._fetch(lead, secret, url)

    async def _fetch(self, lead: Lead, secret: str, url: str) -> CrmState | None:
        raw = await self.reader.get_state(url, secret, lead.phone_e164 or "")
        if raw is None:  # CRM unreachable — keep any cached row, report no opinion
            return None
        state = _parse(raw)
        await self._upsert(lead.id, state)
        return state

    async def _cached(self, lead_id: int | None) -> CrmLeadState | None:
        if lead_id is None:
            return None
        return (await self.session.execute(
            select(CrmLeadState).where(CrmLeadState.lead_id == lead_id)
        )).scalars().first()

    async def _upsert(self, lead_id: int | None, state: CrmState) -> None:
        if lead_id is None:
            return
        row = await self._cached(lead_id)
        if row is None:
            row = CrmLeadState(branch_id=self.branch_id, lead_id=lead_id)
        row.exists_in_crm = state.exists
        row.status, row.owner = state.status, state.owner
        row.verdict, row.reason = state.verdict, state.reason
        row.raw = json.dumps(state.raw, ensure_ascii=False)
        row.deal_won = state.deal_won
        row.deal_won_at = parse_won_at(state.won_at)
        row.fetched_at = utc_now()
        self.session.add(row)
        await self.session.flush()

    async def _stand_down(self, lead: Lead, reason: str) -> None:
        """CRM says a human/process owns this lead: silence the bot and hand off."""
        if lead.stage != Stage.MANAGER:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=None,
                from_stage=str(lead.stage), to_stage=str(Stage.MANAGER),
                actor="crm", reason=f"crm hold: {reason}" if reason else "crm hold",
            ))
            lead.stage = Stage.MANAGER
        lead.agent_enabled = False
        self.session.add(lead)
        await self.session.flush()
        logger.info("branch=%d lead=%d CRM stand-down: %s", self.branch_id, lead.id, reason)


def _from_row(row: CrmLeadState) -> CrmState:
    # Rebuild the two flags hold_kind actually needs. They are real columns, so the fallback
    # path (cache row without the verbatim JSON) classifies a closed deal correctly instead
    # of falling through to "unknown".
    return CrmState(exists=row.exists_in_crm, verdict=row.verdict, reason=row.reason or "",
                    status=row.status, owner=row.owner, deal_won=row.deal_won,
                    raw={"owner": row.owner, "deal_won": row.deal_won})
