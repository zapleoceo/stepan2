"""reply_engine — the per-branch switch between the v2 pipeline and the v3 rebuild.

A branch must never lose replies to a bad setting value, so an unknown/blank engine resolves
to the default rather than to a code path that doesn't exist."""
from __future__ import annotations

import pytest

from app.modules.settings import schema as S
from app.modules.settings.service import _parse


def test_default_engine_is_v3() -> None:
    """A branch that never set the key runs the rebuilt engine."""
    assert _parse({}).reply_engine == "v3"


def test_explicit_v3_is_honoured() -> None:
    assert _parse({"reply_engine": "v3"}).reply_engine == "v3"


@pytest.mark.parametrize("bad", ["", "  ", "V3!", "v4", "legacy", "true"])
def test_unrecognised_engine_falls_back_to_the_default(bad: str) -> None:
    """A typo or a stale row from a removed option must not disable replies."""
    assert _parse({"reply_engine": bad}).reply_engine == "v3"


def test_surrounding_whitespace_is_tolerated() -> None:
    assert _parse({"reply_engine": " v3 "}).reply_engine == "v3"


def test_schema_declares_both_engines_and_defaults_to_v3() -> None:
    """The dropdown the operator sees and the values _parse accepts are the same set."""
    field = S.field_for("reply_engine")
    assert field is not None
    assert field.default == "v3"
    assert [c for c, _ in (field.choices or [])] == ["v2", "v3"]


def test_engine_choices_are_localized_everywhere() -> None:
    field = S.field_for("reply_engine")
    assert field is not None
    for _, label in field.choices or []:
        assert set(label) >= {"ru", "en", "id"}
        assert all(label[lang].strip() for lang in ("ru", "en", "id"))
