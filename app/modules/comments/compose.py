"""Writing the one line that goes under somebody else's post.

The expensive half, and it only ever runs on a post the cheap judge already approved. Worth
the smart model for a reason that has nothing to do with difficulty: a generic comment is
strictly worse than none. "Keren kak 🔥" under a stranger's work is what every bot on the
platform writes, and it is recognised instantly — by the author, by everyone reading, and by
the platform. The only version of this that earns anything is one that could not have been
written without reading the post.

So the prompt forbids more than it asks for. No course name, no price, no link, no invitation,
no question that fishes for a reply. What is left is a person reacting to a specific thing,
which is also the only thing our account has any standing to say uninvited.

The gates after generation are deterministic and unforgiving for the same reason as the
reactive path: a public mistake screenshots. Here they are stricter still, because under our
own post a factual slip is at least on-topic, and under a stranger's it is an advert nobody
asked for.
"""
from __future__ import annotations

import logging
import re

from app.modules.conversation import guard
from app.ports.channel import CandidatePost
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_PROMPT = (
    "Write ONE short comment to leave under this Instagram post, as {persona}, in {lang_name} "
    "(the language of the caption). The author once wrote to us, so we are not strangers.\n\n"
    "React to exactly this: {angle}\n\n"
    "Rules, all of them hard:\n"
    "- One or two sentences. Shorter is better.\n"
    "- Say something only a reader of THIS post could say. No praise that would fit any post.\n"
    "- Do NOT mention our courses, our name, prices, discounts, enrolment or events.\n"
    "- Do NOT include a link, a hashtag, or an @mention.\n"
    "- Do NOT invite them anywhere, and do NOT ask a question designed to start a sale.\n"
    "- Do NOT claim anything about them you cannot see in the caption.\n"
    "- No markdown. At most one emoji, and only if it fits the language.\n\n"
    "CAPTION:\n'''{caption}'''\n\n"
    "Return ONLY the comment text."
)

# The hard ceiling. A long comment under someone else's post reads as an advert regardless of
# what it says, and the platform's own spam heuristics agree.
_MAX_CHARS = 220

_LINK_RE = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|id|net|org|co)\b)", re.I)
_TAG_RE = re.compile(r"[@#]\w")


def rejected(text: str, *, lang: str, brand_terms: tuple[str, ...] = ()) -> str | None:
    """Why this draft must not be posted, or None if it may. Deterministic only.

    Everything here is a rule about what a comment under a stranger's post may contain, not a
    rule about facts — there is nothing to ground against, because the correct comment states
    no facts of ours at all. A draft that reaches for one has misunderstood the job, and the
    safe move is silence: unlike the reactive path there is no fallback line worth posting."""
    body = (text or "").strip()
    if not body:
        return "empty draft"
    if len(body) > _MAX_CHARS:
        return "too long"
    if _LINK_RE.search(body):
        return "contains a link"
    if _TAG_RE.search(body):
        return "contains a mention or hashtag"
    if guard.is_risky(body, lang):
        # Prices, offers, promises. Under our own post these are answerable from the KB; here
        # they are an advert under somebody's photo.
        return "reads as a sales pitch"
    low = body.lower()
    for term in brand_terms:
        if term and term.lower() in low:
            return "names the brand"
    return None


async def draft(
    llm: LLMPort, post: CandidatePost, *, angle: str, persona: str, lang: str,
    lang_name: str, brand_terms: tuple[str, ...] = (), branch_id: int = 0,
) -> tuple[str | None, dict]:
    """One comment, or (None, meta) if nothing publishable came back.

    chat:smart only. The reactive path tries chat:fast first and falls back, which is right
    when a weak draft still beats silence; here silence is the better outcome, so a cheap
    draft that half-works is not worth the public space it would take."""
    prompt = _PROMPT.format(persona=persona, lang_name=lang_name, angle=angle,
                            caption=(post.caption or "")[:1200])
    try:
        raw, meta = await llm.chat(
            [{"role": "user", "content": prompt}],
            capability="chat:smart", workflow="proactive_comment", branch_id=branch_id,
            max_tokens=200, temperature=0.7)
    except Exception as exc:  # noqa: BLE001 — a broker hiccup must not post anything
        logger.warning("proactive draft failed branch=%d media=%s: %s",
                       branch_id, post.media_id, exc)
        return None, {}
    text = _clean(raw)
    reason = rejected(text, lang=lang, brand_terms=brand_terms)
    if reason:
        logger.info("proactive draft dropped branch=%d media=%s: %s",
                    branch_id, post.media_id, reason)
        return None, meta
    return text, meta


def _clean(raw: str) -> str:
    t = (raw or "").strip().strip('"').strip()
    if "\n" in t:
        t = t.split("\n")[0].strip()
    return t[:_MAX_CHARS + 1]  # +1 so an over-long draft still trips the length gate
