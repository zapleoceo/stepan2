"""Достать токен из URL MCP-сервера и отдать его заголовком, а не query-параметром.

Настройка `crm_mcp_url` хранится как `https://host/mcp/crm?token=...`, и в таком виде токен
живёт в самом URL. Оттуда он попадает в текст любой ошибки транспорта (а её логируют целиком),
в дампы настроек и в трассировки. Ровно по этой причине токен Meta в `transports.py` в своё
время переехал из query в заголовок — там его напечатал в лог обычный 400-й ответ.

Проверено на боевом mcp.itstep.org 29.07.2026: `Authorization: Bearer <token>` принимается,
а тот же URL без токена соединение не устанавливает — то есть заголовок действительно
аутентифицирует, а не молча игнорируется.

Совместимость: URL без `token=` возвращается как есть с headers=None, поэтому настройка,
где токен уже вынесен наружу, продолжает работать.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

_TOKEN_KEYS = ("token", "access_token", "api_key")


def connect_args(url: str) -> tuple[str, dict[str, str] | None]:
    """(url без токена, заголовки) — то, что нужно передать в streamablehttp_client."""
    parts = urlsplit(url or "")
    query = parse_qs(parts.query, keep_blank_values=True)
    token = ""
    for key in _TOKEN_KEYS:
        values = query.pop(key, None)
        if values and values[0] and not token:
            token = values[0]
    if not token:
        return url, None
    bare = urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query, doseq=True), parts.fragment))
    return bare, {"Authorization": f"Bearer {token}"}


def redact(url: str) -> str:
    """URL, пригодный для лога: значение токена заменено. Для случая, когда сам URL
    пришёл из настроек и мог не пройти через connect_args."""
    parts = urlsplit(url or "")
    query = parse_qs(parts.query, keep_blank_values=True)
    if not any(k in query for k in _TOKEN_KEYS):
        return url
    for key in _TOKEN_KEYS:
        if key in query:
            query[key] = ["REDACTED"]  # не «***»: urlencode превратил бы звёздочки в %2A
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query, doseq=True), parts.fragment))
