"""Comment engine — hourly ingest + a light reply path for public comments.

Deliberately NOT the DM ReplyService: a comment has no thread, no lead, no funnel. The path
is: fetch new comments under our own posts → dedup → triage (filter) → for a real
question/interest, draft ONE short public line grounded in the KB, gate it against
fabrication, and post it (with a DM invite when the author is warm). The DM "hand-off" is an
INVITE in the public text ("DM aku ya kak"), not an unsolicited first DM to a stranger — that
would be a spam vector and against Meta policy. Public mistakes screenshot, so on any guard
doubt we drop the fact and post only the invite.
"""
from __future__ import annotations

import logging

from app.adapters.db.models import Branch, Channel, PostComment
from app.domain.clock import utc_now
from app.modules.conversation import guard
from app.modules.conversation.reply import guard_prompt
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import BranchSettings
from app.ports.channel import ChannelPort
from app.ports.llm import LLMPort

from .filter import classify_comment, is_warm
from .repository import CommentRepo

logger = logging.getLogger(__name__)

# One short, public, KB-grounded line. No prices/dates unless they are in the retrieved
# context (the gate enforces it). A warm author is invited to DM; everyone else just gets the
# answer. Kept tiny on purpose — a public comment reply is a hook, not a brochure.
_COMMENT_PROMPT = (
    "You are MinStep, replying PUBLICLY to a comment under our own Instagram post. Reply in "
    "{lang}, short and friendly (max ~300 chars), warm and human. Answer ONLY from the "
    "KNOWLEDGE BASE below — never invent a price, date, discount, link or fact that isn't "
    "there. If the answer isn't in the KB, don't guess: invite them to DM for details.\n"
    "⛔ PRICES: quote each product's EXACT price from ITS OWN card — never copy a number from "
    "another product's card. If asked for a general price list of several programs, it is "
    "SAFER to name just 1-2 relevant ones with their exact prices and invite a DM for the "
    "full list than to risk mixing prices up. Every number you write must be the one written "
    "on that specific product's card, verbatim.\n"
    "{invite} Do NOT use markdown. Return ONLY the reply text, nothing else.\n\n"
    "KNOWLEDGE BASE:\n{kb}\n\nPOST CAPTION: {caption}\n\nCOMMENT: {comment}"
)
_DM_INVITE = ("End with a short invite to DM us for full details (e.g. 'DM aku ya Kak buat "
              "info lengkapnya 🙏').")
# Correction fed to the regen when the verifier flags unsupported claims (same shape as the
# DM guard's CORRECTION, tuned for the public price-list failure mode).
_CORRECTION = (
    "[System: your draft stated things NOT supported by the knowledge base: {issues}. "
    "Rewrite it using ONLY facts written verbatim in the KB. For any price, use the EXACT "
    "number from that specific product's own card — do NOT mix prices between products. If "
    "you cannot ground a fact, drop it and invite them to DM instead. Return ONLY the reply "
    "text.]")
# Safe fallback when the draft can't be grounded — no facts, just a warm pull into DMs.
_INVITE_ONLY = "Halo Kak! 😊 Boleh DM aku ya biar aku bantu jelasin lengkap 🙏"


