"""Выбор каналов над списком чатов переживает перезагрузку страницы.

Фильтр жил только в строке запроса, поэтому любая обычная загрузка /ui/inbox — закладка,
новая вкладка, F5 — молча включала все коннекторы обратно. Оператор, спрятавший номера
менеджеров, находил их в списке каждое утро.
"""
from __future__ import annotations

from app.api.ui import KIND_COOKIE, _remember_kind, _remembered_kind


class _Req:
    def __init__(self, **cookies: str) -> None:
        self.cookies = cookies


class _Resp:
    def __init__(self) -> None:
        self.set: dict[str, str] = {}
        self.deleted: list[str] = []

    def set_cookie(self, key: str, value: str, **_kw: object) -> None:
        self.set[key] = value

    def delete_cookie(self, key: str) -> None:
        self.deleted.append(key)


def test_a_plain_reload_recalls_the_last_selection() -> None:
    assert _remembered_kind(_Req(**{KIND_COOKIE: "whatsapp"}), "") == "whatsapp"


def test_an_explicit_choice_beats_the_memory() -> None:
    """Ссылку, присланную коллеге, нельзя переписывать чужой куки: она должна значить то,
    что в ней написано."""
    assert _remembered_kind(_Req(**{KIND_COOKIE: "whatsapp"}), "instagram") == "instagram"


def test_nothing_remembered_and_nothing_asked_means_everything() -> None:
    assert _remembered_kind(_Req(), "") == ""


def test_turning_every_chip_back_on_forgets_the_filter() -> None:
    """'all' — это вид по умолчанию, и он не должен оставлять следа: иначе фильтр
    невозможно снять, только переключить."""
    resp = _Resp()
    _remember_kind(resp, "all")
    assert resp.deleted == [KIND_COOKIE]
    assert resp.set == {}


def test_a_subset_is_remembered() -> None:
    resp = _Resp()
    _remember_kind(resp, "instagram,website")
    assert resp.set[KIND_COOKIE] == "instagram,website"


def test_all_off_is_a_choice_too() -> None:
    """«Скрыть всё» — осознанное состояние, а не пустое; забыть его значит показать всё."""
    resp = _Resp()
    _remember_kind(resp, "none")
    assert resp.set[KIND_COOKIE] == "none"
