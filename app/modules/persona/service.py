"""Persona-library service: lazy seed, listing with adoption stats, per-branch selection,
favorites, and per-section branch addendum. Pure DB ops; no reply-path coupling."""
from __future__ import annotations

import json
import re

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.adapters.db.models import BranchPersona, Persona, PersonaFavorite
from app.domain.clock import utc_now

_AUTHOR = "Zapleo"
_CONTACT = "https://t.me/zapleosoft"

# Section headings are shared across the seed personas so a branch's per-section addendum
# keys stay stable when it switches persona.
_S = ("## Voice & tone", "## Discovery style", "## Handling objections",
      "## Closing", "## Boundaries")


def _body(voice: str, disc: str, obj: str, close: str, bound: str) -> str:
    return "\n\n".join((
        f"{_S[0]}\n{voice}", f"{_S[1]}\n{disc}", f"{_S[2]}\n{obj}",
        f"{_S[3]}\n{close}", f"{_S[4]}\n{bound}"))


SEED_PERSONAS = [
    {
        "slug": "consultative-closer", "name": "The Consultative Closer", "version": "2.1",
        "lang": "en", "country": "ID",
        "summary": "Warm, asks sharp questions, times the offer to the buying signal.",
        "content": _body(
            "Warm, confident, human. Text like a real person in a DM: 1-3 short sentences, a "
            "touch of humor, at most one emoji when it fits. Never corny, never a wall of text.",
            "Discover before you pitch. Ask ONE sharp question at a time and react like a human. "
            "Uncover the goal, then dig for the pain behind it with a gentle why.",
            "Feel-felt-found, honestly. Never overpromise or invent numbers. Reframe price as "
            "value tied to the pain the lead voiced. If you do not know, say so.",
            "Soft close, no pressure. When the lead is warm, invite one clear next step and ask "
            "for a contact. Time the offer to the buying signal, not to a script.",
            "Stay grounded in the branch facts. One question per turn. If the lead says no, stay "
            "friendly and keep the door open."),
    },
    {
        "slug": "warm-advisor", "name": "The Warm Advisor", "version": "1.4",
        "lang": "en", "country": "ID",
        "summary": "Patient and reassuring, great with nervous first-time buyers.",
        "content": _body(
            "Gentle, patient, encouraging. Lots of reassurance, zero pressure. Short sentences, "
            "friendly, meets anxious beginners where they are.",
            "Go slow. Acknowledge feelings first, then ask one easy question. Normalise starting "
            "from zero. Let the lead set the pace.",
            "Address fear directly and kindly. Name the worry, then show the smallest safe first "
            "step. Never make the lead feel behind.",
            "Invite a low-friction next step (a free session, a look inside) before any big "
            "commitment. Ask for a contact once trust is there.",
            "Never rush or guilt a hesitant lead. Grounded in branch facts. One question "
            "per turn."),
    },
    {
        "slug": "fast-mover", "name": "The Fast Mover", "version": "1.2",
        "lang": "en", "country": "ID",
        "summary": "Concise and momentum-driven, built for high-volume inbound.",
        "content": _body(
            "Crisp and energetic. Very short replies, high momentum, one emoji max. Respects the "
            "lead's time and gets to the point.",
            "Qualify fast with one tight question. Read the intent quickly and skip the small "
            "talk when the lead is already warm.",
            "Answer objections in one line, then move forward. No long justifications; keep the "
            "conversation advancing.",
            "Drive to the next step early and clearly. Ask for a contact the moment interest is "
            "real. Make saying yes easy.",
            "Never spammy or pushy despite the pace. Grounded in branch facts. One question "
            "per turn."),
    },
]


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.strip().lower())
    return s.strip("-") or "section"


def sections(content: str) -> list[tuple[str, str, str]]:
    """Parse `## Heading` sections → list of (title, slug, body). Text before the first
    heading is ignored (personas are fully sectioned)."""
    out: list[tuple[str, str, str]] = []
    parts = re.split(r"(?m)^##\s+", content or "")
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        title = head.strip()
        out.append((title, slugify(title), body.strip()))
    return out


async def ensure_seeded(session: AsyncSession) -> None:
    """One-time lazy seed: if the library is empty, install the starter personas. Idempotent."""
    n = (await session.execute(select(func.count()).select_from(Persona))).scalar_one()
    if n:
        return
    now = utc_now()
    for p in SEED_PERSONAS:
        session.add(Persona(
            slug=p["slug"], name=p["name"], version=p["version"], lang=p["lang"],
            country=p["country"], summary=p["summary"], content=p["content"],
            author_name=_AUTHOR, author_contact=_CONTACT, status="published",
            created_at=now, updated_at=now))
    await session.flush()


def _ver_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


async def list_personas(session: AsyncSession) -> list[Persona]:
    """The library grid: the LATEST version of each persona line (by slug), so re-imported
    versions collapse into one card. The full version history lives on the detail page."""
    rows = list((await session.execute(
        select(Persona).where(Persona.status == "published"))).scalars())
    latest: dict[str, Persona] = {}
    for p in rows:
        cur = latest.get(p.slug)
        if cur is None or _ver_key(p.version) > _ver_key(cur.version):
            latest[p.slug] = p
    return sorted(latest.values(), key=lambda p: p.name)


