"""Persona-library service: listing with adoption stats, per-branch selection, favorites, and
per-section branch addendum. Pure DB ops; no reply-path coupling.

Nothing is seeded here any more. The library used to ship one built-in persona, "website-demo"
— a browsable copy of the landing chat's prompt, whose runtime original was a Python constant
in app/api/_routes_demo._SYSTEM. Two copies of one text, already drifting. Since S6 the site is
an ordinary branch and its persona is a prompt-library row (app/modules/website/library.py)
cloned into it like any other branch's, so there is a single text to edit. What is left in
this table is what branches import from themselves.
"""
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
