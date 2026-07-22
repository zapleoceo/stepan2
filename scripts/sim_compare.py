"""Run the sales scenarios through the real reply path and print what Stepan says.

The scenarios are the failure modes visible in branch 1's live data, not invented ones: the ad
prefill that produced canned boilerplate in 71% of openers, the bare "iya", the haggler, the
parents gate, soft and blunt refusals, the YouTube-is-free objection, the job-guarantee
question. 1710 leads wrote exactly once and never came back, so the opener is most of the loss.

Runs on the SANDBOX branch only (8 / ClodeCouch) — never branch 1.

    docker compose run --rm --no-deps -v /var/www/stepan2/scripts:/app/scripts \
        -e PYTHONPATH=/app api python /app/scripts/sim_compare.py
"""
from __future__ import annotations

import asyncio
import sys

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.sim import SimService

SANDBOX_BRANCH = 8  # ClodeCouch. NEVER 1 — that is the live Indonesian branch.

SCENARIOS: list[tuple[str, list[str]]] = [
    ("price_first", ["Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?"]),
    ("bare_hello", ["Iya"]),
    ("haggler", ["berapa harga kursus programming?", "diskon dong kak"]),
    ("parent_gate", ["saya masih SMA, mau ikut kelas coding", "mau tanya orang tua dulu ya"]),
    ("soft_no", ["berapa biayanya?", "hmm saya pikir-pikir dulu deh"]),
    ("blunt_no", ["info kursus dong", "nggak usah, makasih"]),
    ("youtube_free", ["mau belajar coding", "di youtube gratis kok, ngapain bayar"]),
    ("job_guarantee", ["abis kursus dijamin dapat kerja nggak?"]),
    ("no_time", ["tertarik kelas cyber security", "tapi saya kerja, nggak ada waktu"]),
    ("ready_to_buy", ["mau daftar kelas vibe coding, gimana caranya?"]),
]

# The two failures worth flagging automatically; everything else needs human eyes.
_BOILERPLATE = ("menara sudirman", "kampus kami", "kampus kita")
_STUB = ("pastikan dulu ke tim", "aku kabari secepatnya")


def _flags(reply: str) -> str:
    low = reply.lower()
    marks = [name for name, needles in (("BOILERPLATE", _BOILERPLATE), ("STUB", _STUB))
             if any(n in low for n in needles)]
    return (" [!] " + ", ".join(marks)) if marks else ""


async def _run(key: str, turns: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    async with session_scope() as session:
        sim = SimService(session, BrokerLLM())
        await sim.reset(SANDBOX_BRANCH, key)
        for turn in turns:
            result = await sim.say(SANDBOX_BRANCH, key, turn)
            out.append((turn, result.get("reply") or f"[no reply: {result.get('detail')}]"))
        await session.commit()
    return out


async def main() -> int:
    if SANDBOX_BRANCH == 1:
        print("refusing to run against the live branch")
        return 1
    for key, turns in SCENARIOS:
        print("=" * 78)
        print(f"SCENARIO: {key}")
        try:
            exchanges = await _run(key, turns)
        except Exception as exc:  # noqa: BLE001 — one broken scenario must not stop the sweep
            print(f"  failed: {exc}")
            continue
        for lead_said, stepan_said in exchanges:
            print(f"\n  LEAD:   {lead_said}")
            print(f"  STEPAN: {stepan_said}{_flags(stepan_said)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
