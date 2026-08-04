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

from app.connectors.registry import non_outreach_kinds

# Drops a thread whose channel belongs to a connector that cannot write first. `channel` is
# joined by id rather than assumed to be in scope, so both harvest queries can paste it in.
NO_OUTREACH_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM channel nch WHERE nch.id = ct.channel_id"
    "      AND nch.kind IN :nokinds)"
)


def no_outreach_param() -> BindParameter:
    """The expanding bind for NO_OUTREACH_SQL. Bind with `.bindparams(no_outreach_param())`."""
    return bindparam("nokinds", value=list(non_outreach_kinds()), expanding=True)
