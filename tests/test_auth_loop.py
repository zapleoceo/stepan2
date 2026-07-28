"""A login that succeeds and then does not stick must say so, not bounce forever.

Reported live 2026-07-28: Opera 134 on Linux sat in /ui/inbox → 303 → /login → Telegram →
/ui/inbox → 303 … indefinitely, while Chrome on the same machine stayed signed in. Proven
server-side at the time: the callback returned 200, issued
`stepan2_session=…; HttpOnly; Max-Age=2592000; Path=/; SameSite=lax; Secure`, and that exact
cookie fed back into /ui/inbox returned the real inbox (54 750 bytes). The session was fine;
the browser was not returning it.

The defect worth fixing was not the browser — it was that this looked identical to "you are
not signed in", so nobody could tell the difference, least of all the person stuck in it.
"""
from __future__ import annotations

from app.api._routes_auth import (
    LOGIN_PROBE_COOKIE,
    LOGIN_PROBE_MAX_AGE_S,
    _cookie_blocked_html,
)


def test_the_probe_is_short_lived() -> None:
    """It describes ONE attempt. Outliving it would misdiagnose the next login as a loop."""
    assert 0 < LOGIN_PROBE_MAX_AGE_S <= 300


def test_the_diagnosis_names_the_cause_and_what_to_do() -> None:
    html = _cookie_blocked_html()
    # It must not blame the account — that is the wrong thing to go and check.
    assert "Nothing is wrong with your account" in html
    assert "cookies" in html.lower()
    assert "stepan2.zapleo.com" in html
    assert "another browser" in html.lower()
    assert "/login" in html  # a way back out


def test_the_login_page_serves_the_widget_when_there_is_no_loop(monkeypatch) -> None:  # noqa: ANN001
    """The ordinary first visit is untouched: no breadcrumb, no diagnosis, just the widget."""
    import asyncio

    from app.api import _routes_auth as mod

    class _Req:
        cookies: dict[str, str] = {}

    monkeypatch.setattr(mod.settings(), "auth_enabled", True, raising=False)
    monkeypatch.setattr(mod.settings(), "tg_login_bot_username", "itSTEPan_bot", raising=False)
    body = asyncio.run(mod.login_page(_Req(), next="")).body.decode()
    assert "telegram-widget.js" in body
    assert "did not keep the session" not in body


def test_a_second_pass_with_the_breadcrumb_shows_the_diagnosis(monkeypatch) -> None:  # noqa: ANN001
    """Coming back to /login still carrying the probe means the session WAS issued and did not
    return — the one case the widget must not be shown for a fourth time."""
    import asyncio

    from app.api import _routes_auth as mod

    class _Req:
        cookies = {LOGIN_PROBE_COOKIE: "1"}

    monkeypatch.setattr(mod.settings(), "auth_enabled", True, raising=False)
    resp = asyncio.run(mod.login_page(_Req(), next="/ui/inbox"))
    body = resp.body.decode()
    assert "did not keep the session" in body
    assert "telegram-widget.js" not in body
    # …and the breadcrumb is cleared, so the retry starts from a clean state.
    cleared = [v.decode() for k, v in resp.raw_headers if k.decode().lower() == "set-cookie"]
    assert any(LOGIN_PROBE_COOKIE in c for c in cleared), cleared
