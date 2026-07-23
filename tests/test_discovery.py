"""The discovery-extraction backstop — a separate, tiny chat:fast call.

Covers: merges correctly via merge_dossier, always runs on FAST, never overwrites already-
known pains/desired_state (only adds), and fails soft on a broken/timeout LLM."""
from __future__ import annotations

import json

from app.adapters.db.models import Message
from app.modules.conversation.discovery import extract_discovery
from app.modules.conversation.dossier import LeadDossier, merge_dossier
from app.modules.conversation.routing import FAST, SMART


def _msg(direction: str, text: str) -> Message:
    return Message(branch_id=1, thread_id=1, channel_id=1, external_id="x",
                   direction=direction, sent_by="lead" if direction == "in" else "bot",
                   text=text)


class _LLM:
    def __init__(self, answer: str | None = None, *, raises: Exception | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.capabilities: list[str] = []
        self.workflows: list[str | None] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.capabilities.append(kw.get("capability", ""))
        self.workflows.append(kw.get("workflow"))
        if self._raises is not None:
            raise self._raises
        return self._answer, {"model": "fake", "cost_usd": 0.0}


def _answer(**over) -> str:  # noqa: ANN003
    payload = {"pains": ["takut telat"], "desired_state": ["kerja remote"], "objections": []}
    payload.update(over)
    return json.dumps(payload)


async def test_extraction_runs_on_fast() -> None:
    llm = _LLM(_answer())
    dialog = [_msg("in", "halo"), _msg("out", "hai"), _msg("in", "aku takut telat mulai")]
    await extract_discovery(llm, dialog, LeadDossier(), "id", branch_id=1, thread_id=1)
    assert llm.capabilities == [FAST]
    assert FAST != SMART


async def test_extraction_merges_via_merge_dossier() -> None:
    llm = _LLM(_answer())
    dialog = [_msg("in", "aku takut telat mulai kerja remote")]
    delta = await extract_discovery(llm, dialog, LeadDossier(), "id", branch_id=1, thread_id=1)
    merged = merge_dossier(LeadDossier(), delta)
    assert merged.pains == ["takut telat"]
    assert merged.desired_state == ["kerja remote"]


async def test_extraction_never_overwrites_only_adds() -> None:
    """merge_dossier's union semantics: a new phrase is appended, an existing one never
    disappears even if the extractor's re-phrasing of it differs slightly."""
    stored = LeadDossier(pains=["takut telat"], desired_state=["kerja remote"])
    llm = _LLM(_answer(pains=["takut telat", "gaji kecil"], desired_state=["kerja remote"]))
    dialog = [_msg("in", "aku juga khawatir soal gaji kecil")]
    delta = await extract_discovery(llm, dialog, stored, "id", branch_id=1, thread_id=1)
    merged = merge_dossier(stored, delta)
    assert "takut telat" in merged.pains
    assert "gaji kecil" in merged.pains
    assert merged.desired_state == ["kerja remote"]


async def test_extraction_fails_soft_on_broken_response() -> None:
    llm = _LLM("not json at all")
    dialog = [_msg("in", "halo")]
    delta = await extract_discovery(llm, dialog, LeadDossier(), "id", branch_id=1, thread_id=1)
    assert delta == LeadDossier()


async def test_extraction_fails_soft_on_timeout() -> None:
    llm = _LLM(raises=TimeoutError("broker still pending"))
    dialog = [_msg("in", "halo")]
    delta = await extract_discovery(llm, dialog, LeadDossier(), "id", branch_id=1, thread_id=1)
    assert delta == LeadDossier()


async def test_extraction_skipped_on_empty_dialog() -> None:
    llm = _LLM(_answer())
    delta = await extract_discovery(llm, [], LeadDossier(), "id", branch_id=1, thread_id=1)
    assert delta == LeadDossier()
    assert llm.capabilities == []  # never called — nothing to read


async def test_extraction_captures_objections_too() -> None:
    llm = _LLM(_answer(objections=["takut ga dapat kerja"]))
    dialog = [_msg("in", "aku takut ga dapat kerja setelah lulus")]
    delta = await extract_discovery(llm, dialog, LeadDossier(), "id", branch_id=1, thread_id=1)
    assert [o.text for o in delta.objections] == ["takut ga dapat kerja"]
