"""Кто решает, о каком продукте этот тред.

Порядок старшинства: ручной выбор менеджера, потом рекламный клик, потом догадка модели.
Слаг любой из них обязан существовать в каталоге филиала.

10.08.2026 замок с рекламы сняли и в тот же день вернули, потому что разбор был верным, но
неполным. Вред из треда 4943 действительно шёл через knowledge_context(product_slug),
сужавший базу знаний до карточки продукта треда, и той функции в боевом пути больше нет.
Но у привязки есть вторая работа: она уходит в CRM как «Minat», и по ней менеджер понимает,
о чём звонить. Тред 2791 сломался ровно об это через 33 минуты после снятия.

Случай 3163 (клик по рекламе ивента, разговор давно о курсе за 13 млн) закрывается не
привязкой, а целью согласия — см. test_consent_target. Это разные вопросы: привязка отвечает
за атрибуцию, согласие — за то, что лежит на столе прямо сейчас.
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


async def test_an_ad_click_outranks_one_turn_of_guessing() -> None:
    """Замок с 'ad' сняли 10.08.2026 и в тот же день вернули.

    Через 33 минуты после снятия тред 2791 (рекламный клик, привязка smm_intensive — верная,
    Степан весь разговор продавал именно его: Rp 1.882.955, DP 500.000, две недели) уехал на
    social_media_bootcamp, другой курс за 750.000 на один день. Ни одной неверной цены в чат
    не ушло — ценовой путь вреда из 4943 действительно мёртв, — но продукт треда уходит в CRM
    как «Minat», и менеджер получил не то название. Счёт по трём известным случаям: клик прав
    дважды (4943, 2791), модель права один раз (3163). Один ход догадки слабее клика.

    Случай 3163 закрывается не здесь, а целью согласия — она за разговором и идёт."""
    thread = _thread("smm_intensive", "ad")

    logs = await _sync(thread, _decision("social_media_bootcamp"))

    assert thread.product_slug == "smm_intensive"
    assert thread.product_source == "ad"
    assert [x for x in logs if getattr(x, "kind", "") == "product_changed"] == []


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
    thread = _thread("smm_intensive", "model")

    logs = await _sync(thread, _decision("smm-intensive"), known=False)

    assert thread.product_slug == "smm_intensive"
    assert thread.product_source == "model"
    assert [x for x in logs if getattr(x, "kind", "") == "product_changed"] == []


# ── журнал ────────────────────────────────────────────────────────────────────


async def test_a_rebind_is_written_to_the_chat_timeline_as_the_bot() -> None:
    """Смену должно быть видно в чате и нельзя спутать с кликом менеджера."""
    added = await _sync(_thread("vibe_coding_demo_event", "model"), _decision("vibe_coding"))
    logs = [x for x in added if getattr(x, "kind", "") == "product_changed"]

    assert len(logs) == 1
    assert logs[0].actor == "agent"
    assert logs[0].detail == "vibe_coding_demo_event → vibe_coding"


async def test_naming_the_same_product_again_writes_nothing() -> None:
    logs = [x for x in await _sync(_thread("vibe_coding", "ad"), _decision("vibe_coding"))
            if getattr(x, "kind", "") == "product_changed"]
    assert logs == []
