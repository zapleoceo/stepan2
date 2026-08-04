"""Instagram credential panel — the two-step login/checkpoint flow.

Moved out of app/api/_ui_panels so the Instagram connector owns its own panel; the
panel is reached through ConnectorSpec.credential_form, never by an if-chain on kind.
"""
from __future__ import annotations

import html as _h

from app.api._i18n import t

from .ui_bits import _ch_err, _ch_hint, _ch_step

# Seconds to wait before each automatic re-attempt of a phone-approved login. Every entry
# costs one real Instagram login call, so this backs off instead of polling on a fixed tick,
# and its length is the attempt cap (~2.5 min total) after which we stop and hand the
# operator a button. Never make this tighter: repeated logins are a checkpoint/ban vector.
_IG_POLL_DELAYS = (8, 15, 25, 40, 60)


def _ch_ig_form(
    ch_id: int, step: str = "login", flow_id: str = "", error: str = "",
    kind: str = "", username: str = "", attempt: int = 0,
) -> str:
    """Two-step Instagram connect flow: (1) credentials, (2) resolving whatever Instagram
    asked for. Step 2's content switches on `kind` — instagrapi hits FOUR unrelated
    Instagram mechanisms that all land here:
    - `kind='2fa'` — real 2FA where a TYPED code exists (authenticator app / SMS, detected via
      two_factor_info's totp_two_factor_on / sms_two_factor_on), resolved by re-login.
    - `kind='device'` — a login-approval PUSH to the user's other device: no code exists, the
      user taps Approve in the Instagram notification, then we re-login on the same client. No
      code field — only a "I approved on my phone → continue" button (the itstep.kl bug: a push
      approval was shown a code field that never accepts anything).
    - `kind='challenge'` — a security "is this really you" check, code emailed/texted,
      resolved via challenge_resolve.
    - `kind='manual'` — a checkpoint instagrapi flags as NOT resolvable by any text code at
      all (Bloks redirect / native in-app approval) — no code field; only a "confirm in the
      real Instagram app, then retry" button, reusing the same client/device fingerprint.
    Showing all three as a bare "2FA code" field used to make a challenge/manual checkpoint
    look like a missing-2FA problem, so turning 2FA off didn't stop the prompt (real
    report, 2026-07-08).

    IMPORTANT — hx-disabled-elt/hx-indicator on the <form> ITSELF, not per-button:
    htmx 1.9.12 has a real bug (confirmed empirically, not documented) where an element
    with hx-disabled-elt="find button" and/or hx-indicator="find .htmx-indicator" on an
    ANCESTOR <form> silently swallows the click of any OTHER descendant that has its own
    independent hx-get/hx-post — the request never leaves the browser, no console error.
    This broke "Start over" and the app-confirm button from day one (real report,
    2026-07-09: clicking either did visibly nothing). Fix: never put these two attributes
    on a <form> that contains more than one independently-triggering element — set
    hx-disabled-elt="this" and hx-indicator="#<id>" on each button individually instead."""
    err = _ch_err(error)
    if step == "2fa":
        spin_id = f"ig-spin-{ch_id}"
        spin = (
            f'<span id="{spin_id}" class="htmx-indicator" style="margin-left:.5rem;'
            f'color:#8b98a5;font-size:.72rem">⏳ {_h.escape(t("ch.logging_in"))}</span>'
        )
        who = (
            f'<div style="font-size:.76rem;color:#9aa5b1;margin-bottom:.6rem">'
            f'{_h.escape(t("ch.for_account"))} <b>@{_h.escape(username)}</b></div>'
            if username else ""
        )
        if kind in ("manual", "device"):
            # No code to type — the login is approved on the phone. 'device' = a login-approval
            # push to another device; 'manual' = an in-app checkpoint instagrapi flags as
            # code-unresolvable. Either way the operator approves in the Instagram app and we
            # just re-attempt on the same client, so there is nothing for them to click: poll
            # for them. Each poll is a REAL login attempt, so the delay grows (_IG_POLL_DELAYS)
            # and stops at the cap — hammering login is a checkpoint/ban vector, and the
            # operator may simply not have reached their phone yet. Past the cap we fall back
            # to the manual button so they stay in control and Instagram is left alone.
            hint = t("ch.hint_device") if kind == "device" else t("ch.hint_manual")
            back = (
                f'<button type="button" class="btn-sm btn-g" style="margin-left:.4rem"'
                f' hx-disabled-elt="this" hx-indicator="#{spin_id}"'
                f' hx-get="/ui/channels/{ch_id}/form" hx-target="#ch-form" hx-swap="innerHTML">'
                f'{_h.escape(t("ch.start_over"))}</button>'
            )
            if attempt < len(_IG_POLL_DELAYS):
                delay = _IG_POLL_DELAYS[attempt]
                vals = (f'{{"flow_id":"{_h.escape(flow_id)}","attempt":"{attempt + 1}"}}')
                return (
                    f'{_ch_step(t("ch.step2"))}{who}{err}'
                    f'{_ch_hint(hint)}'
                    f'<div style="max-width:340px">'
                    f'<div id="ig-poll-{ch_id}" hx-post="/ui/channels/{ch_id}/ig/verify"'
                    f" hx-trigger=\"load delay:{delay}s\" hx-target=\"#ch-form\""
                    f' hx-swap="innerHTML" hx-vals=\'{vals}\''
                    f' style="font-size:.76rem;color:#8b98a5;margin-bottom:.5rem">'
                    f'<span class="spin" style="margin-right:.4rem;vertical-align:middle"></span>'
                    f'{_h.escape(t("ch.waiting_approve"))}</div>'
                    f'{back}</div>'
                )
            btn = t("ch.continue_device") if kind == "device" else t("ch.retry_manual")
            return (
                f'{_ch_step(t("ch.step2"))}{who}{err}'
                f'{_ch_hint(hint)}{_ch_hint(t("ch.poll_gave_up"))}'
                f'<form hx-post="/ui/channels/{ch_id}/ig/verify" hx-target="#ch-form"'
                f' hx-swap="innerHTML" style="max-width:340px">'
                f'<input type="hidden" name="flow_id" value="{_h.escape(flow_id)}">'
                f'<button type="submit" class="btn-sm btn-p" hx-disabled-elt="this"'
                f' hx-indicator="#{spin_id}">{_h.escape(btn)}</button>'
                f'{back}{spin}'
                f'</form>'
            )
        is_challenge = kind == "challenge"
        code_lbl = t("ch.code_challenge") if is_challenge else t("ch.code_2fa")
        hint = t("ch.hint_challenge") if is_challenge else t("ch.hint_2fa")
        # Instagram can fire the 2FA code prompt AND an in-app "was this you?" push for
        # the SAME login attempt at once. If the operator already approved the push,
        # making them type a code that isn't even needed just to reach the eventual
        # manual-retry step is pointless — this button skips straight to a plain retry.
        app_confirm_btn = (
            f'<div style="margin-top:.4rem">'
            f'<button type="button" class="btn-sm btn-g"'
            f' hx-post="/ui/channels/{ch_id}/ig/verify" hx-target="#ch-form"'
            f' hx-swap="innerHTML" hx-include="closest form" hx-vals=\'{{"skip_code":"1"}}\''
            f' hx-disabled-elt="this" hx-indicator="#{spin_id}">'
            f'{_h.escape(t("ch.already_confirmed"))}</button></div>'
            if not is_challenge else ""
        )
        return (
            f'{_ch_step(t("ch.step2"))}{who}{err}'
            f'<form hx-post="/ui/channels/{ch_id}/ig/verify" hx-target="#ch-form"'
            f' hx-swap="innerHTML" style="max-width:340px">'
            f'<input type="hidden" name="flow_id" value="{_h.escape(flow_id)}">'
            f'<div class="frm-grp">'
            f'<label class="frm-lbl">{_h.escape(code_lbl)}</label>'
            f'<input class="frm-inp" name="code" autocomplete="one-time-code" autofocus></div>'
            f'{_ch_hint(hint)}'
            f'<button type="submit" class="btn-sm btn-p" hx-disabled-elt="this"'
            f' hx-indicator="#{spin_id}">{_h.escape(t("ch.verify"))}</button>'
            f'{app_confirm_btn}'
            f'<button type="button" class="btn-sm btn-g" style="margin-left:.4rem"'
            f' hx-disabled-elt="this" hx-indicator="#{spin_id}"'
            f' hx-get="/ui/channels/{ch_id}/form" hx-target="#ch-form" hx-swap="innerHTML">'
            f'{_h.escape(t("ch.start_over"))}</button>{spin}'
            f'</form>'
        )
    spin = (
        f'<span class="htmx-indicator" style="margin-left:.5rem;color:#8b98a5;'
        f'font-size:.72rem">⏳ {_h.escape(t("ch.logging_in"))}</span>'
    )
    return (
        f'{_ch_step(t("ch.step1"))}{err}'
        f'<form hx-post="/ui/channels/{ch_id}/ig/start" hx-target="#ch-form"'
        f' hx-swap="innerHTML" hx-disabled-elt="find button"'
        f' hx-indicator="find .htmx-indicator" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.username"))}</label>'
        f'<input class="frm-inp" name="username" autocomplete="username"></div>'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.password"))}</label>'
        f'<input class="frm-inp" name="password" type="password"'
        f' autocomplete="current-password"></div>'
        f'{_ch_hint(t("ch.hint_login"))}'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.ig_login"))}</button>{spin}'
        f'</form>'
        # Sign in with a sessionid taken from a browser that is ALREADY logged in. Not hidden
        # away: Instagram moved 2FA onto its Bloks endpoints and instagrapi still calls the
        # legacy accounts/two_factor_login/ (subzeroid/instagrapi#2231, #2109), so for an
        # account with 2FA on this is the only path through this panel that works at all —
        # it carries an existing session and never touches the login/2FA flow.
        f'<div style="margin-top:.9rem;border-top:1px solid #2d3748;padding-top:.7rem">'
        f'<form hx-post="/ui/channels/{ch_id}/ig/start" hx-target="#ch-form"'
        f' hx-swap="innerHTML" hx-disabled-elt="find button"'
        f' hx-indicator="find .htmx-indicator" style="max-width:360px">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.sessionid"))}</label>'
        # type=password: this grants full account access, so it must not sit in plain view
        # on a shared screen, and it must never be offered to a password manager.
        f'<input class="frm-inp" name="sessionid" type="password" autocomplete="off"></div>'
        f'{_ch_hint(t("ch.hint_sessionid"))}'
        f'<button type="submit" class="btn-sm btn-p">'
        f'{_h.escape(t("ch.connect_sessionid"))}</button>{spin}'
        f'</form></div>'
        # Session-JSON import is a power-user escape hatch (paste an already-logged-in
        # instagrapi session, skip the login/2FA dance entirely) — collapsed by default so
        # it doesn't compete with the normal path for attention.
        f'<details style="margin-top:.7rem">'
        f'<summary style="font-size:.72rem;color:#6b7685;cursor:pointer">'
        f'{_h.escape(t("ch.advanced_json"))}</summary>'
        f'<form hx-post="/ui/channels/{ch_id}/ig/start" hx-target="#ch-form"'
        f' hx-swap="innerHTML" hx-disabled-elt="find button"'
        f' hx-indicator="find .htmx-indicator" style="max-width:360px;margin-top:.5rem">'
        f'<div class="frm-grp">'
        f'<label class="frm-lbl">{_h.escape(t("ch.ig_json"))}</label>'
        f'<textarea class="frm-ta" name="session_json" rows="3"'
        f' placeholder=\'{{"device_settings":...}}\' style="min-height:4rem"></textarea></div>'
        f'{_ch_hint(t("ch.hint_json"))}'
        f'<button type="submit" class="btn-sm">{_h.escape(t("ch.save"))}</button>{spin}'
        f'</form></details>'
    )
