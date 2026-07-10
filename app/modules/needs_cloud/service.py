"""Needs-cloud classification + aggregation.

classify_branch  — nightly, incremental: (re)map only leads whose needs changed onto the
                   branch's persistent canonical taxonomy (the stability anchor).
cloud_for        — cheap SQL: COUNT(DISTINCT lead) per entity over a date range, with a 0..1
                   weight for the visual bar. No LLM here — any range renders instantly.
write_snapshot   — freeze today's per-entity counts for history / day-to-day comparison.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Lead, LeadNeedTag, NeedAggSnapshot, NeedEntity, NeedLeadState
from app.domain.clock import utc_now
from app.domain.script_guard import wrong_script
from app.modules.conversation.needs import parse_needs
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

KINDS = ("pains", "jobs", "gains")  # column order: Боли · Цели · Выгоды
_BATCH = 40           # phrases per LLM classify call — bounds tokens
_MAX_LEADS_PER_RUN = 400  # cap the nightly LLM work per branch; the rest drain next night

_SYSTEM = (
    "You group short customer-need phrases (Indonesian/English, from Instagram sales chats) "
    "into a SMALL set of BROAD, stable categories. Return STRICT JSON: an object mapping each "
    "input phrase EXACTLY as given to a short category label IN RUSSIAN (1-2 words, Title case, "
    "a plural noun where natural). \n"
    "RULES:\n"
    "1. REUSE an existing category label VERBATIM whenever the phrase fits it — do not coin a "
    "near-synonym of one already listed.\n"
    "2. Merge synonyms, paraphrases, and sub-topics AGGRESSIVELY into ONE category: e.g. "
    "'mahal'/'gak ada budget'/'terlalu mahal' → 'Цена'; 'SMM'/'социальные сети'/'соцсети'/"
    "'изучение маркетинга соцсетей с нуля' → 'Соцсети'; 'карьера'/'карьерный рост' → 'Карьера'.\n"
    "3. Prefer the BROADEST sensible category — better 10 categories than 40. Avoid vague "
    "catch-alls like 'Услуги'/'Обучение' unless nothing specific fits.\n"
    "4. LABELS ARE ALWAYS IN RUSSIAN (Cyrillic). Common tech abbreviations may stay Latin "
    "(AI, IT, SMM, HR). NEVER use Arabic, Chinese, or any other script — a phrase in "
    "Indonesian/Arabic still gets a RUSSIAN category (e.g. 'coding' → 'Программирование').\n"
    "The input `phrases` is an object of index→phrase. Return a JSON object mapping each "
    "INDEX (same string key) to its category label — do NOT echo the phrase text. Output "
    "ONLY that JSON object."
)

_I18N_SYSTEM = (
    "You translate short Russian category labels (customer-need topics) to English and "
    "Indonesian. The input `labels` is an object index→Russian-label. Return STRICT JSON: an "
    "object mapping each INDEX (same string key) to {\"en\": \"…\", \"id\": \"…\"}. Keep each "
    "translation short (1-2 words, Title case). Output ONLY that JSON object."
)

# A clean category label: Cyrillic/Latin letters, digits and a little punctuation only. Rejects
# the Arabic/CJK garbage a drifting model sometimes emits ('برمجة', 'كمبيوتر', mixed 'Кمبيوتر').
_VALID_LABEL = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9 \-/&.]{0,28}$")


@dataclass
class CloudEntry:
    label: str
    count: int
    weight: float  # 0..1 relative to the column's top entity — drives the visual bar


def _needs_sha(raw: str | None) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _strip_json(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    a, b = s.find("{"), s.rfind("}")
    return s[a:b + 1] if a != -1 and b > a else s


async def _classify_phrases(
    llm: LLMPort, kind: str, existing: list[str], phrases: list[str], branch_id: int,
) -> dict[str, str]:
    """phrase → canonical label, reusing `existing` labels where possible. Best-effort: a
    parse/broker failure yields {} (those phrases go untagged this run, retried when the
    lead's needs next change) rather than corrupting the taxonomy."""
    out: dict[str, str] = {}
    labels = list(existing)  # grows as batches coin new labels, so LATER batches reuse EARLIER
    for i in range(0, len(phrases), _BATCH):        # ones instead of coining a near-duplicate
        chunk = phrases[i:i + _BATCH]
        # index→phrase in, index→label out: the model never echoes the (possibly long) phrase
        # text, so the response can't blow past max_tokens and truncate into invalid JSON.
        numbered = {str(j): p for j, p in enumerate(chunk)}
        user = json.dumps({"existing_categories": labels, "phrases": numbered},
                          ensure_ascii=False)
        try:
            raw, _ = await llm.chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                capability="chat:smart", temperature=0.0, max_tokens=2000,
                workflow="needs_cloud", branch_id=branch_id)
            mapping = json.loads(_strip_json(raw))
        except Exception:
            logger.warning("needs_cloud: classify failed branch=%d kind=%s", branch_id, kind,
                           exc_info=True)
            continue
        if isinstance(mapping, dict):
            for j, ph in enumerate(chunk):
                label = (mapping.get(str(j)) or "").strip() if isinstance(
                    mapping.get(str(j)), str) else ""
                # Reject a garbage/wrong-script label (Arabic/CJK drift) — leave the phrase
                # untagged this run rather than create a junk category; retried when it changes.
                if label and _VALID_LABEL.match(label):
                    out[ph] = label
                    if label not in labels:
                        labels.append(label)
    return out


