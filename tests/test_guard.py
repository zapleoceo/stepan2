"""Reply-guard: ungrounded-URL detection + regenerate-once + safe hand-off on fabrication."""
from __future__ import annotations

import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import AppSetting, Branch  # noqa: E402
from app.modules.conversation import guard  # noqa: E402
from app.modules.conversation.sim import SimService  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

_FAKE_LINK = "https://lab.itstep.id/cybersecurity-practice?access=HANDAYANI2024"


# ─── deterministic URL grounding ────────────────────────────────────────────────

def test_ungrounded_url_flagged_grounded_allowed() -> None:
    ctx = "Program details. Source: https://itstep.id/vibe-coding is the fact base."
    assert guard.ungrounded_urls(f"cek di {_FAKE_LINK} ya", ctx) == [_FAKE_LINK]  # invented
    assert guard.ungrounded_urls("lihat https://itstep.id/vibe-coding", ctx) == []  # in KB
    assert guard.ungrounded_urls("kunjungi https://itstep.id", ctx) == []  # bare official site


def test_is_risky_detects_offers_and_links() -> None:
    assert guard.is_risky("aku kirim link akses lab gratis ya")
    assert guard.is_risky(f"ini {_FAKE_LINK}")
    assert not guard.is_risky("Vibe Coding harganya 13 juta, bisa dicicil.")


# ─── integration through the real reply path (SimService) ───────────────────────

class _ScriptLLM:
    """Returns decision JSONs in sequence; embed is a no-op. Simulates the model first
    fabricating, then (or not) fixing on the guard's corrective regeneration."""

    def __init__(self, *replies: str) -> None:
        self._q = list(replies)
        self.chats = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.chats += 1
        r = self._q.pop(0) if self._q else self._q_last
        self._q_last = r
        payload = {"reply": r, "stage": "qualifying", "jobs": [], "pains": [], "gains": []}
        return json.dumps(payload), {"model": "deepseek/deepseek-chat", "cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _branch(s) -> int:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(AppSetting(branch_id=b.id, key="reply_guard", value="urls"))  # deterministic path
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_guard_regenerates_away_a_fabricated_link(db_session) -> None:
    bid = await _branch(db_session)
    llm = _ScriptLLM(f"Coba akses lab di {_FAKE_LINK} ya Kak",  # 1st draft: fabricated link
                     "Boleh Kak, aku bantu langsung di sini aja ya 😊")  # regen: clean
    out = await SimService(db_session, llm).say(bid, "g1", "boleh kirim akses lab?")
    assert out["ok"] and _FAKE_LINK not in out["reply"]           # fabrication removed
    assert "aku bantu langsung" in out["reply"]                   # the clean regen was used


async def test_guard_hands_off_when_fabrication_persists(db_session) -> None:
    bid = await _branch(db_session)
    llm = _ScriptLLM(f"ini linknya {_FAKE_LINK}", f"beneran kok {_FAKE_LINK}")  # both bad
    out = await SimService(db_session, llm).say(bid, "g2", "kirim link lab dong")
    assert out["ok"] and _FAKE_LINK not in out["reply"]
    assert out["reply"] == guard.SAFE_FALLBACK and out["needs_manager"] is True  # handed off
