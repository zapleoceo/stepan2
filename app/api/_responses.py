"""Короткие ответы-заглушки для htmx-панелей.

Один и тот же div повторялся в роутах сорок раз пятью вариантами: «Not found», «Thread not
found», «Forbidden», «gone», «pick branch». Форма при этом обязана совпадать — панель
подменяется целиком, и лишний класс или другой статус меняют поведение htmx, а не только
вид. Держим в одном месте, чтобы менялось тоже в одном.
"""
from __future__ import annotations

from fastapi.responses import HTMLResponse

from ._i18n import t


def emp(message: str, status: int = 200) -> HTMLResponse:
    """Пустое состояние панели. Статус важен: htmx по 4xx не подменяет цель без hx-swap."""
    return HTMLResponse(f'<div class="emp">{message}</div>', status_code=status)


def not_found() -> HTMLResponse:
    return emp("Not found", 404)


def thread_not_found() -> HTMLResponse:
    return emp("Thread not found", 404)


def branch_not_found() -> HTMLResponse:
    return emp("Branch not found", 404)


def forbidden() -> HTMLResponse:
    return emp("Forbidden", 403)


def gone() -> HTMLResponse:
    return emp(t("pl.gone"), 404)


def pick_branch() -> HTMLResponse:
    return emp(t("pl.pick_branch"), 400)
