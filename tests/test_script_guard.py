"""wrong_script — reject a broker translation/label that drifted to the wrong script."""
from __future__ import annotations

from app.domain.script_guard import wrong_script


def test_arabic_is_rejected_for_every_target() -> None:
    for lang in ("ru", "en", "id"):
        assert wrong_script("تعلم واجهة المستخدم", lang) is True  # Arabic


def test_cjk_and_hebrew_rejected() -> None:
    assert wrong_script("学习编程", "en") is True      # Chinese
    assert wrong_script("프로그래밍", "id") is True      # Korean
    assert wrong_script("לימוד", "ru") is True          # Hebrew


def test_valid_translations_pass() -> None:
    assert wrong_script("Программирование", "ru") is False
    assert wrong_script("Programming", "en") is False
    assert wrong_script("Pemrograman", "id") is False
    assert wrong_script("AI", "ru") is False   # a Latin tech abbrev is fine, not "wrong script"


def test_empty_is_not_wrong() -> None:
    assert wrong_script("", "ru") is False
