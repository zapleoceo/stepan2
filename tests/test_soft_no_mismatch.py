"""Отказ через «не подходит», а не через «нет времени»."""
import pytest

from app.modules.conversation.signals import SOFT_NO_RE

REFUSALS = [
    "Bukan kak, memang ga cocok aja dari sisi waktunya. Terimakasih.",  # тред 38
    "Maaf kak, jadwalnya nggak cocok buat saya",
    "Waktunya kurang pas sih kak",
    "Harganya belum cocok buat saya sekarang",
    "Lokasinya nggak pas, kejauhan",
]

NOT_REFUSALS = [
    "Ini cocok gak buat pemula?",          # уточняющий вопрос, а не отказ
    "Kira-kira cocok buat saya nggak kak?",
    "Jadwalnya gimana kak?",
    "Kalau harganya cocok, saya mau daftar",
    "Programnya cocok banget buat saya",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_mismatch_refusals_are_caught(text: str) -> None:
    assert SOFT_NO_RE.search(text), text


@pytest.mark.parametrize("text", NOT_REFUSALS)
def test_questions_about_fit_are_not_refusals(text: str) -> None:
    """«cocok» без объекта — обычный вопрос; принять его за отказ значит уронить живой лид."""
    assert not SOFT_NO_RE.search(text), text
