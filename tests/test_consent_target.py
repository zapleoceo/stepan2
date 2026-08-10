"""Согласие принадлежит цели, а не человеку.

Тред 3163, 10.08.2026. 28 июля лид дал телефон, соглашаясь на демо-ивент за 100 тысяч —
бот записал stage=ready, ready_subtype=deal и отправил хендофф в CRM. Через две недели
разговор ушёл на курс за 13 миллионов, и на вопрос «как вам мероприятие» лид ответил
«хочу учить AI, чтобы развивать карьеру в 3D». Это заявление об интересе, а не согласие
купить. Но в досье с июля лежало readiness='ready', модель прочла это в собственной памяти,
подтвердила — и в чат ушло «Pendaftaran Kakak aku teruskan ke tim» о заявке, которой не было.

«Готов» было булевым про лида и не помнило, к чему относится. Теперь помнит.
"""
from __future__ import annotations

from app.adapters.db.models import Lead
from app.modules.conversation.delivery import ReplyDelivery
from app.modules.conversation.dossier import LeadDossier, merge_dossier

valid = ReplyDelivery._consent_still_valid  # noqa: SLF001 — чистая функция, тестируем как есть


def test_the_first_yes_always_counts() -> None:
    """Цели ещё не записано — сравнивать не с чем, согласие проходит."""
    assert valid(Lead(branch_id=1), "vibe_coding") is True


def test_a_yes_holds_while_the_target_is_the_same() -> None:
    lead = Lead(branch_id=1, agreed_product_slug="vibe_coding")
    assert valid(lead, "vibe_coding") is True


def test_a_yes_to_the_event_is_not_a_yes_to_the_course() -> None:
    """Ровно случай 3163: согласие на билет за 100 тысяч, разговор про курс за 13 миллионов."""
    lead = Lead(branch_id=1, agreed_product_slug="vibe_coding_demo_event")
    assert valid(lead, "vibe_coding") is False


def test_an_unknown_current_target_does_not_cancel_a_yes() -> None:
    """Ход, в котором продукт не назван, — это отсутствие сведений, а не смена цели.
    Гасить согласие по молчанию экстрактора значило бы терять живые сделки."""
    lead = Lead(branch_id=1, agreed_product_slug="vibe_coding")
    assert valid(lead, None) is True
    assert valid(lead, "") is True


# ── досье: readiness не переезжает через смену цели ───────────────────────────


def test_readiness_is_dropped_when_the_product_changes() -> None:
    stored = LeadDossier(readiness="ready", product_slug="vibe_coding_demo_event")
    delta = LeadDossier(product_slug="vibe_coding")

    merged = merge_dossier(stored, delta)

    assert merged.product_slug == "vibe_coding"
    assert merged.readiness == ""  # заработать заново


def test_readiness_survives_a_turn_that_names_the_same_product() -> None:
    stored = LeadDossier(readiness="ready", product_slug="vibe_coding")
    merged = merge_dossier(stored, LeadDossier(product_slug="vibe_coding"))
    assert merged.readiness == "ready"


def test_readiness_survives_a_turn_that_names_no_product() -> None:
    """Экстрактор промолчал о продукте — это не переход на другой курс."""
    stored = LeadDossier(readiness="ready", product_slug="vibe_coding")
    merged = merge_dossier(stored, LeadDossier())
    assert merged.readiness == "ready"
    assert merged.product_slug == "vibe_coding"


# ── цель согласия не привязана к рекламному клику ────────────────────────────


async def test_the_consent_target_follows_the_talk_not_the_ad_click() -> None:
    """Тред 3163 закрывается здесь, а НЕ перепривязкой треда.

    Привязка треда держится за рекламный клик — она отвечает за атрибуцию и за то, о чём
    менеджер будет звонить. Согласие спрашивают о другом: что лежит на столе прямо сейчас.
    Клик был по рекламе демо-ивента за 100 тысяч, разговор давно о курсе за 13 млн, и «да»
    ивенту не может считаться «да» курсу — даже когда тред по-прежнему числится за ивентом."""
    lead = Lead(branch_id=1, agreed_product_slug="vibe_coding_demo_event")

    assert valid(lead, "vibe_coding") is False  # разговор ушёл — согласие спрашивать заново
    assert valid(lead, "vibe_coding_demo_event") is True
