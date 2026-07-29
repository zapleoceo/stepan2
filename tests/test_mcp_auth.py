"""Токен MCP уезжает в заголовок, а не живёт в URL.

`crm_mcp_url` хранится как `https://host/mcp/crm?token=...`. В таком виде токен попадает в
текст ошибки транспорта — а её логируют целиком, — в дампы настроек и в трассировки. Ровно
это уже случалось с токеном Meta: обычный 400-й ответ напечатал живой page-токен в лог, после
чего он переехал в заголовок (`transports.py`).

Проверено на боевом mcp.itstep.org 29.07.2026: `Authorization: Bearer` принимается, а тот же
URL без токена соединения не даёт — заголовок аутентифицирует, а не игнорируется.
"""
from __future__ import annotations

import pytest

from app.adapters.mcp_auth import connect_args, redact

_URL = "https://mcp.itstep.org/mcp/crm?token=itmcp_secret123"


def test_token_moves_from_the_query_string_to_a_header() -> None:
    url, headers = connect_args(_URL)
    assert url == "https://mcp.itstep.org/mcp/crm"
    assert headers == {"Authorization": "Bearer itmcp_secret123"}
    assert "itmcp_secret123" not in url


def test_other_query_params_survive_the_move() -> None:
    url, headers = connect_args("https://h/mcp?token=abc&city=jakarta")
    assert url == "https://h/mcp?city=jakarta"
    assert headers == {"Authorization": "Bearer abc"}


@pytest.mark.parametrize("key", ["token", "access_token", "api_key"])
def test_the_usual_spellings_are_all_recognised(key: str) -> None:
    _, headers = connect_args(f"https://h/mcp?{key}=zzz")
    assert headers == {"Authorization": "Bearer zzz"}


def test_a_url_without_a_token_is_left_exactly_as_it_is() -> None:
    """Back-compat: a setting where the token was already moved out must keep working."""
    assert connect_args("https://h/mcp") == ("https://h/mcp", None)
    assert connect_args("https://h/mcp?city=jakarta") == ("https://h/mcp?city=jakarta", None)
    assert connect_args("") == ("", None)


def test_redact_hides_the_value_but_keeps_the_url_readable() -> None:
    assert redact(_URL) == "https://mcp.itstep.org/mcp/crm?token=REDACTED"
    assert "itmcp_secret123" not in redact(_URL)
    assert redact("https://h/mcp") == "https://h/mcp"