async def _entity_id(session: AsyncSession, cache: dict[str, int], branch_id: int,
                     kind: str, label: str) -> int:
    key = label.lower()
    if key in cache:
        return cache[key]
    row = (await session.execute(
        select(NeedEntity).where(NeedEntity.branch_id == branch_id, NeedEntity.kind == kind,
                                 NeedEntity.label == label))).scalar_one_or_none()
    if row is None:
        row = NeedEntity(branch_id=branch_id, kind=kind, label=label)
        session.add(row)
        await session.flush()
    cache[key] = row.id  # type: ignore[assignment]
    return row.id  # type: ignore[return-value]


async def classify_branch(session: AsyncSession, branch_id: int, llm: LLMPort) -> int:
    """Incrementally (re)classify leads whose needs changed. Returns leads processed."""
    existing: dict[str, list[str]] = {k: [] for k in KINDS}
    ent_cache: dict[str, dict[str, int]] = {k: {} for k in KINDS}
    for e in (await session.execute(
            select(NeedEntity).where(NeedEntity.branch_id == branch_id))).scalars():
        existing[e.kind].append(e.label)
        ent_cache[e.kind][e.label.lower()] = e.id

    states = {s.lead_id: s.needs_sha for s in (await session.execute(
        select(NeedLeadState).where(NeedLeadState.branch_id == branch_id))).scalars()}

    changed: list[tuple[Lead, str]] = []
    for lead in (await session.execute(
            select(Lead).where(Lead.branch_id == branch_id,
                               Lead.needs.is_not(None))  # type: ignore[union-attr]
            .order_by(Lead.created_at.desc()))).scalars():  # type: ignore[union-attr]
        sha = _needs_sha(lead.needs)
        if states.get(lead.id) != sha:
            changed.append((lead, sha))
        if len(changed) >= _MAX_LEADS_PER_RUN:
            break
    if not changed:
        return 0

    # Gather distinct new phrases per kind across the changed leads, classify in bulk.
    parsed = {lead.id: parse_needs(lead.needs) for lead, _ in changed}
    label_of: dict[str, dict[str, str]] = {}
    for kind in KINDS:
        phrases = sorted({p for prof in parsed.values() for p in getattr(prof, kind)})
        label_of[kind] = await _classify_phrases(
            llm, kind, existing[kind], phrases, branch_id) if phrases else {}

    processed = 0
    for lead, sha in changed:
        await session.execute(text("DELETE FROM lead_need_tag WHERE lead_id = :l"),
                              {"l": lead.id})
        prof = parsed[lead.id]
        for kind in KINDS:
            seen: set[int] = set()
            for phrase in getattr(prof, kind):
                label = label_of[kind].get(phrase)
                if not label:
                    continue
                eid = await _entity_id(session, ent_cache[kind], branch_id, kind, label)
                if eid not in seen:
                    session.add(LeadNeedTag(lead_id=lead.id, kind=kind, entity_id=eid,
                                            branch_id=branch_id))
                    seen.add(eid)
        st = await session.get(NeedLeadState, lead.id)
        if st is None:
            session.add(NeedLeadState(lead_id=lead.id, branch_id=branch_id, needs_sha=sha,
                                      classified_at=utc_now()))
        else:
            st.needs_sha, st.classified_at = sha, utc_now()
            session.add(st)
        processed += 1
    logger.info("needs_cloud: classified branch=%d leads=%d", branch_id, processed)
    return processed  # caller owns the transaction (commit)


def _localized(label: str, i18n_json: str | None, lang: str) -> str:
    """The label in the viewer's UI language, or the canonical Russian if there's no cached
    translation (labels are stored in Russian; label_i18n holds {en, id})."""
    if lang == "ru" or not i18n_json:
        return label
    try:
        return json.loads(i18n_json).get(lang) or label
    except (json.JSONDecodeError, AttributeError):
        return label


