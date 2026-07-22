"""Reply-guard tier-2 (LLM grounding verify) — the DEFAULT reply_guard='full' path.

Every other guard test seeds reply_guard='urls' (deterministic only), so verify_grounding —
the selective LLM fabrication check that ships in production — was never exercised. These pin
(1) an LLM-flagged claim on a risky-but-deterministically-clean reply triggers the corrective
regen, and (2) a RAISING verifier LLM fails OPEN (returns []), sending the draft rather than
blocking — a documented, dangerous default that a refactor must not silently flip to fail-closed.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import AppSetting, Branch, KnowledgeDoc  # noqa: E402
from app.modules.conversation.sim import SimService  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

# Risky (a price → guard.is_risky True) but deterministically clean (no url / false-delivery /
# double question), so ONLY the tier-2 LLM verify can flag it — isolating the 'full' path.
_RISKY_DRAFT = "Biayanya cuma 5 juta kak"
_CLEAN_DRAFT = "Boleh aku bantu jelaskan langsung di sini ya kak"


class _FullModeLLM:
    """Splits generation from the guard verify by the workflow kwarg: workflow='guard' is the
    tier-2 verify call (scripted response, or raise for the fail-open test); anything else is a
    reply-generation call returning the next decision JSON."""

    def __init__(self, drafts: list[str], verify: object) -> None:
        self._drafts = list(drafts)
        self._verify = verify
        self.verify_calls = 0
        self.gen_calls = 0
        self._last = ""

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        if kw.get("workflow") == "guard":
            self.verify_calls += 1
            if isinstance(self._verify, Exception):
                raise self._verify
            return self._verify, {"model": "v", "cost_usd": 0.0}
        self.gen_calls += 1
        self._last = self._drafts.pop(0) if self._drafts else self._last
        payload = {"reply": self._last, "stage": "qualifying", "jobs": [], "pains": [],
                   "gains": []}
        return json.dumps(payload), {"model": "gen", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _full_branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000 via transfer BCA atau QRIS."))
    # v2 behaviour — these tests retire with the engine
    s.add(AppSetting(branch_id=b.id, key="reply_engine", value="v2"))
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="full"))  # tier-2 LLM verify ON
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_full_mode_llm_flagged_claim_triggers_regen(db_session) -> None:
    bid = await _full_branch(db_session)
    # verify flags the price claim → corrective regen → the clean draft ships.
    llm = _FullModeLLM([_RISKY_DRAFT, _CLEAN_DRAFT], verify="- harga 5 juta tidak ada di KB")
    out = await SimService(db_session, llm).say(bid, "gf1", "berapa biayanya kak?")
    assert out["ok"]
    assert llm.verify_calls == 1          # tier-2 actually ran
    assert out["reply"] == _CLEAN_DRAFT   # the flagged draft was regenerated away
    assert llm.gen_calls == 2             # initial draft + one corrective regen


async def test_full_mode_verifier_error_fails_open(db_session) -> None:
    bid = await _full_branch(db_session)
    llm = _FullModeLLM([_RISKY_DRAFT], verify=RuntimeError("broker 502 during verify"))
    out = await SimService(db_session, llm).say(bid, "gf2", "berapa biayanya kak?")
    assert out["ok"]
    assert llm.verify_calls == 1          # verify was attempted
    assert out["reply"] == _RISKY_DRAFT   # fail-OPEN: the draft ships unblocked
    assert llm.gen_calls == 1             # no regen — verify returned [] on its exception
