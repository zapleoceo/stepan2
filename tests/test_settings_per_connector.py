"""Настройки канала показываются тому коннектору, который умеет их читать.

Раньше каждый коннектор показывал все настройки области канала: капы комментариев в
редакторе WhatsApp, таймеры фолоапов на канале сайта. Оператор не мог понять, какие из них
его коннектор вообще применяет, а какие лежат мёртвым грузом.

Требование заявляет НАСТРОЙКА, а отвечает на него коннектор — своими же объявлениями
(`capabilities`, `proactive_outreach`). Списка имён коннекторов здесь нет намеренно: он
пришлось бы править при добавлении каждого нового, а ровно от этого реестр и избавлялся.
"""
from __future__ import annotations

import pytest

from app.api._ui_settings import _field_for_kind
from app.modules.settings import schema as S


def _visible(kind: str) -> set[str]:
    return {
        f.key
        for sec in S.sections_for_scope("channel")
        for f in sec.fields
        if not f.hidden and _field_for_kind(f, kind)
    }


@pytest.mark.parametrize("key", [
    "comment_replies_enabled", "comment_hourly_cap", "comment_per_post_cap",
    "proactive_comments_enabled", "proactive_comment_daily_cap",
])
def test_comment_settings_only_where_comments_exist(key: str) -> None:
    """WhatsApp не читает ни одного поста — эти поля там были обещанием, которого никто
    не выполнит."""
    assert key in _visible("instagram")
    assert key not in _visible("whatsapp")
    assert key not in _visible("meta_business")


@pytest.mark.parametrize("key", ["hourly_cap", "daily_cap", "followup_enabled",
                                 "followup_schedule_h"])
def test_unprompted_send_settings_are_hidden_where_nobody_can_be_written_to(key: str) -> None:
    """Посетитель сайта живёт один HTTP-запрос и адреса после себя не оставляет. Кап на
    неспровоцированные отправки и лестница фолоапов там управляют пустотой."""
    assert key in _visible("instagram")
    assert key not in _visible("website")


def test_meta_credentials_stay_with_meta() -> None:
    """Это защита по префиксу ключа, она была и раньше — проверяем, что не сломали."""
    assert "meta_page_id" in _visible("meta_business")
    assert "meta_page_id" not in _visible("whatsapp")


def test_the_settings_every_connector_reads_are_shown_to_every_connector() -> None:
    common = {"reply_delay_min_s", "reply_delay_max_s", "phone_country_code"}
    for kind in ("instagram", "whatsapp", "meta_business", "website"):
        assert common <= _visible(kind), kind


def test_no_connector_lost_everything() -> None:
    """Фильтр, скрывающий всё, выглядит как сломанный редактор, а не как порядок."""
    for kind in ("instagram", "whatsapp", "meta_business", "website"):
        assert len(_visible(kind)) >= 4, kind


def test_whatsapp_sees_fewer_settings_than_instagram() -> None:
    """Собственно то, с чего началось: два коннектора показывали одно и то же."""
    assert _visible("whatsapp") < _visible("instagram")


def test_a_declared_requirement_matches_a_real_capability() -> None:
    """Опечатка в строке возможности молча спрятала бы настройку у всех коннекторов."""
    from app.connectors.spec import Capability

    valid = {c.value for c in Capability}
    for sec in S.SCHEMA:
        for f in sec.fields:
            assert f.capability is None or f.capability in valid, f.key
