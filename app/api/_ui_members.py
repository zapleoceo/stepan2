"""Members panel HTML — super_admin only: list with inline role/branch editors + add
form. Every mutation form targets #main (the shared panel container) so any inline
row-level form can swap the whole freshly-rendered panel back in, same as any other
nav-triggered panel load."""
from __future__ import annotations

import html as _h

from ._i18n import t

_ROLE_KEYS = ("super_admin", "branch_admin", "branch_viewer")


def _role_select(current: str) -> str:
    opts = "".join(
        f'<option value="{r}" {"selected" if r == current else ""}>'
        f'{_h.escape(t(f"role.{r}"))}</option>'
        for r in _ROLE_KEYS
    )
    return f'<select class="act-sel" name="role">{opts}</select>'


def _branch_select(current: int | None, branches: list) -> str:
    opts = (
        f'<option value="" {"selected" if current is None else ""}>'
        f'{_h.escape(t("member.platform"))}</option>'
    )
    opts += "".join(
        f'<option value="{b[0]}" {"selected" if b[0] == current else ""}>'
        f'{_h.escape(str(b[1]))}</option>'
        for b in branches  # (id, name)
    )
    return f'<select class="act-sel" name="branch_id">{opts}</select>'


def members_panel_html(rows: list, branches: list) -> str:
    """rows: (membership_id, telegram_id, role, name, branch_id); branches: (id, name)."""
    title = _h.escape(t("nav.members"))
    help_txt = _h.escape(t("help.members"))

    def _row(r: object) -> str:
        mid, tg, role, name, bid = r
        role_form = (
            f'<form hx-post="/ui/members/{mid}/role" hx-target="#main" hx-swap="innerHTML"'
            f' hx-trigger="change">{_role_select(str(role))}</form>'
        )
        branch_form = (
            f'<form hx-post="/ui/members/{mid}/branch" hx-target="#main" hx-swap="innerHTML"'
            f' hx-trigger="change">{_branch_select(bid, branches)}</form>'
        )
        del_btn = (
            f'<form hx-post="/ui/members/{mid}/delete" hx-target="#main" hx-swap="innerHTML"'
            f' hx-confirm="{_h.escape(t("member.remove_confirm"))}">'
            f'<button class="act-btn" style="background:rgba(134,46,46,.3);color:#ff9b9b">'
            f'{_h.escape(t("member.remove"))}</button></form>'
        )
        return (
            f'<tr>'
            f'<td><strong style="color:#e8eef4">{_h.escape(str(name or "—"))}</strong>'
            f'<br><span style="font-size:.7rem;color:#4a5568">tg:{tg}</span></td>'
            f'<td>{role_form}</td>'
            f'<td>{branch_form}</td>'
            f'<td>{del_btn}</td>'
            f'</tr>'
        )

    trows = "".join(_row(r) for r in rows)

    add_form = (
        f'<form hx-post="/ui/members/create" hx-target="#main" hx-swap="innerHTML"'
        f' class="fin-row" style="margin-top:.6rem;flex-wrap:wrap;gap:.4rem">'
        f'<input class="frm-inp" name="telegram_id" type="number" required'
        f' placeholder="{_h.escape(t("member.tg_id"))}" style="max-width:10rem">'
        f'<input class="frm-inp" name="name" placeholder="{_h.escape(t("member.name"))}"'
        f' style="max-width:10rem">'
        f'{_role_select("branch_viewer")}'
        f'{_branch_select(None, branches)}'
        f'<button class="btn-sm btn-p">{_h.escape(t("member.add"))}</button>'
        f'</form>'
    )

    return (
        f'<div class="ch"><span class="ch-n">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{help_txt}</div>'
        f'<table class="tbl">'
        f'<thead><tr><th>User</th><th>Role</th><th>Branch</th><th></th></tr></thead>'
        f'<tbody>{trows or "<tr><td colspan=4 style=color:#4a5568>—</td></tr>"}</tbody>'
        f'</table>'
        f'{add_form}'
        f'</div>'
    )