class CommentService:
    def __init__(self, session, branch_id: int, llm: LLMPort,
                 knowledge: KnowledgeService, settings: BranchSettings) -> None:  # noqa: ANN001
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.settings = settings
        self.repo = CommentRepo(session, branch_id)

    async def ingest(self, channel: Channel, port: ChannelPort) -> int:
        """Pull new comments under our posts and store them pending. Dedup by native comment
        id (unique constraint is the backstop for two overlapping runs). Returns rows stored."""
        if not hasattr(port, "fetch_comments"):
            return 0
        since = await self.repo.latest_comment_time(channel.id or 0)
        comments = await port.fetch_comments(since=since)
        stored = 0
        for c in comments:
            if not c.external_id or await self.repo.exists(channel.id or 0, c.external_id):
                continue
            await self.repo.add(PostComment(
                branch_id=self.branch_id, channel_id=channel.id or 0,
                external_id=c.external_id, media_id=c.media_id,
                media_caption=c.media_caption, media_permalink=c.media_permalink,
                author_username=c.author_username, author_pk=c.author_pk,
                text=c.text, occurred_at=c.occurred_at, status="pending"))
            stored += 1
        return stored

    async def process(self, channel: Channel, port: ChannelPort) -> int:
        """Triage pending comments and reply to the ones worth it, within the caps. Returns
        replies actually posted."""
        hourly_cap = self.settings.comment_hourly_cap
        per_post_cap = self.settings.comment_per_post_cap
        budget = max(0, hourly_cap - await self.repo.replied_last_hour(channel.id or 0))
        if budget <= 0:
            return 0
        pending = await self.repo.pending(channel.id or 0, limit=budget * 2)
        posted = 0
        for c in pending:
            if posted >= budget:
                break
            action, reason = classify_comment(c.text)
            if action == "hide":
                await self._hide(c, port, reason)
                continue
            if action != "reply":
                c.status = "skipped"
                c.skip_reason = reason
                c.handled_at = utc_now()
                self.session.add(c)
                continue
            if await self.repo.replied_under_post(channel.id or 0, c.media_id) >= per_post_cap:
                c.status = "skipped"
                c.skip_reason = "per_post_cap"
                c.handled_at = utc_now()
                self.session.add(c)
                continue
            if await self._reply(c, port):
                posted += 1
        return posted

    async def _hide(self, c: PostComment, port: ChannelPort, reason: str) -> None:
        """Delete a spam/abuse comment under our post. If the delete fails (transport hiccup),
        leave it pending so the next run retries rather than marking it hidden when it isn't."""
        result = await port.hide_comment(f"{c.media_id}:{c.external_id}")
        if result.ok:
            c.status = "hidden"
            c.skip_reason = reason
            c.handled_at = utc_now()
        else:
            logger.warning("comment hide failed branch=%d comment=%s: %s",
                           self.branch_id, c.external_id, result.error)
            c.attempts += 1  # stays pending → retried next run
        self.session.add(c)

    async def _reply(self, c: PostComment, port: ChannelPort) -> bool:
        text, meta = await self._draft(c)
        # Composite id the transport needs: media pk + replied-to comment pk.
        target = f"{c.media_id}:{c.external_id}"
        result = await port.reply_to_comment(target, text)
        c.handled_at = utc_now()
        if result.ok:
            c.status = "dm_sent" if is_warm(c.text) else "replied"
            c.reply_text = text
            c.reply_external_id = result.external_message_id
            c.llm_info = _fmt(meta)
        else:
            c.status = "error"
            c.skip_reason = (result.error or "")[:200]
            c.attempts += 1
        self.session.add(c)
        return result.ok

    async def _draft(self, c: PostComment) -> tuple[str, dict]:
        """One grounded public line, or a safe DM-invite if it can't be grounded.

        Same two-tier guard as a DM reply, adapted for a public comment: deterministic checks
        first, then — for a risky reply (price/offer/link/story) — the LLM fabrication verify
        (verify_grounding), and if that flags anything, ONE corrective regen, then re-verify.
        A public price mistake screenshots (live: a price list mixed up Python/Cyber/UI-UX
        numbers — every figure was real but attached to the wrong course, which the
        deterministic price gate can't catch but the verifier does), so anything still
        unsupported degrades to the DM invite rather than shipping the fact."""
        branch = await self.session.get(Branch, self.branch_id)
        lang = branch.lang if branch else "id"
        context = await self.knowledge.knowledge_context(
            product_slug=None, lang=lang, query=c.text, light=True)
        prompt = _COMMENT_PROMPT.format(
            lang=lang, kb=context, caption=(c.media_caption or "")[:400], comment=c.text,
            invite=_DM_INVITE if is_warm(c.text) else "")
        text, meta = await self._generate(prompt, context)
        if text is None:
            return _INVITE_ONLY, meta
        if not guard.is_risky(text):
            return text, meta  # nothing risky to verify — a plain grounded line
        # LLM fabrication verify (chat:smart), same gate as the DM reply guard.
        system = await guard_prompt(self.session, self.branch_id)
        unsupported = await guard.verify_grounding(
            self.llm, text, context, branch_id=self.branch_id, thread_id=0, system=system)
        if not unsupported:
            return text, meta
        logger.info("comment verify flagged branch=%d comment=%s: %s → regen",
                    self.branch_id, c.external_id, unsupported[:3])
        fixed, meta2 = await self._generate(
            prompt + "\n" + _CORRECTION.format(issues="; ".join(unsupported[:5])), context)
        if fixed is None:
            return _INVITE_ONLY, meta2 or meta
        if guard.is_risky(fixed):
            still = await guard.verify_grounding(
                self.llm, fixed, context, branch_id=self.branch_id, thread_id=0, system=system)
            if still:
                logger.info("comment still ungrounded after regen branch=%d comment=%s "
                            "→ invite-only", self.branch_id, c.external_id)
                return _INVITE_ONLY, meta2
        return fixed, meta2

    async def _generate(self, prompt: str, context: str) -> tuple[str | None, dict]:
        """chat:fast, then chat:smart on a blank or fabricated draft. Returns (text, meta) or
        (None, meta) when nothing usable came back. The free chat:fast model returns EMPTY
        content ~half the time (it stuffs the answer into a reasoning field) — measured live —
        and the deterministic _fabricated gate is the cheap first pass before the LLM verify."""
        meta: dict = {}
        for capability in ("chat:fast", "chat:smart"):
            try:
                raw, meta = await self.llm.chat(
                    [{"role": "user", "content": prompt}],
                    capability=capability, workflow="comment", branch_id=self.branch_id,
                    max_tokens=250, temperature=0.5)
            except Exception as exc:  # noqa: BLE001 — never let a broker hiccup post garbage
                logger.warning("comment draft failed branch=%d (%s): %s",
                               self.branch_id, capability, exc)
                continue
            text = _clean(raw)
            if text and not _fabricated(text, context):
                return text, meta
        return None, meta


def _fabricated(text: str, context: str) -> bool:
    """Public text is held to a STRICTER bar than a DM: there is no cheap LLM-verify in this
    light path, and a public mistake screenshots. So a risky reply (a price, an offer, a
    link, a story) must be verbatim-grounded in the KB context — every money figure it quotes
    has to appear there. This catches a WRONG price even when the KB has a (different) price,
    which the deterministic invented_price_no_card gate alone misses (it only fires when the
    context has no price at all). Any doubt → drop the fact, post the DM invite instead."""
    if guard.ungrounded_urls(text, context) or guard.impossible_capability_offers(text) \
            or guard.false_delivery_claims(text):
        return True
    return guard.is_risky(text) and not guard.price_claims_grounded(text, context)


def _clean(raw: str) -> str:
    t = (raw or "").strip().strip('"').strip()
    # A single public bubble — collapse any accidental multi-line/JSON noise to one line.
    for marker in ("|||", "\n"):
        if marker in t:
            t = t.split(marker)[0].strip()
    return t[:400]


def _fmt(meta: dict) -> str | None:
    if not meta:
        return None
    model = meta.get("model", "")
    cost = meta.get("cost_usd")
    return f"{model} ${cost:.5f}" if cost is not None else str(model) or None
