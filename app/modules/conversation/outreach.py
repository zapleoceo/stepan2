"""Which channels may be written to FIRST, as a SQL fragment.

The follow-up harvest and the dormant harvest both pick threads by SQL. Both used to assume
every connector could be written to unprompted, because every connector was a DM connector.
The website chat is not: the visitor is anonymous the moment the response is written, so a
nudge has no recipient.

The answer is the connector's own declaration (ConnectorSpec.proactive_outreach), read from
the registry instead of `if branch_id == the site branch` in four places — a branch id is a
deployment accident, and the next deployment's site branch would have a different one.

Expressed as SQL rather than a post-filter for two reasons. The dormant harvest is LIMITed, so
a thread dropped afterwards has already eaten a batch slot that a writable thread wanted. And
a Python filter needed the channel table read a second time, once per harvest per branch per
tick, to learn what the registry already knows.
"""
from __future__ import annotations

from sqlalchemy import bindparam
from sqlalchemy.sql.elements import BindParameter

from app.connectors.registry import non_outreach_kinds, windowed_kinds

# Drops a thread whose channel belongs to a connector that cannot write first. `channel` is
# joined by id rather than assumed to be in scope, so both harvest queries can paste it in.
NO_OUTREACH_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM channel nch WHERE nch.id = ct.channel_id"
    "      AND nch.kind IN :nokinds)"
)


def no_outreach_param() -> BindParameter:
    """The expanding bind for NO_OUTREACH_SQL. Bind with `.bindparams(no_outreach_param())`."""
    return bindparam("nokinds", value=list(non_outreach_kinds()), expanding=True)


# Drops a thread whose platform has stopped accepting automated sends. The window is a fact of
# the channel — `window_until` is the lead's last inbound plus 24h, written by ingest for every
# kind — but only some connectors are REFUSED outside it, and that is their own declaration
# (ConnectorSpec.send_window).
#
# The send path already refused these, so nothing wrong ever went out. What it could not undo
# was the cost: the nudge had been composed by then. On a windowed connector the default
# schedule `1,4,24,120` puts two of its four steps beyond the window by construction — hours
# counted from OUR reply, against a window counted from the LEAD's message — so those two were
# generated and thrown away every time, per thread, forever.
#
# Kept in SQL next to its sibling for the same two reasons: the dormant harvest is LIMITed, so
# a thread dropped afterwards has eaten a slot a live thread wanted, and a Python filter would
# re-read the channel table to learn what the registry already knows.
CLOSED_WINDOW_SQL = (
    " AND (ct.window_until IS NULL OR ct.window_until > :now"
    "      OR NOT EXISTS (SELECT 1 FROM channel wch WHERE wch.id = ct.channel_id"
    "           AND wch.kind IN :windowkinds))"
)


def closed_window_param() -> BindParameter:
    """The expanding bind for CLOSED_WINDOW_SQL. Needs `:now` already bound by the caller."""
    return bindparam("windowkinds", value=list(windowed_kinds()), expanding=True)


# Канал переведён в режим ЧТЕНИЯ (настройка канала `replies_enabled`): принимаем, но не
# отвечаем. Ни один производитель строк не должен для него ничего сочинять.
#
# Одно определение на четыре места, и это не педантизм. Правило успело разъехаться: гейт
# стоял только на основном ответе, а реактивация и фолоапы о нём не знали — 26.08.2026 в
# очереди филиала 1 лежало 68 реактиваций на CRM Jakarta, замолчавшем каналом ранее в тот же
# день. Отправка их не пропустила бы, но текст к тому моменту уже сочинён и оплачен брокеру.
#
# Алиас треда параметром, потому что запросы называют таблицу по-разному: сборщики пишут `ct`,
# а отбор тредов на ответ идёт по SQLAlchemy без алиаса. Подставляется имя из НАШЕГО исходника,
# не из запроса — как и виды коннекторов в awaiting_kind_sql.
#
# В SQL, а не питоновским пост-фильтром: дормант-выборка ограничена LIMIT, и тред, отброшенный
# после него, уже съел слот, которого ждал живой.
def read_only_channel_sql(thread: str = "ct") -> str:
    """Условие «канал этого треда отвечает». Отсутствие настройки = отвечает (так же читает
    `_b` в настройках), поэтому отсекаются только каналы с заданным неистинным значением."""
    # noqa на f-строке: подставляется только имя таблицы из нашего исходника, не из запроса.
    return (
        f" AND NOT EXISTS (SELECT 1 FROM app_setting s"  # noqa: S608
        f"      WHERE s.channel_id = {thread}.channel_id AND s.key = 'replies_enabled'"
        f"        AND lower(s.value) NOT IN ('true', '1', 'yes'))"
    )
