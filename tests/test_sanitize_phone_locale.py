"""A fabricated phone number was only recognisable if it was Indonesian.

clean_reply deletes any line carrying a phone-like token that is not the branch's own official
number — the model invents contact numbers, and a lead who calls one reaches a stranger. The
shapes it knew were `+62…` and `08x…`, hardcoded, so a branch in Malaysia, Vietnam or Ukraine
shipped invented local numbers with nothing in the way.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.config import settings  # noqa: E402
from app.modules.conversation.sanitize import clean_reply  # noqa: E402


def test_an_invented_indonesian_number_is_still_stripped_for_branch_one() -> None:
    out = clean_reply("Halo Kak\nWA saya 0812 3456 7890 ya")
    assert "0812" not in out and "Halo Kak" in out


def test_the_branch_s_own_number_still_survives() -> None:
    """The one line that must never be deleted — it used to be, when the constant went stale
    and every line carrying the NEW official number looked fabricated (thread 452)."""
    digits = settings().official_phone_e164
    assert clean_reply(f"WA kami {digits}").endswith(digits.lstrip("+"))


def test_a_malaysian_number_reaches_an_indonesian_lead_and_a_malaysian_branch_strips_it() -> None:
    """Same invented number, two branches. Under the Indonesian code it is invisible — which
    is exactly what every non-Indonesian branch has been running with."""
    line = "Hubungi +60 12 345 6789 ya"
    assert "+60" in clean_reply(line)                       # today's behaviour on branch 1
    assert "+60" not in clean_reply(line, country_code="60")


def test_a_national_trunk_number_is_stripped_without_a_country_prefix() -> None:
    """The local form carries no '+' at all, so the international pattern cannot see it —
    which is why Indonesia needed its own '08x' branch in the first place."""
    assert "012 345 6789" not in clean_reply("Call 012 345 6789", country_code="60")


def test_a_price_is_never_mistaken_for_a_phone_number() -> None:
    """The reason the pattern is anchored on a dialling code and a trunk zero: a money figure
    is a long digit run too, and deleting the line with the price on it is worse than the
    fabricated number this exists to stop."""
    for cc in ("62", "60", "380"):
        assert "13.360.000" in clean_reply("Harganya Rp 13.360.000 kak", country_code=cc)


def test_an_unlisted_country_still_gets_a_pattern_rather_than_indonesia_s() -> None:
    assert "+380" not in clean_reply("Телефон +380 67 123 4567", country_code="380")
