"""Продукт треда идёт за разговором, но не перебивает человека.

Тред 3163, 10.08.2026: лид кликнул рекламу демо-ивента 28 июля, тред получил
product_source='ad'. Дальше четыре месяца разговора о полном курсе за 13 млн — а продукт
треда так и остался ивентом за 100 тысяч, потому что 'ad' был заперт от переквалификации.
Продукт треда решает вид события в CRM и цель согласия, так что замок стоил живых сделок:
2568 тредов из 4750 были прибиты к одному давнему клику.

Замок ставили после треда 4943 (модель увела рекламный SMM-лид на Vibe Coding и назвала
цену не того курса), но вред тогда шёл через knowledge_context(product_slug), сужавший базу
знаний до карточки продукта треда. В боевом пути этой функции больше нет — промт собирает
full_knowledge_context() без продукта. Замок стерёг заложенную дверь.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.adapters.db.models import Lead
from app.domain.enums import Stage
from app.modules.conversation.decision import Decision
from app.modules.conversation.delivery import ReplyDelivery


def _thread(slug: str | None, source: str | None):
    return SimpleNamespace(id=1, product_slug=slug, product_source=source)


def _decision(slug: str | None) -> Decision:
    return Decision(reply="ok", stage=Stage.QUALIFYING, product_slug=slug,
                    ready=False, needs_manager=False)


async def _sync(thread, decision: Decision, *, known: bool = True) -> list:
    """Прогоняем _sync_lead_fields на заглушках, собирая записи журнала.

    `known` подменяет сверку по каталогу — сам каталог тут не нужен, нужна развилка."""
    added: list = []

    async def _known_product(_slug: str) -> bool:
        return known

    delivery = SimpleNamespace(
        branch_id=1, settings=None, session=SimpleNamespace(add=added.append),
        _known_product=_known_product)
    await ReplyDelivery._sync_lead_fields(  # noqa: SLF001
        delivery, Lead(branch_id=1), thread, decision)
    return added


# ── что модели теперь разрешено ───────────────────────────────────────────────


async def test_the_model_may_rebind_a_thread_that_came_from_an_ad() -> None:
    """Ровно 3163: клик по рекламе ивента, разговор ушёл на курс."""
    thread = _thread("vibe_coding_demo_event", "ad")

    await _sync(thread, _decision("vibe_coding"))

    assert thread.product_slug == "vibe_coding"
    assert thread.product_source == "model"


async def test_the_model_may_rebind_its_own_earlier_guess() -> None:
    thread = _thread("smm", "model")
    await _sync(thread, _decision("vibe_coding"))
    assert thread.product_slug == "vibe_coding"


async def test_the_model_may_bind_a_thread_that_was_never_anchored() -> None:
    thread = _thread(None, None)
    await _sync(thread, _decision("vibe_coding"))
    assert thread.product_slug == "vibe_coding"
    assert thread.product_source == "model"


# ── что остаётся запретным ────────────────────────────────────────────────────


async def test_a_managers_pick_still_wins() -> None:
    """Человек посмотрел в чат и выбрал руками — догадка модели младше."""
    thread = _thread("smm", "manager")

    await _sync(thread, _decision("vibe_coding"))

    assert thread.product_slug == "smm"
    assert thread.product_source == "manager"


async def test_silence_about_the_product_never_unbinds_a_thread() -> None:
    """Ход, в котором продукт не назван, — отсутствие сведений, а не смена цели."""
    thread = _thread("vibe_coding", "ad")

    await _sync(thread, _decision(None))

    assert thread.product_slug == "vibe_coding"
    assert thread.product_source == "ad"


async def test_a_slug_that_is_not_in_the_catalogue_never_reaches_the_thread() -> None:
    """Экстрактор сверяется со списком слагов, продающая модель — нет, а в досье попадают оба.
    На бою так набралось 28 тредов на несуществующих продуктах (open_house, smm_int,
    uiux_design_skillboost) и ~70 рекламных с вариантами написания (smm-intensive вместо
    smm_intensive). Такой слаг ломает вид события в CRM и заклинивает согласие: записали одно
    написание, следующим ходом сравнили с другим — и готовность гасится на каждом ходу."""
    thread = _thread("smm_intensive", "ad")

    logs = await _sync(thread, _decision("smm-intensive"), known=False)

    assert thread.product_slug == "smm_intensive"
    assert thread.product_source == "ad"
    assert [x for x in logs if getattr(x, "kind", "") == "product_changed"] == []


# ── журнал ────────────────────────────────────────────────────────────────────


async def test_a_rebind_is_written_to_the_chat_timeline_as_the_bot() -> None:
    """Смену должно быть видно в чате и нельзя спутать с кликом менеджера."""
    added = await _sync(_thread("vibe_coding_demo_event", "ad"), _decision("vibe_coding"))
    logs = [x for x in added if getattr(x, "kind", "") == "product_changed"]

    assert len(logs) == 1
    assert logs[0].actor == "agent"
    assert logs[0].detail == "vibe_coding_demo_event → vibe_coding"


async def test_naming_the_same_product_again_writes_nothing() -> None:
    logs = [x for x in await _sync(_thread("vibe_coding", "ad"), _decision("vibe_coding"))
            if getattr(x, "kind", "") == "product_changed"]
    assert logs == []
