"""Auto-translate a lead's captured needs (jobs/pains/gains) into the current UI language,
cached per (original phrase, language) on lead.needs_tr — translate once, never re-bill.

Failures degrade to the original (untranslated) text and leave the cache untouched, so a
transient broker error self-heals on the next render instead of getting stuck."""
from __future__ import annotations

import json
import logging

from app.ports.llm import LLMPort

from .needs import NeedsProfile

logger = logging.getLogger(__name__)


def _load_cache(needs_tr: str | None, lang: str) -> dict[str, str]:
    if not needs_tr:
        return {}
    try:
        d = json.loads(needs_tr)
    except (json.JSONDecodeError, TypeError):
        return {}
    v = d.get(lang) if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


def _save_cache(needs_tr: str | None, lang: str, cache: dict[str, str]) -> str:
    try:
        d = json.loads(needs_tr) if needs_tr else {}
    except (json.JSONDecodeError, TypeError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d[lang] = cache
    return json.dumps(d, ensure_ascii=False)


async def translated_needs(
    profile: NeedsProfile, needs_tr: str | None, lang: str, llm: LLMPort,
) -> tuple[NeedsProfile, str | None]:
    """Return (profile with jobs/pains/gains in `lang`, updated needs_tr JSON or None).

    The second element is None when nothing needed translating (pure cache hit) — the
    caller should skip writing to the DB in that case."""
    if not (profile.jobs or profile.pains or profile.gains):
        return profile, None
    cache = _load_cache(needs_tr, lang)
    all_items = list(dict.fromkeys([*profile.jobs, *profile.pains, *profile.gains]))
    missing = [s for s in all_items if s not in cache]
    new_tr = None
    if missing:
        fresh = await _translate_batch(llm, missing, lang)
        if fresh:
            cache = {**cache, **fresh}
            new_tr = _save_cache(needs_tr, lang, cache)
    translated = NeedsProfile(
        jobs=[cache.get(s, s) for s in profile.jobs],
        pains=[cache.get(s, s) for s in profile.pains],
        gains=[cache.get(s, s) for s in profile.gains],
        discovery_complete=profile.discovery_complete,
    )
    return translated, new_tr


async def _translate_batch(llm: LLMPort, items: list[str], lang: str) -> dict[str, str]:
    """One broker call for every cache-miss phrase; empty dict (no cache write) on any
    failure so the next render retries instead of freezing a bad/partial result."""
    from app.modules.notifications.summarize import lang_name  # noqa: PLC0415

    target = lang_name(lang)
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(items))
    messages = [
        {
            "role": "system",
            "content": (
                f"Translate each numbered short phrase to {target}. Return ONLY a JSON "
                "array of strings, same order, one translation per line, no numbering."
            ),
        },
        {"role": "user", "content": numbered},
    ]
    try:
        raw, _ = await llm.chat(
            messages, capability="chat:fast", max_tokens=600, workflow="translate",
        )
        arr = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — degrade to originals, never break the render
        logger.warning("needs translate failed lang=%s: %s", lang, exc)
        return {}
    if not (isinstance(arr, list) and len(arr) == len(items)):
        return {}
    return {orig: str(tr).strip() or orig for orig, tr in zip(items, arr, strict=True)}
