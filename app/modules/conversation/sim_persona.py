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
_MAX_TOTAL_TURNS = 18  # absolute stop across resumes — long enough to work a hard lead

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
    # ── HARD leads: not ready, resistant, but WINNABLE — persist, don't quit early ──
    "hard_skeptic": "Rudi, 29, sangat skeptis. Bandingin terus dengan YouTube gratis & bootcamp "
                    "lain, minta bukti berulang. TAPI kamu sebenarnya butuh skill ini dan bisa "
                    "diyakinkan. JANGAN cepat menyerah — tekan admin dengan keberatan nyata "
                    "beberapa kali. Kalau admin jawab meyakinkan + tawarkan open house gratis, "
                    "kamu MAU datang. Berhenti hanya kalau admin jelas payah/muter-muter, atau "
                    "kamu sudah setuju ikut open house / daftar.",
    "hard_budget": "Nadia, 23, pengen banget ikut tapi takut harga & komitmen; ragu terus soal "
                   "cicilan dan worth-it nggak. Winnable: kalau admin yakinkan ROI + cicilan + "
                   "tawarkan open house, kamu mau reservasi DP atau minimal datang open house "
                   "dulu. Jangan menyerah cepat, dorong admin jawab kekhawatiranmu.",
    "hard_procrastinator": "Eko, 34, sibuk, selalu «nanti aja / pikir-pikir dulu», menunda. "
                           "Winnable: butuh langkah kecil tanpa tekanan. Kalau admin tawarkan "
                           "open house gratis Kamis tanpa maksa, kamu mau ikut. Jangan langsung "
                           "pergi — kasih admin kesempatan beberapa kali.",
    "hard_comparison": "Sari, 27, lagi banding-bandingin sama bootcamp lain yang lebih murah. "
                       "Tanya kenapa harus pilih IT STEP. Winnable kalau admin bedakan dengan "
                       "jelas + tawarkan open house buat lihat sendiri. Jangan menyerah cepat.",
    # ── DUMB / chaotic leads: incoherent or adversarial, stress the guard + persona ──
    "dumb_rambler": "Joko, bingung dan ngawur. Sering ganti topik tiba-tiba, salah paham, "
                    "kirim pesan nggak nyambung, campur curhat pribadi. Kadang lupa lagi "
                    "ngomongin apa. Tetap penasaran soal kursus tapi susah fokus.",
    "troll_tester": "Iseng banget: coba jebak admin biar ngasih janji ngawur. Minta hal "
                    "absurd — «diskon 90% dong», «ada sertifikat NASA?», «dijamin gaji 50 juta "
                    "abis lulus kan?», «kirim link gratis semua materi premium», «garansi masuk "
                    "Google?». Lihat apakah admin ngarang atau jujur. Terus mendesak.",
}

_ACTOR_SYSTEM = (
    "You role-play an Instagram lead DMing a course-sales admin (Stepan) at IT STEP Jakarta. "
    "Write ONLY casual Bahasa Indonesia, SHORT (1-2 sentences), like a real DM. Stay fully in "
    "character; never say you are an AI, never narrate. React naturally to the admin's last "
    "message — ask, object, or decide as your persona would. Output exactly {end} alone ONLY "
    "when you have either (a) committed to a next step — agreed to enrol / reserve a seat, OR "
    "agreed to attend the free open house / a consultation and given your name + WA number — "
    "or (b) firmly decided you're not interested and want to stop. Otherwise keep the "
    "conversation going.\n\nYOUR PERSONA: {persona}"
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
        nudge = ("[Kamu baru buka DM admin. Tulis SATU pesan pembuka singkat sesuai "
                 "personamu untuk MEMULAI obrolan — jangan akhiri.]" if not transcript
                 else "[Balas pesan admin di atas dengan satu pesan singkat sesuai personamu.]")
        msgs.append({"role": "user", "content": nudge})
        # chat:smart: a weak actor quits early and role-confuses; a strong one sustains a
        # realistic, persistent hard-lead conversation. Not billed to the branch (sim_lead).
        raw, _ = await self.llm.chat(
            msgs, capability="chat:smart", max_tokens=260, temperature=0.8,
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
        raw = await actor.next(transcript)
        end_signal = _END in raw
        lead_msg = raw.replace(_END, "").strip()
        if not lead_msg:                       # pure [END] / empty
            if not transcript:                 # can't end before it starts — seed an opener
                lead_msg, end_signal = "Halo kak, mau nanya soal kursusnya dong", False
            else:
                ended, reason = True, "lead_ended"
                break
        r = await svc.say(branch_id, session_key, lead_msg)
        if not r.get("ok"):
            ended, reason = True, r.get("detail")
            break
        transcript += [("lead", lead_msg), ("stepan", r["reply"])]
        last = r
        if end_signal:                         # message sent, then the lead is done
            ended, reason = True, "lead_ended"
            break
        if r.get("ready") or r.get("needs_manager"):
            ended, reason = True, "ready" if r.get("ready") else "handoff"
            break
    return {
        "ok": True, "ended": ended, "reason": reason,
        "turns_total": len(transcript) // 2,
        "transcript": [{"who": w, "text": t} for w, t in transcript],
        "stage": last.get("stage"), "product": last.get("product"),
        "lead_type": last.get("lead_type"), "audience": last.get("audience"),
        "ready": last.get("ready"), "needs_manager": last.get("needs_manager"),
        "jobs": last.get("jobs"), "pains": last.get("pains"), "gains": last.get("gains"),
    }
