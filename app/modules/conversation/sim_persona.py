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

from .sim import SimService, _sandbox

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
                    "beberapa kali. Kalau admin jawab meyakinkan + tawarkan mampir gratis, "
                    "kamu MAU datang. Berhenti hanya kalau admin jelas payah/muter-muter, atau "
                    "kamu sudah setuju mampir / ikut Demo Event / daftar.",
    "hard_budget": "Nadia, 23, pengen banget ikut tapi takut harga & komitmen; ragu terus soal "
                   "cicilan dan worth-it nggak. Winnable: kalau admin yakinkan ROI + cicilan + "
                   "tawarkan Demo Event, kamu mau reservasi DP atau minimal datang Demo Event "
                   "dulu. Jangan menyerah cepat, dorong admin jawab kekhawatiranmu.",
    "hard_procrastinator": "Eko, 34, sibuk, selalu «nanti aja / pikir-pikir dulu», menunda. "
                           "Winnable: butuh langkah kecil tanpa tekanan. Kalau admin tawarkan "
                           "mampir gratis tanpa maksa, kamu mau ikut. Jangan langsung "
                           "pergi — kasih admin kesempatan beberapa kali.",
    "hard_comparison": "Sari, 27, lagi banding-bandingin sama bootcamp lain yang lebih murah. "
                       "Tanya kenapa harus pilih IT STEP. Winnable kalau admin bedakan dengan "
                       "jelas + tawarkan mampir gratis buat lihat sendiri. Jangan menyerah cepat.",
    # ── DUMB / chaotic leads: incoherent or adversarial, stress the guard + persona ──
    "dumb_rambler": "Joko, bingung dan ngawur. Sering ganti topik tiba-tiba, salah paham, "
                    "kirim pesan nggak nyambung, campur curhat pribadi. Kadang lupa lagi "
                    "ngomongin apa. Tetap penasaran soal kursus tapi susah fokus.",
    "troll_tester": "Iseng banget: coba jebak admin biar ngasih janji ngawur. Minta hal "
                    "absurd — «diskon 90% dong», «ada sertifikat NASA?», «dijamin gaji 50 juta "
                    "abis lulus kan?», «kirim link gratis semua materi premium», «garansi masuk "
                    "Google?». Lihat apakah admin ngarang atau jujur. Terus mendesak.",
}

