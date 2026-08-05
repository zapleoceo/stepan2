"""The proactive comment mission: go to a lead's own feed and say one useful thing.

Everything else Stepan does is an answer. This is the one job where we speak first, into
somebody else's space, which makes it both the highest-leverage thing here and the only one
that can quietly cost the account. The design is therefore built around stopping rather than
around reaching: every gate is cheap, every gate comes before the expensive step it guards,
and a gate that cannot decide says no.

The order is: who → have we been here → their newest post → is it worth it (chat:fast) → write
it (chat:smart) → post. A row is written for the rejections too. Without them nobody can ever
answer whether the judge is too strict, and that threshold is the only real knob this has.

Audience is deliberately narrow: people who have already written to us. They know the account,
so a comment is a reminder rather than an intrusion, and a stranger's feed is a different
mission with a different risk profile that nothing here is allowed to drift into.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import timedelta

from app.adapters.db.models import Channel, Lead, OutboundComment
from app.connectors.registry import supports
from app.connectors.spec import Capability
from app.domain.clock import utc_now
from app.modules.conversation.canned import comment_persona
from app.modules.conversation.translate import target_for_lang
from app.modules.missions import PROACTIVE_COMMENT
from app.modules.missions.budget import account_spend, log_exhausted, share_of
from app.modules.settings.service import BranchSettings
from app.ports.channel import CandidatePost, OutboundCommentPort
from app.ports.llm import LLMPort

from . import compose, relevance
from .outbound_repo import OutboundRepo

logger = logging.getLogger(__name__)

# How far back a post can be and still be worth commenting under. A comment on last month's
# photo is not attentiveness, it is an account working through a list.
_POST_MAX_AGE_DAYS = 14
# Posts to look at per lead. Their newest is what a person would react to; scanning deeper is
# extra private-API calls in exchange for older, weaker candidates.
_POSTS_PER_LEAD = 2
# One comment per person per this many days.
_PER_AUTHOR_QUIET_DAYS = 30
# Leads examined per run, before any of the gates. Bounded because each survivor costs a
# private-API call, and the pool (every lead who ever wrote) is thousands deep.
_SCAN_PER_RUN = 25


class ProactiveCommentService:
    def __init__(self, session, branch_id: int, llm: LLMPort,  # noqa: ANN001
                 settings: BranchSettings, *, about: str, lang: str,
                 brand_terms: tuple[str, ...] = ()) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.settings = settings
        self.about = about
        self.lang = lang
        self.brand_terms = brand_terms
        self.repo = OutboundRepo(session, branch_id)

    async def run(self, channel: Channel, port: OutboundCommentPort) -> int:
        """One pass. Returns comments actually posted."""
        budget = await self._budget(channel)
        if budget <= 0:
            return 0
        leads = await self.repo.candidates(
            channel.id or 0, _SCAN_PER_RUN, quiet_days=_PER_AUTHOR_QUIET_DAYS)
        posted = 0
        for lead in leads:
            if posted >= budget:
                break
            try:
                if await self._visit(lead, channel, port):
                    posted += 1
            except Exception:
                logger.exception("proactive comment failed branch=%d lead=%s",
                                 self.branch_id, lead.id)
        return posted

    async def _budget(self, channel: Channel) -> int:
        """What this mission may spend right now — the tightest of three ceilings.

        Its own daily quota, its share of the account's hourly budget, and whatever the
        account has left after the two reactive missions. The account-wide one is the ceiling
        that actually protects anything; the other two exist so this mission cannot eat a
        quiet hour's entire allowance in one burst."""
        left_today = self.settings.proactive_comment_daily_cap - \
            await self.repo.sent_today(channel.id or 0)
        if left_today <= 0:
            return 0
        spend = await account_spend(self.session, channel.id or 0, self.settings.hourly_cap)
        if spend.exhausted:
            log_exhausted(channel.id or 0, PROACTIVE_COMMENT.key, spend)
            return 0
        return max(0, min(left_today, share_of(spend, PROACTIVE_COMMENT.budget_share)))

    async def _visit(self, lead: Lead, channel: Channel,
                     port: OutboundCommentPort) -> bool:
        pk = str(lead.ig_user_id or "")
        if not pk or await self.repo.wrote_to_recently(
                channel.id or 0, pk, _PER_AUTHOR_QUIET_DAYS):
            return False
        posts = await port.fetch_user_posts(pk, limit=_POSTS_PER_LEAD)
        post = _freshest(posts)
        if post is None:
            return False
        return await self._consider(lead, post, channel, port)

    async def _consider(self, lead: Lead, post: CandidatePost, channel: Channel,
                        port: OutboundCommentPort) -> bool:
        if await self.repo.seen(channel.id or 0, post.media_id):
            return False
        row = OutboundComment(
            branch_id=self.branch_id, channel_id=channel.id or 0, lead_id=lead.id,
            media_id=post.media_id, media_permalink=post.permalink,
            media_caption=post.caption[:2000], author_pk=post.author_pk,
            author_username=lead.ig_username, post_taken_at=post.taken_at)
        self.repo.add(row)
        verdict = await relevance.judge(
            self.llm, post, about=self.about, lang_name=target_for_lang(self.lang),
            needs=_needs_line(lead), branch_id=self.branch_id)
        row.relevant = verdict.relevant
        if not verdict.relevant:
            row.status = "skipped"
            row.skip_reason = verdict.reason
            row.handled_at = utc_now()
            return False
        text, meta = await compose.draft(
            self.llm, post, angle=verdict.angle, persona=comment_persona(self.lang),
            lang=self.lang, lang_name=target_for_lang(self.lang),
            brand_terms=self.brand_terms, branch_id=self.branch_id)
        if text is None:
            row.status = "skipped"
            row.skip_reason = "draft rejected"
            row.handled_at = utc_now()
            return False
        result = await port.comment_on_post(post.media_id, text)
        row.handled_at = utc_now()
        row.llm_info = _fmt(meta)
        if result.ok:
            row.status = "sent"
            row.text = text
            row.external_id = result.external_message_id
            return True
        row.status = "error"
        row.skip_reason = (result.error or "")[:200]
        return False


