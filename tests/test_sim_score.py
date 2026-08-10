"""Счётчик качества диалога — основа сравнения раундов.

Пороги взяты с боя: 1869 исходящих филиала 1 за 03–10.08.2026, средняя длина 268 знаков,
медиана 240, длиннее 400 знаков 16.3%, «Kakak tertarik?» 124 раза (6.6% всех сообщений).
"""
from __future__ import annotations

from app.modules.quality.sim_score import score_chat, score_turn, summarize


def test_a_stock_closer_is_caught() -> None:
    """«Kakak tertarik?» — самая частая тупиковая концовка филиала, 124 раза за неделю."""
    t = score_turn("Programnya bagus lho. Kakak tertarik?")
    assert t.stock == ["tertarik_dead_end"]


def test_a_menu_of_two_programmes_is_a_false_choice() -> None:
    """Худший ход раунда r1: лид сказал «хочу быть контент-креатором» — то есть направление
    назвал, — а получил выбор из двух программ. По Биркенбиль это ошибка №2: выбор из наших
    вариантов вместо разговора о человеке."""
    t = score_turn("Ada Bootcamp 1 hari atau SMM Intensive 2 minggu. Kakak lebih tertarik "
                   "yang mana?")
    assert t.is_false_choice is True
    assert t.stock == ["tertarik_dead_end"]


def test_asking_what_drew_them_in_is_not_a_stock_phrase() -> None:
    """Тот же корень «tertarik», но это хороший discovery-вопрос. Широкий шаблон считал его
    ошибкой, и метрика увела бы следующие раунды не туда."""
    t = score_turn("Kira-kira yang bikin Kakak tertarik waktu lihat iklan kita itu apa?")
    assert t.stock == []
    assert t.is_false_choice is False


def test_splitting_the_kinds_of_expensive_is_not_a_false_choice() -> None:
    """«Дорого по сумме или дорого относительно бюджета?» — это разбор возражения, ровно то,
    чего мы добиваемся. Союз «или» сам по себе не делает вопрос ложной альтернативой."""
    t = score_turn("Yang bikin belum sanggup ini harga totalnya, atau memang lagi cari yang "
                   "di bawah budget tertentu?")
    assert t.is_false_choice is False


def test_the_shape_of_a_machine_is_caught() -> None:
    """Маркерный список, жирный шрифт и длинное тире — то, чего в личной переписке не бывает."""
    t = score_turn("Detailnya:\n- durasi 2 minggu\n- **harga 1.882.955**\nsemua jelas — ya kak?")
    assert set(t.robot) == {"bullets", "bold", "em_dash"}


def test_a_plain_human_line_trips_nothing() -> None:
    t = score_turn("Boleh tau kakak sekarang kerja di bidang apa?")
    assert t.stock == [] and t.robot == []


def test_bubbles_and_length_ignore_the_separator() -> None:
    """Разделитель пузырей — наш служебный знак, в длину сообщения он не входит."""
    t = score_turn("Halo kak!|||Boleh tau tujuannya apa?")
    assert t.bubbles == 2
    assert t.chars == len("Halo kak! Boleh tau tujuannya apa?")


def test_a_concrete_step_is_told_apart_from_a_dead_end() -> None:
    assert score_turn("Boleh minta nomor WhatsApp-nya kak?").has_next_step is True
    assert score_turn("Ada lagi yang bisa saya bantu?").has_next_step is False


def test_the_stall_reply_is_counted() -> None:
    """Ответ «уточню у команды» — цена выдуманного факта, её надо видеть отдельно."""
    assert score_turn("Bentar ya kak, aku cek dulu ke tim").is_stall is True


# ── свод разговора ────────────────────────────────────────────────────────────


def _run(*replies: str) -> dict:
    out = []
    for r in replies:
        out += [{"who": "lead", "text": "halo"}, {"who": "stepan", "text": r}]
    return {"transcript": out, "reason": "lead_ended", "stage": "qualifying"}


def test_a_repeated_question_is_counted_once_per_repeat() -> None:
    """Один и тот же вопрос дважды — самый читаемый признак, что расшифровку не читают."""
    s = score_chat("p", _run(
        "Boleh tau tujuan kakak apa?", "Oke. Boleh tau tujuan kakak apa?"))
    assert s.repeated_questions == 1


def test_the_same_question_reordered_still_counts() -> None:
    s = score_chat("p", _run("Kakak kerja di bidang apa sekarang?",
                             "Sekarang kakak kerja di bidang apa?"))
    assert s.repeated_questions == 1


def test_two_different_questions_are_not_a_repeat() -> None:
    s = score_chat("p", _run("Boleh tau tujuan kakak apa?",
                             "Kakak sudah pernah pegang akun bisnis sebelumnya?"))
    assert s.repeated_questions == 0


def test_long_share_uses_the_four_hundred_mark() -> None:
    s = score_chat("p", _run("x" * 401, "pendek aja"))
    assert s.long_share == 0.5
    assert s.max_chars == 401


def test_a_round_summary_adds_up_across_chats() -> None:
    a = score_chat("a", _run("Programnya bagus. Kakak tertarik?"))
    b = score_chat("b", _run("- satu\n- dua"))
    total = summarize([a, b])
    assert total["chats"] == 2
    assert total["stock_hits"] == 1
    assert total["robot_hits"] == 1
    assert total["stock_kinds"] == ["tertarik_dead_end"]


def test_an_empty_round_summarizes_to_nothing_rather_than_crashing() -> None:
    assert summarize([]) == {}
