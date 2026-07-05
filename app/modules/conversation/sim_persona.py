"""Persona-driven auto-dialogues: an LLM plays a lead of a given archetype and holds a
full conversation with Stepan (the real reply engine) until a natural end — enrol, hand
off, or walk away. For evaluating segmentation, discovery, funnel movement, close, and
anti-fabrication end-to-end. Fully sandboxed (runs on the SIM branch, never sends to IG).

Bounded + resumable: each call runs up to `max_turns` turns and reconstructs state from
the sandbox thread, so the client loops it (well under the gateway timeout) to completion.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.ports.llm import LLMPort

from .sim import SimService

_END = "[END]"
_MAX_TOTAL_TURNS = 12  # absolute stop across resumes, so a chatty persona can't loop forever

# 10 archetypes spanning the segmentation / funnel test matrix.
PERSONAS: dict[str, str] = {
    "hot_ready": "Andi, 25, karyawan startup. Sudah riset Vibe Coding dan yakin mau daftar "
                 "kalau harga & jadwal cocok. Antusias, to-the-point, cepat ambil keputusan, "
                 "budget aman. Kalau puas, kasih nama + nomor WA dan setuju daftar.",
    "budget_student": "Sinta, 20, mahasiswi. Tertarik tapi budget pas-pasan. Terus nanya "
                      "cicilan / diskon pelajar, ragu soal uang, butuh diyakinkan worth-it.",
    "skeptic_diy": "Bagus, 28, programmer junior. Skeptis, mikir bisa belajar sendiri gratis "
                   "di YouTube. Nyinyir, minta bukti kenapa harus bayar. Susah diyakinkan.",
    "confused_explorer": "Maya, 19, lulusan SMA. Bingung mau ambil bidang apa (coding / "
                         "desain / data), belum tau minatnya. Butuh diarahkan admin.",
    "career_switcher": "Rina, 30, kerja di HR, mau pindah karier ke tech. Takut ketuaan & "
                       "ragu soal peluang kerja. Butuh diyakinkan soal outcome karier.",
    "freelancer_upskill": "Handa, 26, freelancer. Mau skill cybersecurity yang bisa dijual "
                          "ke klien, minta akses latihan / lab / tools buat praktik sekarang.",
    "parent_for_child": "Ibu Dewi, 45, nanya buat anaknya (16 th) yang suka komputer. Fokus "
                        "ke masa depan anak, biaya, dan apakah cocok untuk usia anak.",
    "corporate_bulk": "Pak Yusuf, manajer, mau training tim 8 orang perusahaan. Nanya paket "
                      "korporat, invoice, dan diskon grup.",
    "ghoster_busy": "Tomi, 32, sibuk. Jawab singkat-singkat, gampang ilang minat kalau admin "
                    "kebanyakan nanya atau balasannya kepanjangan.",
    "wrong_fit": "Fajar, 22, nyari sesuatu yang IT STEP kemungkinan nggak punya — misal kelas "
                 "'AI robotics / IoT hardware'. Kalau ternyata nggak ada, lihat apa admin jujur.",
}

_ACTOR_SYSTEM = (
    "You role-play an Instagram lead DMing a course-sales admin (Stepan) at IT STEP Jakarta. "
    "Write ONLY casual Bahasa Indonesia, SHORT (1-2 sentences), like a real DM. Stay fully in "
    "character; never say you are an AI, never narrate. React naturally to the admin's last "
    "message — ask, object, or decide as your persona would. Output exactly {end} alone when "
    "you have either (a) given your name + WA number and agreed to enrol / take the next step, "
    "or (b) decided you're not interested and want to stop.\n\nYOUR PERSONA: {persona}"
)


class LeadActor:
    def __init__(self, llm: LLMPort, persona: str, branch_id: int) -> None:
        self.llm = llm
        self.system = _ACTOR_SYSTEM.format(persona=persona, end=_END)
        self.branch_id = branch_id

    async def next(self, transcript: list[tuple[str, str]]) -> str:
        msgs = [{"role": "system", "content": self.system}]
        for who, txt in transcript:
            msgs.append({"role": "user" if who == "stepan" else "assistant", "content": txt})
        if not transcript or transcript[-1][0] == "lead":
            msgs.append({"role": "user",
                         "content": "[Mulai / lanjutkan chat — kirim pesan Kakak berikutnya.]"})
        raw, _ = await self.llm.chat(
            msgs, capability="chat:fast", max_tokens=160, temperature=0.9,
            workflow="sim_lead", branch_id=self.branch_id)
        return (raw or "").strip()


async def _load_dialog(session: AsyncSession, key: str) -> list[tuple[str, str]]:
    rows = (await session.execute(
        text("SELECT m.direction, m.text FROM message m JOIN channel_thread ct"
             " ON ct.id = m.thread_id WHERE ct.external_thread_id = :e AND m.text <> ''"
             " ORDER BY m.occurred_at, m.id"), {"e": f"sim:{key}"})).all()
    return [("stepan" if d == "out" else "lead", t) for d, t in rows]


async def run_persona(
    session: AsyncSession, branch_id: int, persona_key: str, session_key: str,
    llm: LLMPort, max_turns: int = 3,
) -> dict:
    """Advance a persona conversation up to max_turns; resumable from the thread state."""
    persona = PERSONAS.get(persona_key)
    if persona is None:
        return {"ok": False, "detail": f"unknown persona {persona_key}"}
    svc = SimService(session, llm)
    actor = LeadActor(llm, persona, branch_id)
    transcript = await _load_dialog(session, session_key)
    ended, reason, last = False, None, {}
    for _ in range(max_turns):
        if len(transcript) >= _MAX_TOTAL_TURNS * 2:
            ended, reason = True, "max_turns"
            break
        lead_msg = await actor.next(transcript)
        if _END in lead_msg or not lead_msg.strip():
            ended, reason = True, "lead_ended"
            break
        r = await svc.say(branch_id, session_key, lead_msg)
        if not r.get("ok"):
            ended, reason = True, r.get("detail")
            break
        transcript += [("lead", lead_msg), ("stepan", r["reply"])]
        last = r
        if r.get("ready") or r.get("needs_manager"):
            ended, reason = True, "ready" if r.get("ready") else "handoff"
            break
    return {
        "ok": True, "ended": ended, "reason": reason,
        "turns_total": len(transcript) // 2,
        "transcript": [{"who": w, "text": t} for w, t in transcript],
        "stage": last.get("stage"), "product": last.get("product"),
        "ready": last.get("ready"), "needs_manager": last.get("needs_manager"),
        "jobs": last.get("jobs"), "pains": last.get("pains"), "gains": last.get("gains"),
    }
