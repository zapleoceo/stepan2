"""Run the same conversations through v2 and v3 and print them side by side.

Scenarios are the failure modes visible in branch 1's live data, not invented ones:
1710 leads wrote exactly once and never came back, and 71% of openers on 2026-07-22 were the
canned campus line that measured a 35.8% reply rate against 47.7% for a real answer.

Runs on the SANDBOX branch only (8 / ClodeCouch) through the real reply path — never branch 1.

    docker compose run --rm --no-deps -e PYTHONPATH=/app api python scripts/sim_v3_compare.py
"""
from __future__ import annotations

import asyncio
import sys

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.sim import SimService
from app.modules.settings import service as settings_service
from app.modules.settings.repository import SettingRepo

SANDBOX_BRANCH = 8  # ClodeCouch. NEVER 1 — that is the live Indonesian branch.

# (key, [lead turns]) — each list is one conversation.
SCENARIOS: list[tuple[str, list[str]]] = [
    # The exact ad prefill that produced the boilerplate opener 71% of the time.
    ("price_first", ["Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?"]),
    ("bare_hello", ["Iya"]),
    ("haggler", ["berapa harga kursus programming?", "diskon dong kak"]),
    ("parent_gate", ["saya masih SMA, mau ikut kelas coding",
                     "mau tanya orang tua dulu ya"]),
    ("soft_no", ["berapa biayanya?", "hmm saya pikir-pikir dulu deh"]),
    ("blunt_no", ["info kursus dong", "nggak usah, makasih"]),
    ("youtube_free", ["mau belajar coding", "di youtube gratis kok, ngapain bayar"]),
    ("job_guarantee", ["abis kursus dijamin dapat kerja nggak?"]),
    ("no_time", ["tertarik kelas cyber security", "tapi saya kerja, nggak ada waktu"]),
    ("ready_to_buy", ["mau daftar kelas vibe coding, gimana caranya?"]),
]

# The two automatic checks worth making on an opener; everything else needs human eyes.
_BOILERPLATE = ("menara sudirman", "kampus kami", "kampus kita")
_STUB = ("pastikan dulu ke tim", "aku kabari secepatnya")


async def _set_engine(engine: str) -> None:
    async with session_scope() as session:
        await SettingRepo(session).upsert(
            "reply_engine", engine, branch_id=SANDBOX_BRANCH)
        await session.commit()
    settings_service.invalidate(SANDBOX_BRANCH)


async def _run(engine: str, key: str, turns: list[str]) -> list[str]:
    await _set_engine(engine)
    replies: list[str] = []
    async with session_scope() as session:
        sim = SimService(session, BrokerLLM())
        await sim.reset(SANDBOX_BRANCH, f"{engine}_{key}")
        for turn in turns:
            result = await sim.say(SANDBOX_BRANCH, f"{engine}_{key}", turn)
            replies.append(result.get("reply") or f"[no reply: {result.get('detail')}]")
        await session.commit()
    return replies


def _flags(reply: str) -> str:
    low = reply.lower()
    marks = []
    if any(b in low for b in _BOILERPLATE):
        marks.append("BOILERPLATE")
    if any(s in low for s in _STUB):
        marks.append("STUB")
    return (" ⚠ " + ", ".join(marks)) if marks else ""


async def main() -> int:
    if SANDBOX_BRANCH == 1:
        print("refusing to run against the live branch")
        return 1
    for key, turns in SCENARIOS:
        print("=" * 78)
        print(f"SCENARIO: {key}")
        try:
            v2, v3 = await _run("v2", key, turns), await _run("v3", key, turns)
        except Exception as exc:  # noqa: BLE001 — one broken scenario must not stop the sweep
            print(f"  failed: {exc}")
            continue
        for i, turn in enumerate(turns):
            print(f"\n  LEAD: {turn}")
            for label, replies in (("v2", v2), ("v3", v3)):
                reply = replies[i] if i < len(replies) else "[missing]"
                print(f"  {label}: {reply}{_flags(reply)}")
    await _set_engine("v3")  # leave the sandbox on the engine under test
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