async def versions_of(session: AsyncSession, slug: str) -> list[Persona]:
    """Every version of a persona line (newest first) — the readable change history."""
    rows = list((await session.execute(
        select(Persona).where(Persona.slug == slug))).scalars())
    return sorted(rows, key=lambda p: _ver_key(p.version), reverse=True)


async def adoption(session: AsyncSession) -> dict[int, tuple[int, int]]:
    """persona_id → (branches using it as active, favorites count)."""
    used = dict((await session.execute(
        select(BranchPersona.persona_id, func.count())
        .where(BranchPersona.persona_id.is_not(None))
        .group_by(BranchPersona.persona_id))).all())
    favs = dict((await session.execute(
        select(PersonaFavorite.persona_id, func.count())
        .group_by(PersonaFavorite.persona_id))).all())
    keys = set(used) | set(favs)
    return {int(k): (int(used.get(k, 0)), int(favs.get(k, 0))) for k in keys}


def _next_version(v: str) -> str:
    try:
        major, _, minor = (v or "1.0").partition(".")
        return f"{int(major)}.{int(minor or 0) + 1}"
    except ValueError:
        return "1.1"


async def import_from_branch(
    session: AsyncSession, branch_id: int, name: str, *,
    lang: str = "id", country: str = "", author_name: str = _AUTHOR,
    author_contact: str = _CONTACT, changelog: str = "",
) -> Persona:
    """Snapshot a branch's FULL non-product config into a versioned library persona: the
    persona core plus every playbook / reference / sales / behaviour doc (all of knowledge_doc).
    Products stay per-branch (separate table), so they're never bundled. Re-importing the same
    name mints the NEXT version, so a branch can refresh its library copy after edits."""
    from app.adapters.db.models import KnowledgeDoc  # noqa: PLC0415
    docs = (await session.execute(
        select(KnowledgeDoc).where(KnowledgeDoc.branch_id == branch_id)
        .order_by(KnowledgeDoc.category, KnowledgeDoc.slug))).scalars().all()
    parts = [f"## {d.slug}\n{(d.content or '').strip()}"
             for d in docs if (d.content or "").strip()]
    slug = slugify(name)
    prev = (await session.execute(
        select(Persona).where(Persona.slug == slug)
        .order_by(Persona.version.desc()))).scalars().first()
    version = _next_version(prev.version) if prev else "1.0"
    note = (changelog or "").strip() or (
        "Re-imported from the branch KB." if prev else "Initial import from the branch KB.")
    now = utc_now()
    persona = Persona(
        slug=slug, name=name, version=version, lang=lang, country=country,
        summary=f"Imported from the {name} branch: persona core + all playbooks, references "
                "and sales docs (everything except the product catalog).",
        content="\n\n".join(parts), changelog=note,
        author_name=author_name, author_contact=author_contact,
        status="published", created_at=now, updated_at=now)
    session.add(persona)
    await session.flush()
    return persona


async def get_persona(session: AsyncSession, pid: int) -> Persona | None:
    return (await session.execute(
        select(Persona).where(Persona.id == pid))).scalar_one_or_none()


async def branch_state(
    session: AsyncSession, branch_id: int,
) -> tuple[int | None, dict[str, str], set[int]]:
    """(active_persona_id, addendum map, favorited persona ids) for a branch."""
    bp = (await session.execute(
        select(BranchPersona).where(BranchPersona.branch_id == branch_id))).scalar_one_or_none()
    active = bp.persona_id if bp else None
    try:
        add = json.loads(bp.addendum) if bp and bp.addendum else {}
    except (json.JSONDecodeError, TypeError):
        add = {}
    favs = set((await session.execute(
        select(PersonaFavorite.persona_id)
        .where(PersonaFavorite.branch_id == branch_id))).scalars())
    return active, (add if isinstance(add, dict) else {}), favs


async def set_active(session: AsyncSession, branch_id: int, persona_id: int) -> None:
    bp = (await session.execute(
        select(BranchPersona).where(BranchPersona.branch_id == branch_id))).scalar_one_or_none()
    if bp is None:
        session.add(BranchPersona(branch_id=branch_id, persona_id=persona_id, addendum="{}"))
    else:
        bp.persona_id = persona_id
        bp.updated_at = utc_now()
    await session.flush()


async def toggle_favorite(session: AsyncSession, branch_id: int, persona_id: int) -> bool:
    fav = (await session.execute(
        select(PersonaFavorite).where(
            PersonaFavorite.branch_id == branch_id,
            PersonaFavorite.persona_id == persona_id))).scalar_one_or_none()
    if fav is None:
        session.add(PersonaFavorite(branch_id=branch_id, persona_id=persona_id))
        await session.flush()
        return True
    await session.delete(fav)
    await session.flush()
    return False


async def save_addendum(
    session: AsyncSession, branch_id: int, section_slug: str, text: str,
) -> None:
    bp = (await session.execute(
        select(BranchPersona).where(BranchPersona.branch_id == branch_id))).scalar_one_or_none()
    if bp is None:
        bp = BranchPersona(branch_id=branch_id, persona_id=None, addendum="{}")
        session.add(bp)
    try:
        data = json.loads(bp.addendum) if bp.addendum else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    text = (text or "").strip()
    if text:
        data[section_slug] = text
    else:
        data.pop(section_slug, None)
    bp.addendum = json.dumps(data, ensure_ascii=False)
    bp.updated_at = utc_now()
    await session.flush()
