"""MCP admin page — incoming connectors (token management) + the outgoing CRM link.

Server-rendered HTML string, same idiom as the other admin panels. The page is the swap
target for its own POSTs (create/revoke token, save CRM), so every action re-renders it.
"""
from __future__ import annotations

import html as _h
from datetime import datetime

from app.adapters.db.models import McpToken

_C_BG = "#141925"
_C_BORDER = "#2d3748"
_C_INK = "#cfe0f4"
_C_MUTE = "#8b98a5"
_C_ACCENT = "#e2b33d"


def _card(inner: str) -> str:
    return (f'<div style="background:{_C_BG};border:1px solid {_C_BORDER};border-radius:8px;'
            f'padding:1rem 1.15rem;margin-bottom:1rem">{inner}</div>')


def _h3(txt: str) -> str:
    return (f'<div style="color:{_C_INK};font-weight:600;font-size:.95rem;'
            f'margin-bottom:.6rem">{_h.escape(txt)}</div>')


def _endpoint_row(label: str, url: str) -> str:
    return (f'<div style="margin:.15rem 0;font-size:.8rem;color:{_C_MUTE}">{_h.escape(label)}: '
            f'<code style="color:{_C_INK};background:#0d1117;padding:.1rem .35rem;'
            f'border-radius:4px;user-select:all">{_h.escape(url)}</code></div>')


def _new_token_banner(raw: str) -> str:
    return _card(
        f'<div style="color:{_C_ACCENT};font-weight:600;margin-bottom:.35rem">'
        f'🔑 Новый токен создан — скопируйте сейчас, он больше не покажется</div>'
        f'<code style="display:block;color:{_C_INK};background:#0d1117;padding:.5rem;'
        f'border-radius:4px;font-size:.82rem;word-break:break-all;user-select:all">'
        f'{_h.escape(raw)}</code>')


def _scope_badge(scope: str) -> str:
    color = "#3fb950" if scope == "read" else "#e2b33d"
    return (f'<span style="color:{color};border:1px solid {color};border-radius:4px;'
            f'padding:0 .35rem;font-size:.7rem">{_h.escape(scope)}</span>')


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else "—"


def _branch_badge(name: str) -> str:
    """A token's branch scope: the branch name, or a highlighted 'all branches' chip."""
    if not name:
        return ('<span style="color:#7ea6ff;border:1px solid #7ea6ff;border-radius:4px;'
                'padding:0 .35rem;font-size:.7rem">все филиалы</span>')
    return f'<span style="color:{_C_MUTE};font-size:.78rem">{_h.escape(name)}</span>'


def _token_row(tk: McpToken, branch_name: str) -> str:
    when = _fmt_dt(tk.created_at)
    used = _fmt_dt(tk.last_used_at)
    if tk.revoked_at is not None:
        action = f'<span style="color:{_C_MUTE};font-size:.75rem">отозван</span>'
        name = f'<s style="color:{_C_MUTE}">{_h.escape(tk.label)}</s>'
    else:
        action = (
            f'<button hx-post="/ui/mcp/token/{tk.id}/revoke" hx-target="#mcp-page"'
            f' hx-confirm="Отозвать токен «{_h.escape(tk.label)}»? Доступ пропадёт сразу."'
            f' style="background:none;border:1px solid #b3543f;color:#e06a4f;'
            f'border-radius:4px;padding:.1rem .5rem;font-size:.72rem;cursor:pointer">'
            f'Отозвать</button>')
        name = f'<span style="color:{_C_INK}">{_h.escape(tk.label)}</span>'
    return (
        f'<tr style="border-top:1px solid {_C_BORDER}">'
        f'<td style="padding:.35rem .5rem">{name}</td>'
        f'<td style="padding:.35rem .5rem">{_scope_badge(tk.scope)}</td>'
        f'<td style="padding:.35rem .5rem">{_branch_badge(branch_name)}</td>'
        f'<td style="padding:.35rem .5rem"><code style="color:{_C_MUTE}">'
        f'{_h.escape(tk.prefix)}…</code></td>'
        f'<td style="padding:.35rem .5rem;color:{_C_MUTE};font-size:.75rem">{when}</td>'
        f'<td style="padding:.35rem .5rem;color:{_C_MUTE};font-size:.75rem">{used}</td>'
        f'<td style="padding:.35rem .5rem;text-align:right">{action}</td></tr>')


