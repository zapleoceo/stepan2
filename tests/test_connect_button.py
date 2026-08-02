"""The Meta connector shows the consent-screen button first, the paste box second.

A client cannot obtain a System User token, and Meta's App Review will not approve a
permission without a recording of a user granting it — which only the consent screen can show.
So the button has to be the primary path in the UI, not a hidden alternative to a token field.

The manual form stays: it is how our own channels were connected and the only way in when the
app id is not configured yet.
"""
from __future__ import annotations

from app.api._i18n import _lang
from app.api._ui_panels import _ch_meta_form


def _html(lang: str = "ru") -> str:
    _lang.set(lang)
    return _ch_meta_form(16)


def test_button_points_at_the_connect_flow() -> None:
    assert 'href="/connect/meta/16/start"' in _html()


def test_button_comes_before_the_manual_form() -> None:
    html = _html()
    assert html.index("/connect/meta/16/start") < html.index("/ui/channels/16/meta/connect")


def test_manual_form_is_kept_but_folded_away() -> None:
    html = _html()
    assert "<details" in html
    assert 'hx-post="/ui/channels/16/meta/connect"' in html


def test_hint_explains_what_happens_in_plain_words() -> None:
    """The person clicking this is a client, not an operator."""
    assert "окно Meta" in _html("ru")
    # The apostrophe is HTML-escaped on the way out, so match around it.
    assert "window: pick the Page" in _html("en")


def test_no_untranslated_keys_leak() -> None:
    for lang in ("ru", "en", "id"):
        html = _html(lang)
        assert "ch.connect_fb" not in html
        assert "ch.connect_manual" not in html


def test_error_still_renders_above_everything() -> None:
    _lang.set("ru")
    html = _ch_meta_form(16, error="Boom")
    assert "Boom" in html
    assert html.index("Boom") < html.index("/connect/meta/16/start")
