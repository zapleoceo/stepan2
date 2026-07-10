"""Needs cloud — AI-aggregated tag cloud of what leads want (pains/jobs/gains).

Two layers: nightly incremental CLASSIFICATION maps each lead's free-text needs onto a
STABLE canonical taxonomy (so labels don't churn day-to-day); the widget then just COUNTS
tags over the selected date range — no LLM at render, any range instant. A daily snapshot
preserves history."""
from .service import KINDS, classify_branch, cloud_for, write_snapshot

__all__ = ["KINDS", "classify_branch", "cloud_for", "write_snapshot"]