def _incoming(
    base_url: str, tokens: list[McpToken], new_token: str | None,
    branches: list[tuple[int, str]],
) -> str:
    write_url = f"{base_url}/connector/mcp"
    read_url = f"{base_url}/reader/mcp"
    branch_by_id = {bid: nm for bid, nm in branches}
    rows = "".join(
        _token_row(t, branch_by_id.get(t.branch_id, "") if t.branch_id else "")
        for t in tokens) or (
        f'<tr><td colspan="7" style="padding:.5rem;color:{_C_MUTE};font-size:.8rem">'
        f'Токенов пока нет</td></tr>')
    banner = _new_token_banner(new_token) if new_token else ""
    branch_opts = '<option value="">Все филиалы (универсальный)</option>' + "".join(
        f'<option value="{bid}">{_h.escape(nm)}</option>' for bid, nm in branches)
    return _card(
        _h3("Входящие подключения — клиенты подключаются к Степану")
        + _endpoint_row("write (двигать воронку)", write_url)
        + _endpoint_row("read (только чтение чатов)", read_url)
        + '<div style="height:.6rem"></div>' + banner
        + '<table style="width:100%;border-collapse:collapse;font-size:.82rem">'
        + f'<thead><tr style="color:{_C_MUTE};font-size:.72rem;text-align:left">'
        + '<th style="padding:.2rem .5rem">Название</th><th style="padding:.2rem .5rem">Тип</th>'
        + '<th style="padding:.2rem .5rem">Филиал</th>'
        + '<th style="padding:.2rem .5rem">Префикс</th><th style="padding:.2rem .5rem">Создан</th>'
        + '<th style="padding:.2rem .5rem">Использован</th><th></th></tr></thead>'
        + f'<tbody>{rows}</tbody></table>'
        + '<form hx-post="/ui/mcp/token/create" hx-target="#mcp-page"'
        '  style="display:flex;gap:.4rem;margin-top:.7rem;flex-wrap:wrap">'
        f'<input name="label" placeholder="кому (директор, партнёр…)" required'
        f' style="flex:1;min-width:140px;background:#0d1117;border:1px solid {_C_BORDER};'
        f'color:{_C_INK};border-radius:5px;padding:.35rem .5rem">'
        f'<select name="scope" style="background:#0d1117;border:1px solid {_C_BORDER};'
        f'color:{_C_INK};border-radius:5px;padding:.35rem">'
        '<option value="read">read — только чтение</option>'
        '<option value="write">write — двигать воронку</option></select>'
        f'<select name="branch_id" title="Доступ к филиалу"'
        f' style="background:#0d1117;border:1px solid {_C_BORDER};'
        f'color:{_C_INK};border-radius:5px;padding:.35rem">{branch_opts}</select>'
        f'<button style="background:{_C_ACCENT};border:none;color:#1a1d24;font-weight:600;'
        f'border-radius:5px;padding:.35rem .8rem;cursor:pointer">Создать токен</button>'
        '</form>')


def _outgoing(enabled: bool, url: str, has_secret: bool) -> str:
    chk = "checked" if enabled else ""
    secret_ph = ("секрет задан — оставьте пустым, чтобы не менять" if has_secret
                 else "Bearer-токен CRM")
    return _card(
        _h3("Исходящее подключение — Степан читает CRM (перед контактом)")
        + f'<div style="color:{_C_MUTE};font-size:.78rem;margin-bottom:.5rem">Если CRM говорит,'
        ' что лида уже ведёт менеджер / сделка закрыта — Степан не пишет повторно. Контракт'
        ' эндпоинта — в документации ниже.</div>'
        + '<form hx-post="/ui/mcp/outgoing/save" hx-target="#mcp-page"'
        '  style="display:flex;flex-direction:column;gap:.5rem">'
        f'<label style="color:{_C_INK};font-size:.82rem"><input type="checkbox" name="enabled"'
        f' {chk}> Включить CRM-гейт для выбранного филиала</label>'
        f'<input name="url" value="{_h.escape(url)}" placeholder="https://crm.example/lead-state"'
        f' style="background:#0d1117;border:1px solid {_C_BORDER};color:{_C_INK};'
        f'border-radius:5px;padding:.35rem .5rem">'
        f'<input name="secret" type="password" placeholder="{_h.escape(secret_ph)}"'
        f' style="background:#0d1117;border:1px solid {_C_BORDER};color:{_C_INK};'
        f'border-radius:5px;padding:.35rem .5rem">'
        f'<button style="align-self:flex-start;background:{_C_ACCENT};border:none;color:#1a1d24;'
        f'font-weight:600;border-radius:5px;padding:.35rem .8rem;cursor:pointer">Сохранить</button>'
        '</form>')


def mcp_page_html(
    base_url: str, tokens: list[McpToken], *, crm_enabled: bool, crm_url: str,
    crm_has_secret: bool, new_token: str | None = None,
    branches: list[tuple[int, str]] | None = None,
) -> str:
    from app.api._i18n import t  # noqa: PLC0415
    return (
        '<div id="mcp-page" style="padding:1rem 1.2rem;max-width:920px">'
        f'<div data-help="{_h.escape(t("help.mcp"))}"'
        f' style="color:{_C_INK};font-size:1.2rem;font-weight:700;margin-bottom:.2rem">'
        'MCP — подключения и токены</div>'
        f'<div style="color:{_C_MUTE};font-size:.82rem;margin-bottom:1rem">Управление доступом'
        ' к Степану по MCP: входящие коннекторы (внешние клиенты) и исходящая связь с CRM.</div>'
        + _incoming(base_url, tokens, new_token, branches or [])
        + _outgoing(crm_enabled, crm_url, crm_has_secret)
        + f'<a href="/ui/mcp/docs" style="display:inline-block;color:{_C_ACCENT};'
        'font-size:.85rem;text-decoration:none;border:1px solid #3a4150;border-radius:6px;'
        'padding:.4rem .8rem">⬇ Скачать документацию по подключению</a>'
        '</div>')
