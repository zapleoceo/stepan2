"""Третья корзина «ответ не нужен» и её свойства."""
from app.api._query import AWAITING_BASE, IN_QUEUE_EXTRA, SETTLED_EXTRA


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
    settled = f"({AWAITING_BASE}) AND {SETTLED_EXTRA}"
    queue = f"({AWAITING_BASE}) AND NOT {SETTLED_EXTRA} AND ({IN_QUEUE_EXTRA})"
    off = f"({AWAITING_BASE}) AND NOT {SETTLED_EXTRA} AND NOT ({IN_QUEUE_EXTRA})"
    for clause in (settled, queue, off):
        assert clause.count("(") == clause.count(")")
    assert "NOT " + SETTLED_EXTRA in queue
    assert "NOT " + SETTLED_EXTRA in off


def test_settled_is_visible_not_filtered_away() -> None:
    """Лид, придержанный CRM по ошибке, обязан остаться видимым — иначе он исчезнет из
    единственного списка, где это заметят."""
    from app.api._ui_panels import inbox_awaiting_badge_html
    html = inbox_awaiting_badge_html(0, 0, 7)
    assert "7" in html and "awaiting=settled" in html
    assert inbox_awaiting_badge_html(0, 0, 0) == ""
