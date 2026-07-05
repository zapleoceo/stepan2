"""restage_leads ghost guard: a lead chased the full follow-up schedule with no reply is
forced dormant (mirrors outbox.py), so the stage re-check can't reactivate a silent ghost."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from scripts.restage_leads import _GHOST_FOLLOWUPS, _ghost_stage  # noqa: E402


def test_ghost_stage_threshold() -> None:
    assert _GHOST_FOLLOWUPS == 3  # = follow-up schedule length (4,24,72h)
    assert _ghost_stage(3) == "dormant"   # schedule exhausted → dormant
    assert _ghost_stage(4) == "dormant"
    assert _ghost_stage(2) is None        # still being chased → let the LLM classify
    assert _ghost_stage(0) is None
