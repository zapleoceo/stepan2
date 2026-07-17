"""CRM state reader over the CRM's own MCP server (mcp.itstep.org).

Implements the same port as the REST CrmReader — get_state(url, secret, phone) → a flat
dict the gate's compute_verdict understands — but sources it from two MCP tools in one
session: crm_client_search (phone → id_uniq) + crm_client_history (events timeline).

Derivation:
  exists          — the search found a client card
  deal_won        — a `contract` event anywhere in the history
  manager_called  — a SUCCESSFUL out-call (no_answer=0) within the hold window; an old
                    call does NOT hold (Stepan re-engaging a gone-cold lead is the point)
Never raises: any transport/parse failure returns None and the gate fails open — a CRM
outage must not silence a live sales bot.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from app.config import settings

logger = logging.getLogger(__name__)


class CrmMcpReader:
    """Reads a lead's CRM state through the CRM's MCP server."""

    def __init__(self, city_alias: str) -> None:
        self.city_alias = city_alias

    async def get_state(self, url: str, secret: str, phone: str) -> dict | None:  # noqa: ARG002
        try:
            async with asyncio.timeout(settings().crm_mcp_timeout_s):
                return await self._fetch(url, phone)
        except Exception as exc:  # noqa: BLE001 — no opinion → gate fails open
            logger.warning("crm mcp read failed (phone=%s): %s", phone, str(exc)[:200])
            return None

    async def _fetch(self, url: str, phone: str) -> dict | None:
        # local import: keep the app importable even if the mcp package is absent
        from mcp.client.session import ClientSession  # noqa: PLC0415
        from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                found = await self._call(s, "crm_client_search",
                                         {"cityAlias": self.city_alias, "search": phone})
                cards = (found or {}).get("data") or []
                if not cards:
                    return {"exists": False, "source": "mcp"}
                crm_id = int(cards[0].get("id_uniq") or 0)
                if not crm_id:
                    return {"exists": False, "source": "mcp"}
                history = await self._call(s, "crm_client_history",
                                           {"cityAlias": self.city_alias,
                                            "clientId": crm_id, "perPage": 50})
                return self._derive(crm_id, (history or {}).get("data") or [])

    async def list_missed_out_calls(
        self, url: str, days: int = 3, max_pages: int = 3,
    ) -> list[tuple[str, str]]:
        """Phones the branch tried to call and never reached in the last `days`:
        out-calls with billsec ≤ 10s (missed / voicemail-bounce), minus any phone that
        ALSO had an answered call in the window. Newest missed attempt first. Returns []
        on any failure — the rescue job just skips a cycle."""
        try:
            async with asyncio.timeout(settings().crm_mcp_timeout_s * 2):
                return await self._list_missed(url, days, max_pages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("crm mcp calls list failed: %s", str(exc)[:200])
            return []

    async def _list_missed(self, url: str, days: int, max_pages: int) -> list[tuple[str, str]]:
        from mcp.client.session import ClientSession  # noqa: PLC0415
        from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

        now = datetime.now(UTC)
        args_base = {
            "cityAlias": self.city_alias,
            "dateFrom": (now - timedelta(days=days)).date().isoformat(),
            "dateTo": now.date().isoformat(),
            "perPage": 100,
        }
        answered: set[str] = set()
        missed: dict[str, str] = {}
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                for page in range(1, max_pages + 1):
                    data = await self._call(s, "crm_calls_list", {**args_base, "page": page})
                    rows = (data or {}).get("data") or []
                    if not rows:
                        break
                    for x in rows:
                        if x.get("call_type") != "out":
                            continue
                        phone = str(x.get("number_to") or "").strip()
                        if not phone:
                            continue
                        at = str(x.get("date_call") or "")
                        if int(x.get("billsec") or 0) > 10:
                            answered.add(phone)
                        elif at > missed.get(phone, ""):
                            missed[phone] = at
        return sorted(((p, at) for p, at in missed.items() if p not in answered),
                      key=lambda kv: kv[1], reverse=True)

    @staticmethod
    async def _call(s, tool: str, args: dict) -> dict | None:  # noqa: ANN001
        res = await s.call_tool(tool, args)
        if res.isError or not res.content:
            logger.warning("crm mcp tool %s errored: %s", tool,
                           (res.content[0].text if res.content else "")[:150])
            return None
        try:
            data = json.loads(res.content[0].text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _derive(self, crm_id: int, rows: list[dict]) -> dict:
        deal_won = any(r.get("typeName") == "contract" for r in rows)
        last_ok_call = self._last_answered_call(rows)
        hold_window = timedelta(hours=settings().crm_manager_call_hold_h)
        recently_called = (
            last_ok_call is not None
            and datetime.now(UTC) - last_ok_call < hold_window
        )
        return {
            "exists": True,
            "crm_id": crm_id,
            "deal_won": deal_won,
            "manager_called": recently_called,
            "last_manager_call_at": last_ok_call.isoformat() if last_ok_call else None,
            "events_seen": len(rows),
            "source": "mcp",
        }

    @staticmethod
    def _last_answered_call(rows: list[dict]) -> datetime | None:
        latest: datetime | None = None
        for r in rows:
            if r.get("typeName") != "out-call" or str(r.get("no_answer")) != "0":
                continue
            try:
                at = datetime.fromisoformat(str(r.get("date_time")))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if latest is None or at > latest:
                latest = at
        return latest
