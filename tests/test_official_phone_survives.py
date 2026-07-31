"""Строка с НАШИМ номером не должна вырезаться вместе со ссылкой."""
from app.config import settings
from app.modules.conversation.sanitize import clean_reply

LINK_LINE = "Kalau nggak mau nunggu, chat aja: https://wa.me/6281111858519 (+62 811-1185-8519)"


def test_official_number_line_survives_with_its_link() -> None:
    out = clean_reply(f"Tim kami akan hubungi Kakak hari Senin.\n{LINK_LINE}")
    assert "wa.me/6281111858519" in out
    assert "811-1185-8519" in out


def test_fabricated_number_line_is_still_dropped() -> None:
    out = clean_reply("Hubungi kami di +62 812-9876-5432 ya.\nSampai jumpa!")
    assert "812-9876-5432" not in out
    assert "Sampai jumpa" in out


def test_pattern_follows_the_setting_not_a_literal() -> None:
    # ловушка, из-за которой ссылка пропала живьём: номер поменялся, регексп остался старым
    digits = "".join(c for c in settings().official_phone_e164 if c.isdigit())
    assert digits.startswith("62")
    assert clean_reply(f"WA kami +{digits}").endswith(digits)
