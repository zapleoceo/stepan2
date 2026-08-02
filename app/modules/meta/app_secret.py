"""The one place that answers "what is this app's secret?".

The secret belongs to the Meta APP, not to a branch and not to a channel. There is one app
(Stepan by Zapleo) and one secret, so storing it per branch — or, worse, once in .env and again
in the admin — creates a value that can disagree with itself. Rotate it in Meta, update one
copy, and you get a system where the webhook verifies but the connect button fails, or the
reverse, with nothing in the logs to say why.

Lookup order, most specific first:

1. STEPAN2_META_APP_SECRET_<branch_id> — the legacy shape, kept because branch 1 runs against
   a different Meta app (the EdTech client's own) and must keep its own secret.
2. STEPAN2_META_APP_SECRET — the product's app. This is what every new branch uses; adding a
   client must not require a new environment variable.
"""
from __future__ import annotations

import os

_PREFIX = "STEPAN2_META_APP_SECRET"


def app_secret_for(branch_id: int) -> str:
    """The Meta app secret this branch signs with. Empty string when unset — callers are
    fail-closed, and an empty secret must never be mistaken for a valid one."""
    return (os.environ.get(f"{_PREFIX}_{branch_id}")
            or os.environ.get(_PREFIX)
            or "")