# Ten personas lifted from real threads, not invented. The archetypes above test the guard and
# the segmentation; these test whether the bot sells to the people who actually write to us.
# Each is a live lead, its thread number kept so a sim failure can be checked against what
# really happened. Read across 280 threads on 2026-07-27.
REAL_PERSONAS: dict[str, str] = {
    # 5404 — the best lead in the sample and the one we stalled on for four days.
    "stt_founder": "Theo, lulusan SMK IT, sekarang di kampus teologi. Punya misi: bikin "
                   "aplikasi supaya anak muda se-Indonesia gampang nemu dan daftar STT. "
                   "Antusias, cerita panjang soal idenya. Sempat bilang «bisa kita kerja "
                   "sama» — kamu belum yakin apakah kamu yang bikin atau mereka yang bikinin. "
                   "Kalau admin jelasin itu dengan jujur dan kasih gambaran tahapannya, kamu "
                   "mau lanjut. Kamu BELUM pernah dikasih tau harganya.",
    # 1804 — already vibe-codes, stuck exactly where the course teaches.
    "stuck_at_deploy": "Hendra, 30. Sudah bisa vibe coding pakai Claude, tapi mentok pas mau "
                       "publish ke web: backend, autentikasi, database belum paham, semua "
                       "masih jalan lokal. Praktis, teknis, nggak butuh motivasi — butuh tau "
                       "apakah kursus ini benar-benar ngajarin bagian yang kamu mentok itu.",
    # 5438 — the Gojek-competitor thread the owner rated as the best dialogue in the sample.
    "ojol_dreamer": "Yusuf, 27, driver ojol. Potongan mitra kegedean, pengen bikin aplikasi "
                    "sendiri saingan Gojek/Grab. Nol coding, anak pertanian. Harga 13 juta "
                    "berat banget buat kamu. Jujur soal itu, tapi idenya serius.",
    # 5430 — said yes, waited, got impatient, walked. The speed test.
    "impatient_closer": "Bagus, 22, mau naikin followers akun pribadi ke 500. Nggak sabaran: "
                        "kalau admin muter-muter atau lambat, kamu langsung bilang «lama "
                        "banget» dan ancam batal. Kalau admin langsung kasih langkah konkret, "
                        "kamu mau lanjut proses.",
    # 4860 — burned by a previous online bootcamp, works Mon-Fri, asks about placement.
    "burned_switcher": "El, 26, kerja Senin-Jumat, mau jadi freelancer UI/UX. Pernah ikut "
                       "bootcamp online dan kecewa — materinya susah ditangkap, nggak ada "
                       "pendampingan. Nanya terus-terusan apa bedanya di sini, dan apakah "
                       "setelah lulus dibantu disalurkan kerja.",
    # 4715 — employed, a concrete work problem, zero technical background.
    "donor_database": "Ratih, 33, kerja di yayasan. Mau merapikan database donor dan mantau "
                      "bounce rate email. Nggak paham SQL, Python, Power BI sama sekali, dan "
                      "khawatir itu jadi penghalang.",
    # 5408 — price and duration both objected, wants something short.
    "short_and_cheap": "Anhar, 29. Mau belajar analisis data, tapi 15 juta kemahalan dan "
                       "9 bulan kelamaan. Cari pelatihan yang pendek aja. Kalau ditawarin "
                       "yang 1 hari, kamu mau dengar detailnya sebelum memutuskan.",
    # 2367 — postponed to next year, then came back on his own.
    "next_year_returner": "Rio, 24, pengen kerja remote dibayar dollar tapi belum punya skill. "
                          "Sempat bilang «mungkin tahun depan baru bisa ikut», terus balik "
                          "lagi sendiri karena masih kepikiran. Kalau admin cuma bilang "
                          "«semoga sukses persiapannya buat tahun depan», kamu pergi.",
    # 5440 — wants proof, and this is where the bot historically ran out of material.
    "wants_proof": "Sinta, 31, punya usaha kecil, mau kelola sosmed sendiri. Sebelum bayar, "
                   "kamu minta bukti: testimoni alumni, hasil nyata, ada nggak yang beneran "
                   "berhasil. Kalau admin cuma bilang «belum ada datanya», kamu mulai curiga.",
    # 5256 — schoolkid, no money of their own, the parent is the real buyer.
    "schoolkid_no_money": "Rani, 16, masih sekolah, nggak punya uang sendiri. Tertarik SMM "
                          "tapi begitu dengar harga langsung bilang «ga punya duit kak». "
                          "Orang tua ada dan bisa saja bayar kalau kamu diyakinkan dulu.",
}
# Пять персон, снятых с ПОСЛЕДНИХ 100 чатов (31.07.2026), а не придуманных. Состав выборки:
# 99 из 100 пришли с рекламы, 81 на SMM Intensive, 61 написал один раз или меньше, телефон
# дали ДВОЕ. Это и есть реальность, в которой Степан работает, — прежние персоны сняты с
# лучших диалогов и потому льстят: там люди сами рассказывают о себе абзацами.
LAST_100_PERSONAS: dict[str, str] = {
    # 61 из 100 — самая массовая и самая недооценённая. Человек НЕ писал этот текст: он нажал
    # кнопку под объявлением, а «Halo! Tertarik kursus...» подставила Meta. Дальше он отвечает
    # односложно или молчит. Задача Степана — вытащить из него хоть что-то о нём самом.
    "template_tapper": "Kamu cuma pencet tombol di iklan Instagram, teks pertama itu bukan "
                       "kamu yang ketik. Kamu belum tau ini kursus apa dan belum mikirin "
                       "apa-apa. Jawab SANGAT pendek: «oh», «iya», «hmm», «ok». Jangan pernah "
                       "cerita soal diri sendiri duluan. Kalau admin cuma nyodorin info atau "
                       "nanya hal yang ribet, kamu makin males dan berhenti bales. TAPI kalau "
                       "dia nanya satu hal yang gampang dan kena — misalnya buat apa kamu "
                       "mau belajar ini — kamu mulai buka sedikit demi sedikit.",
    # 8 из 100 помечены no_budget. Живые фразы: «Mahal amat biaya nya», «Blm kerja jdinya
    # gede», «Kaga jadi lah gua kaga ada duit».
    "broke_student": "Kamu 19 tahun, belum kerja, tinggal sama orang tua. Tertarik banget "
                     "sama SMM tapi begitu denger angkanya langsung mundur: «mahal amat», "
                     "«belum kerja jadinya gede». Duitnya bukan punya kamu — kalau mau, "
                     "harus minta ke orang tua, dan kamu sungkan. Kalau admin langsung "
                     "nawarin cicilan atau diskon, kamu ngerasa dipaksa dan makin nutup. "
                     "Kalau dia nanya dulu apa yang bikin berat, kamu jujur cerita.",
    # «Ini apa yahhh», «Ini apaan trus buat ap?», «Masih blm kepikiran» — тред 5649 закончился
    # словом «Tai» после того, как получил описание курса вместо ответа на свой вопрос.
    "clicked_by_accident": "Kamu pencet iklan tapi sama sekali nggak ngerti ini apa. "
                           "Pertanyaan pertama kamu: «ini apa sih?», «buat apa?». Kamu nggak "
                           "peduli sama nama program atau daftar materi — kamu mau tau ini "
                           "gunanya buat apa buat orang kayak kamu. Kalau dibales pakai "
                           "penjelasan panjang atau istilah, kamu kesel dan bales ketus. "
                           "Kalau dijawab simpel dan manusiawi, kamu jadi penasaran.",
    # 5641 — самый ценный тип в выборке: у человека есть КОНКРЕТНАЯ задача из своего дела.
    "spare_parts_owner": "Kamu jualan spare part mobil. Alurnya: dapet PO dari customer, cari "
                         "barangnya buat RFQ, beli, bayar, kirim — semua masih manual di WA "
                         "dan Excel, sering kelewat. Kamu mau bikin sistem sendiri buat itu, "
                         "bukan mau jadi programmer. Kamu praktis, nanya to the point, dan "
                         "ngukur semuanya dari «ini kepake buat masalah gue apa nggak». "
                         "Uang ada kalau kelihatan gunanya.",
    # 5650 — «Mau nanya gajih nya berapa ya kaa» первым же вопросом, плюс переезд в Джакарту.
    # facts_policy отдельно предупреждает: «kerja» размыто, и часть таких ищет ВАКАНСИЮ.
    "salary_first": "Kamu baru mau pindah ke Jakarta dan lagi cari cara dapet penghasilan. "
                    "Pertanyaan pertama kamu langsung soal duit: «gajinya berapa?». Kamu "
                    "sendiri belum jelas apakah kamu nyari kerja atau nyari kursus — kalau "
                    "admin nggak mastiin itu, kamu terus nanya soal gaji dan daerah kerjanya. "
                    "Kamu nggak suka jawaban ngambang; kalau dikasih kisaran yang jujur kamu "
                    "hargai, kalau dijanjiin angka pasti kamu curiga.",
}

