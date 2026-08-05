"""Is this post one we should say anything under at all?

The cheap half of the proactive mission, and the half that decides whether it is worth doing.
A comment under a stranger's holiday photo is noise on both sides; a comment under the post
where they show the thing they are learning is the whole point. Nothing about that judgement
needs an expensive model — it is a yes/no over a caption — so it runs on chat:fast, and only
the yes goes on to chat:smart to be written.

Two failure modes are worth more than the rest, and the prompt is built around them:

  Wrong place. A branch serves one city. A caption in Portuguese under a photo from Lisbon
  belongs to somebody who once asked a question while travelling; writing there reaches nobody
  who could enrol and reads as a bot working through its contact list.

  Wrong moment. Illness, a death, a break-up, a religious observance, politics. A brand
  appearing under those is the single most reliably screenshotted mistake in this whole
  system, and no amount of warmth in the wording rescues it.

Both are refusals by default: the judge is told to answer "no" whenever it is unsure, because
the cost of a missed post is zero and the cost of a wrong one is public.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.ports.channel import CandidatePost
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_PROMPT = (
    "You decide whether our Instagram account should leave a friendly comment under this "
    "post. Who we are and where: {about}. The post's author once wrote to us, so they already "
    "know us.\n\n"
    "Answer NO unless ALL of these hold:\n"
    "1. The caption is in {lang_name} or English. Another language means the author is "
    "somewhere we cannot serve them, however friendly the post is.\n"
    "2. The post is about something we could react to warmly and specifically: what they "
    "made, built, filmed, designed, studied, achieved, or are working on. A plain selfie, a "
    "meal, a view, a repost or a quote is not enough.\n"
    "3. Nothing sensitive: illness, death, grief, a break-up, religion, politics, money "
    "trouble, an accident. A brand under any of those is a mistake nobody can undo.\n"
    "4. It is not itself an advertisement, a giveaway, or somebody else's promotion.\n\n"
    "If you are unsure about ANY of the four, answer NO. A post we stay silent under costs "
    "nothing; a comment in the wrong place is public and permanent.\n\n"
    "{about_author}"
    "CAPTION:\n'''{caption}'''\n\n"
    'Return ONLY JSON: {{"relevant": true|false, "reason": "<max 12 words, why>", '
    '"angle": "<if true: the ONE specific thing in the post worth reacting to, max 12 words>"}}'
)

_ABOUT = ("WHAT WE KNOW ABOUT THE AUTHOR (from their earlier messages to us): {needs}\n"
          "Use it only to judge whether the post connects to what they were after — do NOT "
          "treat it as licence to sell.\n\n")

# A caption too short to judge is judged NO. Two words and an emoji carry no evidence that any
# of the four conditions hold, and the judge will happily hallucinate the rest.
_MIN_CAPTION = 40


@dataclass(frozen=True)
class Verdict:
    relevant: bool
    reason: str
    angle: str = ""


def _refuse(reason: str) -> Verdict:
    return Verdict(relevant=False, reason=reason)


async def judge(
    llm: LLMPort, post: CandidatePost, *, about: str, lang_name: str,
    needs: str = "", branch_id: int = 0,
) -> Verdict:
    """One cheap yes/no on whether to comment. Never raises — a broker hiccup is a NO."""
    caption = (post.caption or "").strip()
    if len(caption) < _MIN_CAPTION:
        return _refuse("caption too short to judge")
    prompt = _PROMPT.format(
        about=about[:400], lang_name=lang_name,
        about_author=_ABOUT.format(needs=needs[:600]) if needs.strip() else "",
        caption=caption[:1200])
    try:
        raw, _meta = await llm.chat(
            [{"role": "user", "content": prompt}],
            capability="chat:fast", workflow="comment_relevance", branch_id=branch_id,
            max_tokens=150, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — a broker failure must never become a yes
        logger.warning("relevance judge failed branch=%d media=%s: %s",
                       branch_id, post.media_id, exc)
        return _refuse("judge unavailable")
    return parse_verdict(raw)


def parse_verdict(raw: str) -> Verdict:
    """Read the judge's JSON. Anything unparseable is a NO — the free chat:fast models return
    an empty body or a fenced block often enough that treating garbage as consent would mean
    commenting under posts nothing ever looked at."""
    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.index("{"):] if "{" in body else ""
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return _refuse("no verdict returned")
    try:
        d = json.loads(body[start:end + 1])
    except ValueError:
        return _refuse("unparseable verdict")
    if not isinstance(d, dict) or d.get("relevant") is not True:
        return _refuse(str(d.get("reason", "not relevant"))[:200] if isinstance(d, dict)
                       else "not relevant")
    angle = str(d.get("angle", "")).strip()
    if not angle:
        # A yes with nothing specific to react to is how a generic "keren kak 🔥" gets written,
        # which is exactly the comment that reads as a bot.
        return _refuse("relevant but nothing specific to react to")
    return Verdict(relevant=True, reason=str(d.get("reason", ""))[:200], angle=angle[:200])
