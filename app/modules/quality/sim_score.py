"""Детерминированный замер качества симулированного диалога.

Всё, что можно посчитать без второй LLM, считается здесь: длина, штампы, признаки «писал
робот», следующий шаг, повтор вопросов, срабатывание триггеров воронки. Судья-LLM берёт на
себя только то, что счётчиком не берётся (см. sim_judge).

Базовые доли по 1869 исходящим филиала 1 за 03–10.08.2026 — с чем сравнивать:
средняя длина 268 знаков, медиана 240, длиннее 400 знаков 16.3%, «Kakak tertarik?» 6.6%,
«ada lagi yang bisa dibantu» 2.1%, маркерный список 2.0%, жирный шрифт 1.7%.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# Обороты, по которым переписку узнают как машинную. Не «плохие слова», а признаки бланка:
# каждый из них закрывает разговор вместо того, чтобы двигать его. «Kakak tertarik?» —
# 124 раза за неделю, самая частая тупиковая концовка филиала.
_STOCK = {
    # Именно тупиковая форма: «интересно?» и «который из них?». Раунд r1 показал, что широкий
    # шаблон на «tertarik» ловит и хорошие ходы — «что вас зацепило в рекламе», «дорого по
    # сумме или по бюджету». Из шести срабатываний плохим было одно. Метрика, считающая
    # верные ходы ошибками, увела бы следующие раунды не туда.
    "tertarik_dead_end": re.compile(r"tertarik\s*\?|tertarik\s+yang\s+mana", re.I),
    "ada_lagi_yang_bisa": re.compile(r"ada\s+(lagi\s+)?yang\s+bisa\s+(saya\s+|aku\s+)?bantu", re.I),
    "jangan_ragu": re.compile(r"jangan\s+ragu", re.I),
    "apakah_kakak": re.compile(r"\bapakah\s+kakak", re.I),
    "silakan": re.compile(r"\bsilakan\b", re.I),
    "senang_hati": re.compile(r"senang\s+hati", re.I),
    "semoga_membantu": re.compile(r"semoga\s+.{0,20}membantu", re.I),
    "bagaimana_menurut": re.compile(r"bagaimana\s+menurut", re.I),
}
# Вёрстка, которой в личке не бывает: маркеры, жирный, длинное тире.
_ROBOT_SHAPE = {
    "bullets": re.compile(r"(^|\n)\s*[•\-*]\s+", re.M),
    "bold": re.compile(r"\*\*?[^\s*][^*]*\*\*?"),
    "em_dash": re.compile(r"—"),
}
# Конкретный следующий шаг: номер, запись, встреча, ссылка, выбор даты.
_NEXT_STEP = re.compile(
    r"(wa\.me|whatsapp|nomor|nomer|no\s*hp|daftar|booking|pesan\s+tempat|amankan|"
    r"jadwal|datang|ikut|kirim(kan)?\s+(detail|ringkasan|brosur))", re.I)
_STALL = re.compile(
    r"(cek\s+dulu\s+ke\s+tim|tanya(kan)?\s+dulu\s+ke\s+tim|konfirmasi\s+ke\s+tim)", re.I)
# Ложная альтернатива: «A или B — что выбираете?». По Биркенбиль это ошибка №2 — выбор из
# двух наших вариантов вместо открытого вопроса о человеке. В r1 так выглядел худший ход
# раунда: лид сказал «хочу быть контент-креатором», а получил меню из двух программ.
_FALSE_CHOICE = re.compile(
    r"\batau\b[^?]{0,120}(yang\s+mana|lebih\s+(tertarik|cocok|suka)|pilih)[^?]{0,40}\?", re.I)
_LONG = 400
_BUBBLE = "|||"


@dataclass
class TurnScore:
    chars: int
    bubbles: int
    questions: int
    stock: list[str] = field(default_factory=list)
    robot: list[str] = field(default_factory=list)
    has_next_step: bool = False
    is_stall: bool = False
    is_false_choice: bool = False


@dataclass
class ChatScore:
    persona: str
    turns: int
    ended: str | None
    stage: str | None
    product: str | None
    ready: bool
    needs_manager: bool
    # стиль
    avg_chars: float
    median_chars: float
    max_chars: int
    long_share: float
    avg_bubbles: float
    # штампы и вёрстка
    stock_hits: int
    robot_hits: int
    stock_kinds: list[str]
    # ведение
    next_step_share: float
    stall_turns: int
    false_choice_turns: int
    repeated_questions: int
    multi_question_turns: int

    def as_dict(self) -> dict:
        return asdict(self)


def score_turn(reply: str) -> TurnScore:
    text = reply or ""
    flat = text.replace(_BUBBLE, " ")
    return TurnScore(
        chars=len(flat.strip()),
        bubbles=text.count(_BUBBLE) + 1 if text.strip() else 0,
        questions=flat.count("?"),
        stock=[k for k, rx in _STOCK.items() if rx.search(flat)],
        robot=[k for k, rx in _ROBOT_SHAPE.items() if rx.search(text)],
        has_next_step=bool(_NEXT_STEP.search(flat)),
        is_stall=bool(_STALL.search(flat)),
        is_false_choice=bool(_FALSE_CHOICE.search(flat)),
    )


def score_chat(persona: str, run: dict) -> ChatScore:
    """Свести один прогон персоны в набор чисел."""
    replies = [m["text"] for m in run.get("transcript", []) if m["who"] == "stepan"]
    turns = [score_turn(r) for r in replies]
    lens = sorted(t.chars for t in turns) or [0]
    n = len(turns) or 1
    return ChatScore(
        persona=persona,
        turns=len(turns),
        ended=run.get("reason"),
        stage=run.get("stage"),
        product=run.get("product"),
        ready=bool(run.get("ready")),
        needs_manager=bool(run.get("needs_manager")),
        avg_chars=round(sum(lens) / n, 1),
        median_chars=float(lens[len(lens) // 2]),
        max_chars=max(lens),
        long_share=round(sum(1 for x in lens if x > _LONG) / n, 3),
        avg_bubbles=round(sum(t.bubbles for t in turns) / n, 2),
        stock_hits=sum(len(t.stock) for t in turns),
        robot_hits=sum(len(t.robot) for t in turns),
        stock_kinds=sorted({k for t in turns for k in t.stock}),
        next_step_share=round(sum(1 for t in turns if t.has_next_step) / n, 3),
        stall_turns=sum(1 for t in turns if t.is_stall),
        false_choice_turns=sum(1 for t in turns if t.is_false_choice),
        repeated_questions=_repeated_questions(replies),
        multi_question_turns=sum(1 for t in turns if t.questions > 1),
    )


def _repeated_questions(replies: list[str]) -> int:
    """Один и тот же вопрос, заданный дважды за разговор — самый читаемый признак того, что
    расшифровку никто не читает (строка 7 чеклиста)."""
    seen: set[str] = set()
    repeats = 0
    for reply in replies:
        for raw in re.findall(r"[^.!?|]*\?", reply.replace(_BUBBLE, " ")):
            key = re.sub(r"[^a-z ]", "", raw.lower()).strip()
            key = " ".join(sorted(key.split()))  # порядок слов не спасает от повтора смысла
            if len(key) < 12:
                continue
            if key in seen:
                repeats += 1
            seen.add(key)
    return repeats


def summarize(scores: list[ChatScore]) -> dict:
    """Итог раунда — то, что сравнивают между раундами."""
    if not scores:
        return {}
    n = len(scores)
    return {
        "chats": n,
        "turns_total": sum(s.turns for s in scores),
        "avg_chars": round(sum(s.avg_chars for s in scores) / n, 1),
        "median_chars": round(sum(s.median_chars for s in scores) / n, 1),
        "max_chars": max(s.max_chars for s in scores),
        "long_share": round(sum(s.long_share for s in scores) / n, 3),
        "avg_bubbles": round(sum(s.avg_bubbles for s in scores) / n, 2),
        "stock_hits": sum(s.stock_hits for s in scores),
        "robot_hits": sum(s.robot_hits for s in scores),
        "stock_kinds": sorted({k for s in scores for k in s.stock_kinds}),
        "next_step_share": round(sum(s.next_step_share for s in scores) / n, 3),
        "stall_turns": sum(s.stall_turns for s in scores),
        "false_choice_turns": sum(s.false_choice_turns for s in scores),
        "repeated_questions": sum(s.repeated_questions for s in scores),
        "multi_question_turns": sum(s.multi_question_turns for s in scores),
        "ready": sum(1 for s in scores if s.ready),
        "handoffs": sum(1 for s in scores if s.needs_manager),
    }