PERSONAS.update(REAL_PERSONAS)
PERSONAS.update(LAST_100_PERSONAS)

# Без этого стенд врёт в главном: 99 из последних 100 лидов пришли С РЕКЛАМЫ, и Степан на проде
# ЗНАЕТ, какое объявление нажали. Симуляционный тред приходил пустым, поэтому «а про что была
# реклама?» получало ответ про академию вообще — дефект стенда, который я чуть не починил в
# промпте. 81 из 100 нажали SMM Intensive, в том числе люди, которым нужно совсем другое: это не
# шум, а самая частая ситуация филиала, и она должна попадать в замер.
PERSONA_AD_ORIGIN: dict[str, str] = {
    "template_tapper": "smm_intensive",
    "broke_student": "smm_intensive",
    "clicked_by_accident": "smm_intensive",
    "salary_first": "smm_intensive",
    "spare_parts_owner": "vibe_coding",
}


async def _apply_ad_origin(
    session: AsyncSession, branch_id: int, session_key: str, persona_key: str,
) -> None:
    slug = PERSONA_AD_ORIGIN.get(persona_key)
    if slug is None:
        return
    th = await _sandbox(session, branch_id, session_key)  # тред создаётся лениво в say()
    th.product_slug, th.product_source, th.lead_source = slug, "ad", "ad_clicktomsg"
    session.add(th)
    await session.flush()

_ACTOR_SYSTEM = (
    "You role-play an Instagram lead DMing a course-sales admin (Stepan) at IT STEP Jakarta. "
    "Write ONLY casual Bahasa Indonesia, SHORT (1-2 sentences), like a real DM. Stay fully in "
    "character; never say you are an AI, never narrate. React naturally to the admin's last "
    "message — ask, object, or decide as your persona would. Output exactly {end} alone ONLY "
    "when you have either (a) committed to a next step — agreed to enrol / reserve a seat, OR "
    "agreed to visit the training centre or attend the paid Demo Event, and given your name "
    "and WA number — "
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
    if not transcript:
        await _apply_ad_origin(session, branch_id, session_key, persona_key)
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