async def cloud_for(session: AsyncSession, branch_ids: list[int] | None, kind: str,
                    since: datetime | None, until: datetime | None, limit: int = 20,
                    lang: str = "ru") -> list[CloudEntry]:
    """Top entities for one column over a date range (by lead.created_at), most frequent first,
    labels localized to `lang` from the cached label_i18n (no LLM at render). branch_ids None →
    every branch; a list → those, grouped by the canonical (stable RU) label across branches."""
    sql = ["SELECT e.label, MAX(e.label_i18n) i18n, COUNT(DISTINCT t.lead_id) c",
           "FROM lead_need_tag t",
           "JOIN need_entity e ON e.id = t.entity_id",
           "JOIN lead l ON l.id = t.lead_id",
           "WHERE t.kind = :k"]
    params: dict = {"k": kind, "lim": limit}
    if branch_ids:
        keys = [f"b{i}" for i in range(len(branch_ids))]
        sql.append("AND t.branch_id IN (" + ",".join(f":{k}" for k in keys) + ")")
        params.update(dict(zip(keys, branch_ids, strict=True)))
    if since is not None:
        sql.append("AND l.created_at >= :since")
        params["since"] = since
    if until is not None:
        sql.append("AND l.created_at < :until")
        params["until"] = until
    sql.append("GROUP BY e.label ORDER BY c DESC, e.label LIMIT :lim")
    rows = (await session.execute(text(" ".join(sql)), params)).all()
    top = rows[0][2] if rows else 0
    return [CloudEntry(label=_localized(r[0], r[1], lang), count=r[2],
                       weight=(r[2] / top if top else 0.0)) for r in rows]


async def translate_labels(session: AsyncSession, branch_id: int, llm: LLMPort) -> int:
    """Translate canonical (RU) entity labels missing label_i18n into {en, id}, cached on the
    entity — one broker call, script-guarded, nightly. Returns entities updated."""
    ents = (await session.execute(
        select(NeedEntity).where(NeedEntity.branch_id == branch_id,
                                 NeedEntity.label_i18n.is_(None)))).scalars().all()
    if not ents:
        return 0
    numbered = {str(i): e.label for i, e in enumerate(ents)}
    user = json.dumps({"labels": numbered}, ensure_ascii=False)
    try:
        raw, _ = await llm.chat(
            [{"role": "system", "content": _I18N_SYSTEM}, {"role": "user", "content": user}],
            capability="chat:fast", temperature=0.0, max_tokens=2000,
            workflow="needs_cloud", branch_id=branch_id)
        mapping = json.loads(_strip_json(raw))
    except Exception:
        logger.warning("needs_cloud: label i18n failed branch=%d", branch_id, exc_info=True)
        return 0
    if not isinstance(mapping, dict):
        return 0
    n = 0
    for i, e in enumerate(ents):
        item = mapping.get(str(i))
        if not isinstance(item, dict):
            continue
        en, idn = str(item.get("en") or "").strip(), str(item.get("id") or "").strip()
        # drop a drift to the wrong script; leave label_i18n NULL → retried next run
        if not en or not idn or wrong_script(en, "en") or wrong_script(idn, "id"):
            continue
        e.label_i18n = json.dumps({"en": en, "id": idn}, ensure_ascii=False)
        session.add(e)
        n += 1
    return n


async def write_snapshot(session: AsyncSession, branch_id: int,
                         snap_date: date | None = None) -> int:
    """Freeze today's all-time per-entity counts (idempotent per day). Returns rows written."""
    day = snap_date or utc_now().date()
    rows = (await session.execute(text(
        "SELECT t.kind, t.entity_id, COUNT(DISTINCT t.lead_id) c FROM lead_need_tag t"
        " WHERE t.branch_id = :b GROUP BY t.kind, t.entity_id"), {"b": branch_id})).all()
    written = 0
    for kind, entity_id, c in rows:
        existing = (await session.execute(
            select(NeedAggSnapshot).where(
                NeedAggSnapshot.branch_id == branch_id, NeedAggSnapshot.kind == kind,
                NeedAggSnapshot.entity_id == entity_id, NeedAggSnapshot.snap_date == day))
        ).scalar_one_or_none()
        if existing is None:
            session.add(NeedAggSnapshot(branch_id=branch_id, kind=kind, entity_id=entity_id,
                                        snap_date=day, lead_count=c))
        else:
            existing.lead_count = c
            session.add(existing)
        written += 1
    return written  # caller owns the transaction (commit)
