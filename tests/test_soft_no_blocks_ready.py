"""Мягкий отказ отменяет готовность, а не только уход в спящие.

Тред 4231, 30.07.2026. Лид: «maaf kk hari hari ini waktu saya sangat pul kk sedikit lowong
nyh» — сейчас совсем нет времени. Модель выставила ready=true, и ОДНА генерация выдала два
пузыря подряд: «понимаю, давай потом, номер сохранён, удачи» и следом «регистрацию передаю
команде, свяжутся по оплате и расписанию». Стадия ушла в READY, лид уехал в CRM как хендофф.
Менеджер получил карточку «готов оформляться» на человека, который только что отказался.

Два дефекта, и первый без второго ничего не чинит:
  1) сигнал soft_no считался, но применялся лишь в самом низу _stage_for — ветка READY стоит
     выше и до него не доходила;
  2) детектор не знал самой частой формы вежливого отказа — «нет времени».
"""
from __future__ import annotations

import pytest

from app.modules.conversation.signals import (
    BUYING_SIGNAL_RE,
    PAYMENT_INTENT_RE,
    SOFT_NO_RE,
)


@pytest.mark.parametrize("text", [
    "maaf kk hari hari ini waktu saya sangat pul kk sedikit lowong nyh",  # дословно из 4231
    "lagi sibuk banget kak",
    "belum ada waktu",
    "waktu saya padat",
    "kurang waktu luang kak",
    "nanti aja ya kak",
])
def test_no_time_is_a_soft_no(text: str) -> None:
    assert SOFT_NO_RE.search(text)


@pytest.mark.parametrize("text", [
    "sibuk tapi mau daftar kak",
    "saya sibuk, tapi saya mau bayar DP sekarang",
])
def test_busy_but_buying_is_a_yes(text: str) -> None:
    """Слово «занят» стоит в половине сообщений. Без этого противовеса расширенный детектор
    срезал бы настоящих покупателей — цена ошибки тут выше, чем у пропуска."""
    assert SOFT_NO_RE.search(text)
    assert BUYING_SIGNAL_RE.search(text) or PAYMENT_INTENT_RE.search(text)


@pytest.mark.parametrize("text", [
    "oke kak daftarin saya",
    "berapa biayanya?",
    "jadwalnya kapan ya",
    "mau ikutan kak",
])
def test_ordinary_messages_are_not_refusals(text: str) -> None:
    assert not SOFT_NO_RE.search(text)


def test_a_soft_no_cannot_become_a_hand_off() -> None:
    """Ядро правки: ready + телефон больше не даёт READY, если лид только что отказался.

    Слово лида о себе весит больше, чем догадка модели о лиде."""
    from app.adapters.db.models import Lead  # noqa: PLC0415
    from app.domain.enums import Stage  # noqa: PLC0415
    from app.modules.conversation.decision import Decision  # noqa: PLC0415
    from app.modules.conversation.delivery import ReplyDelivery  # noqa: PLC0415

    # _stage_for — чистая функция от решения и лида, но висит на сервисе, которому для
    # конструктора нужна сессия. Пустой экземпляр даёт доступ к методу без БД.
    svc = object.__new__(ReplyDelivery)
    lead = Lead(branch_id=1, stage=Stage.PRESENTING, phone_e164="+628123")
    decision = Decision(reply="ok", stage=Stage.READY, product_slug=None,
                        needs_manager=False, ready=True)

    assert svc._stage_for(decision, lead, 5, None, soft_no=True) == Stage.OBJECTION  # noqa: SLF001
    # без отказа поведение прежнее — настоящий покупатель по-прежнему передаётся
    assert svc._stage_for(decision, lead, 5, None, soft_no=False) == Stage.READY  # noqa: SLF001
