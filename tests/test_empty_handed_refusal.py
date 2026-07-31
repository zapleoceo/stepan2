"""«У нас нет примеров» без единого проверяемого факта взамен — это брак, а не честность.

База знаний требует обратного, прямым текстом: «НЕ УХОДИ С ПУСТЫМИ РУКАМИ. Если по ЭТОМУ курсу
поимённого кейса нет — назови ближайший реальный и сразу скажи, по какой он программе». Правило
прозаическое, и модель его обходит.

Тред 5440, 27-31.07.2026: лид четыре раза просил отзывы, четыре раза получил «джакартских нет»
плюс адрес и брошюру, на пятый написал «хватит предлагать, если отзывов нет». Всё это время в
facts_market лежали поимённые выпускники сети с фото на itstep.ph/review — включая ровно его
случай: человек прошёл курс и построил собственный интернет-магазин, а лид пришёл именно за
своим делом.

Человек просил подтверждение, что школа настоящая, а не досье по конкретному курсу. Честное
«вот что есть, но это другая программа» закрывает запрос; «нет данных» оставляет подозрение.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.guard import empty_handed_refusal


@pytest.mark.parametrize("reply", [
    # дословно из треда 5440
    "Jujur ya Kak, testimoni khusus Jakarta yang bisa aku kasih belum ada di tangan aku",
    "Bener sih Kak, buat sekarang aku ga pegang testimoni yang ready jadi pegangan.",
    "Testimoni lokal emang belum ada di aku, itu jujur, ga aku tutup-tutupin.",
    "contoh kasusnya belum ada kak",
    "belum ada bukti yang bisa aku kasih",
])
def test_a_bare_we_have_nothing_is_caught(reply: str) -> None:
    assert empty_handed_refusal(reply)


@pytest.mark.parametrize("reply", [
    "Testimoni Jakarta memang belum ada. Tapi ada Alina Ciubat yang bikin toko online "
    "sendiri setelah kursus, semuanya ada di itstep.ph/review",
    "testimoni Jakarta belum ada, tapi sertifikat bisa dicek di diploma.itstep.org",
    "testimoni lokal belum ada, tapi program di Kamboja kami kerjakan bareng UNDP",
    "belum ada testimoni Jakarta, tapi kami jalan sejak 1999 di 24 negara",
])
def test_no_local_case_plus_a_real_one_is_fine(reply: str) -> None:
    """Правило не запрещает говорить «джакартских нет» — оно запрещает на этом остановиться."""
    assert not empty_handed_refusal(reply)


@pytest.mark.parametrize("reply", [
    "Biayanya Rp1.882.955, DP Rp500rb buat booking slot",
    "Kelasnya 2 minggu, 3x seminggu ya Kak",
    "belum ada jadwal pastinya kak",          # расписание — не доказательство
    "tanggal batch berikutnya belum fix",
])
def test_ordinary_answers_are_untouched(reply: str) -> None:
    assert not empty_handed_refusal(reply)


def test_both_word_orders_are_caught() -> None:
    """Порядок слов в индонезийском свободный. Первая версия ловила только «belum ada
    testimoni», и две из трёх реальных фраз треда 5440 прошли мимо — там отрицание стоит
    после существительного."""
    assert empty_handed_refusal("belum ada testimoni")
    assert empty_handed_refusal("testimoni lokal belum ada")


def test_the_gate_reports_it() -> None:
    from app.modules.conversation.money_gate import money_issues  # noqa: PLC0415
    issues = money_issues("Testimoni lokal emang belum ada di aku", "")
    assert any("no proof" in i for i in issues)