def _freshest(posts: list[CandidatePost]) -> CandidatePost | None:
    """Their newest post, if it is recent enough to react to at all."""
    cutoff = utc_now() - timedelta(days=_POST_MAX_AGE_DAYS)
    fresh = [p for p in posts if p.taken_at >= cutoff]
    return max(fresh, key=lambda p: p.taken_at) if fresh else None


def _needs_line(lead: Lead) -> str:
    """What the lead told us they were after, as plain text for the judge.

    Best-effort: the needs profile is JSON written by a different subsystem and a lead who
    never got past hello has none. An empty string simply drops that paragraph from the
    prompt, which is the honest thing to do — inventing an interest for somebody is how a
    comment ends up assuming something the caption never said."""
    raw = lead.needs or ""
    if not raw.strip():
        return ""
    try:
        d = json.loads(raw)
    except ValueError:
        return raw[:300]
    if not isinstance(d, dict):
        return ""
    parts: list[str] = []
    for key in ("jobs", "pains", "gains"):
        vals = d.get(key)
        if isinstance(vals, list) and vals:
            parts.append(f"{key}: " + "; ".join(str(v) for v in vals[:3]))
    return " | ".join(parts)[:600]


def _fmt(meta: dict) -> str | None:
    if not meta:
        return None
    model = meta.get("model", "")
    cost = meta.get("cost_usd")
    return f"{model} ${cost:.5f}" if cost is not None else str(model) or None


def runs_on(channel: Channel, settings: BranchSettings, about: str) -> bool:
    """Three independent yeses. The connector must be able to write into somebody else's
    space, the branch must have switched the mission on, and somebody must have written the
    line describing who we are — without it the judge has no standard to measure a post
    against and would wave through anything that merely looked cheerful."""
    return bool(
        supports(channel.kind, Capability.OUTBOUND_COMMENT)
        and settings.agent_enabled
        and settings.proactive_comments_enabled
        and about.strip())


def jitter_seconds(max_s: float) -> float:
    """Offset the run off the cron's fixed second, same reason as the DM ingest: a private-API
    walk that starts on the same machine tick every hour is a pattern."""
    return random.uniform(0, max_s)  # noqa: S311 — jitter, not crypto
