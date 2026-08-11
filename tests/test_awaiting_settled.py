"""Третья корзина «ответ не нужен» и её свойства."""
from app.api._query import IN_QUEUE_EXTRA, SETTLED_EXTRA, awaiting_base


def test_settled_keys_on_events_not_on_the_crm_verdict() -> None:
    """crm_lead_state живёт 300 секунд и перезапрашивается — тред 3066 читался 'hold' 01.08 и
    'proceed' 02.08. Предикат на текущем вердикте заставил бы строки мигать."""
    assert "crm_lead_state" not in SETTLED_EXTRA
    assert "verdict" not in SETTLED_EXTRA
    assert "handed_off_at IS NOT NULL" in SETTLED_EXTRA
    assert "crm hold%" in SETTLED_EXTRA


def test_a_refused_send_only_counts_after_the_last_inbound() -> None:
    """Старый отказ гейта не должен глушить НОВЫЙ вопрос лида."""
    assert "o.scheduled_at > ct.last_in_at" in SETTLED_EXTRA


def test_three_buckets_partition_the_base() -> None:
    """Три корзины должны в сумме давать total, иначе бейдж соврёт."""
    base = awaiting_base()
    settled = f"({base}) AND {SETTLED_EXTRA}"
    queue = f"({base}) AND NOT {SETTLED_EXTRA} AND ({IN_QUEUE_EXTRA})"
    off = f"({base}) AND NOT {SETTLED_EXTRA} AND NOT ({IN_QUEUE_EXTRA})"
    for clause in (settled, queue, off):
        assert clause.count("(") == clause.count(")")
    assert "NOT " + SETTLED_EXTRA in queue
    assert "NOT " + SETTLED_EXTRA in off


def test_badge_shows_only_what_needs_answering() -> None:
    """Бейдж, который никогда не ноль, никто не читает: спящие, готовые и выключенные тоже
    «без ответа», но проблемой не являются."""
    from app.api._ui_panels import inbox_awaiting_badge_html
    html = inbox_awaiting_badge_html(4)
    assert ">4<" in html and "awaiting=queue" in html
    assert "awaiting=off" not in html and "awaiting=settled" not in html


def test_badge_hides_itself_at_zero() -> None:
    from app.api._ui_panels import inbox_awaiting_badge_html
    assert inbox_awaiting_badge_html(0) == ""


def test_badge_swaps_the_list_instead_of_reloading_the_page() -> None:
    """Бейдж опрашивается каждые 15 секунд; полная перезагрузка выбрасывала бы открытый чат."""
    from app.api._ui_panels import inbox_awaiting_badge_html
    html = inbox_awaiting_badge_html(1)
    assert 'hx-get="/ui/threads?awaiting=queue"' in html
    assert 'hx-target="#tl"' in html
    assert "location.href" not in html


def test_the_badge_click_does_not_fall_through_to_the_parent_link() -> None:
    """Бейдж лежит ВНУТРИ <a href="/ui/inbox">. stopPropagation гасит только обработчики
    родителя, а переход по ссылке — не обработчик, а действие браузера по умолчанию: оно
    срабатывало всё равно, и клик по числу открывал полный список входящих вместо тех самых
    чатов, которые бейдж и посчитал. Нужен preventDefault."""
    from app.api._ui_panels import inbox_awaiting_badge_html
    html = inbox_awaiting_badge_html(1)
    assert "preventDefault" in html
    assert "stopPropagation" in html
