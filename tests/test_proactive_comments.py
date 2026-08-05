"""The one mission that speaks first, and every gate that stops it.

Reading these top to bottom is the fastest way to see what the feature will and will not do.
Almost all of them assert a refusal, which is the shape of the feature: a post we stay silent
under costs nothing, and a comment in the wrong place is public, permanent, and screenshotted.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import json  # noqa: E402
from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Message,
    OutboundComment,
)
from app.domain.clock import utc_now  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402
from app.modules.comments import compose, relevance  # noqa: E402
from app.modules.comments.outbound_repo import OutboundRepo  # noqa: E402
from app.modules.comments.proactive import ProactiveCommentService, runs_on  # noqa: E402
from app.ports.channel import CandidatePost, SendResult  # noqa: E402

_NOW = utc_now()


# --------------------------------------------------------------------------- doubles


class _LLM:
    """Answers the judge and the writer separately, so a test can approve a post and still
    watch what happens to the draft."""

    def __init__(self, verdict: str = "", comment: str = "keren banget hasil renderingnya") -> None:
        self.verdict = verdict or json.dumps(
            {"relevant": True, "reason": "shows their own work", "angle": "the 3D render"})
        self.comment = comment
        self.calls: list[str] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
        cap = kw.get("capability", "")
        self.calls.append(cap)
        body = self.verdict if cap == "chat:fast" else self.comment
        return body, {"model": "x", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003
        return [[0.0] for _ in texts]


class _Port:
    kind = ChannelKind.INSTAGRAM

    def __init__(self, posts: list[CandidatePost], *, ok: bool = True) -> None:
        self.posts = posts
        self.ok = ok
        self.posted: list[tuple[str, str]] = []

    async def fetch_user_posts(self, user_pk: str, *, limit: int = 3) -> list[CandidatePost]:
        return self.posts[:limit]

    async def comment_on_post(self, media_id: str, text: str) -> SendResult:
        self.posted.append((media_id, text))
        return SendResult(ok=self.ok, external_message_id="c1" if self.ok else None,
                          error=None if self.ok else "feedback_required")


class _Settings:
    agent_enabled = True
    hourly_cap = 150
    proactive_comments_enabled = True
    proactive_comment_about = "coding and design courses in Jakarta"
    proactive_comment_daily_cap = 5


def _post(*, media_id: str = "m1", days_old: int = 1, caption: str | None = None) -> CandidatePost:
    return CandidatePost(
        media_id=media_id, author_pk="777",
        caption=caption if caption is not None else
        "Akhirnya selesai juga render 3D interior pertama aku, tiga minggu belajar Blender",
        taken_at=_NOW - timedelta(days=days_old))


async def _seed(session, *, ig_user_id: str = "777") -> tuple[int, Channel, Lead]:  # noqa: ANN001
    b = Branch(name="Jakarta", lang="id")
    session.add(b)
    await session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="itstep")
    session.add(ch)
    await session.flush()
    lead = Lead(branch_id=b.id, ig_user_id=ig_user_id, ig_username="andi")
    session.add(lead)
    await session.flush()
    th = ChannelThread(channel_id=ch.id, lead_id=lead.id, external_thread_id="t1")
    session.add(th)
    await session.flush()
    session.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id="m1",
                        direction="in", sent_by="lead", text="halo",
                        occurred_at=_NOW - timedelta(days=2)))
    await session.flush()
    return int(b.id), ch, lead


def _svc(session, bid: int, llm) -> ProactiveCommentService:  # noqa: ANN001
    return ProactiveCommentService(
        session, bid, llm, _Settings(), about=_Settings.proactive_comment_about,
        lang="id", brand_terms=("Jakarta",))


# --------------------------------------------------------------------------- the judge


def test_a_verdict_that_did_not_parse_is_a_no() -> None:
    """Free chat:fast models return an empty body or a fenced block often enough that this
    is the common case, not the exotic one. Reading garbage as consent would mean commenting
    under posts nothing ever actually looked at."""
    assert not relevance.parse_verdict("").relevant
    assert not relevance.parse_verdict("sure, go ahead!").relevant
    assert not relevance.parse_verdict('{"relevant": true').relevant


def test_a_yes_with_nothing_specific_to_react_to_is_a_no() -> None:
    """A yes without an angle is how "keren kak 🔥" gets written — the exact comment that
    reads as a bot to the author and to everyone scrolling past."""
    verdict = relevance.parse_verdict('{"relevant": true, "reason": "nice", "angle": ""}')

    assert not verdict.relevant


def test_a_fenced_json_block_is_still_read() -> None:
    got = relevance.parse_verdict('```json\n{"relevant": true, "angle": "their first render"}\n```')

    assert got.relevant and got.angle == "their first render"


@pytest.mark.asyncio
async def test_a_caption_too_short_to_judge_never_reaches_the_model() -> None:
    """Two words and an emoji carry no evidence for any of the four conditions, and the judge
    would cheerfully invent the rest. Refusing here also saves the call."""
    llm = _LLM()
    got = await relevance.judge(llm, _post(caption="mantap 🔥"),
                                about="courses", lang_name="Indonesian")

    assert not got.relevant
    assert llm.calls == []


@pytest.mark.asyncio
async def test_a_broker_failure_is_a_no_not_a_yes() -> None:
    class _Broken:
        async def chat(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("no provider")

    got = await relevance.judge(_Broken(), _post(), about="courses", lang_name="Indonesian")

    assert not got.relevant


# --------------------------------------------------------------------------- the draft


@pytest.mark.parametrize(("draft", "why"), [
    ("Keren! Ikut kelas kami di stepan.com ya", "contains a link"),
    ("Bagus banget! cek @itstep_jakarta", "contains a mention or hashtag"),
    ("Mantap! Kelas Blender kami cuma Rp 2.500.000 bulan ini", "reads as a sales pitch"),
    ("", "empty draft"),
])
def test_a_comment_that_advertises_is_not_posted(draft: str, why: str) -> None:
    """Under our own post a factual line is at least on topic. Under a stranger's it is an
    advert nobody asked for, and there is no fallback worth posting instead — so these are
    dropped, not rewritten."""
    assert compose.rejected(draft, lang="id") == why


def test_a_comment_naming_the_brand_is_not_posted() -> None:
    """The one word the model reaches for when it has run out of anything specific to say."""
    assert compose.rejected("Keren! Salam dari Jakarta", lang="id",
                            brand_terms=("Jakarta",)) == "names the brand"


def test_an_ordinary_human_line_passes() -> None:
    assert compose.rejected("Tiga minggu belajar Blender dan hasilnya sebersih ini, salut",
                            lang="id", brand_terms=("Jakarta",)) is None


# --------------------------------------------------------------------------- the engine


@pytest.mark.asyncio
async def test_an_approved_post_gets_a_comment(db_session) -> None:  # noqa: ANN001
    bid, ch, _lead = await _seed(db_session)
    port = _Port([_post()])
    llm = _LLM()

    posted = await _svc(db_session, bid, llm).run(ch, port)

    assert posted == 1
    assert port.posted == [("m1", "keren banget hasil renderingnya")]
    assert llm.calls == ["chat:fast", "chat:smart"]  # cheap judge first, smart writer after


@pytest.mark.asyncio
async def test_the_rejection_is_recorded_not_just_dropped(db_session) -> None:  # noqa: ANN001
    """The threshold on the judge is the only real knob this mission has. A table holding only
    what we sent cannot tell anyone whether it is too strict or too loose."""
    bid, ch, _lead = await _seed(db_session)
    llm = _LLM(verdict=json.dumps({"relevant": False, "reason": "someone's funeral"}))
    port = _Port([_post()])

    posted = await _svc(db_session, bid, llm).run(ch, port)
    row = (await db_session.execute(_all_rows())).scalars().first()

    assert posted == 0 and port.posted == []
    assert row.status == "skipped" and row.relevant is False
    assert row.skip_reason == "someone's funeral"
    assert row.media_caption  # what the judge saw, so a wrong call can be re-read later


@pytest.mark.asyncio
async def test_a_post_already_judged_is_not_judged_again(db_session) -> None:  # noqa: ANN001
    """Re-judging bills the same verdict every hour, and a judge that says yes on the third
    try turns a considered no into a coin flip."""
    bid, ch, _lead = await _seed(db_session)
    llm = _LLM()
    await _svc(db_session, bid, llm).run(ch, _Port([_post()]))

    second = _Port([_post()])
    posted = await _svc(db_session, bid, _LLM()).run(ch, second)

    assert posted == 0 and second.posted == []


@pytest.mark.asyncio
async def test_a_stale_post_is_left_alone(db_session) -> None:  # noqa: ANN001
    """A comment on last month's photo is not attentiveness, it is an account working through
    a list — and it reads exactly that way to the person who gets it."""
    bid, ch, _lead = await _seed(db_session)
    port = _Port([_post(days_old=40)])

    posted = await _svc(db_session, bid, _LLM()).run(ch, port)

    assert posted == 0 and port.posted == []


@pytest.mark.asyncio
async def test_the_same_person_is_not_visited_twice_in_a_month(db_session) -> None:  # noqa: ANN001
    """Appearing under three of somebody's posts in a week is following them around."""
    bid, ch, _lead = await _seed(db_session)
    await _svc(db_session, bid, _LLM()).run(ch, _Port([_post()]))

    later = _Port([_post(media_id="m2")])
    posted = await _svc(db_session, bid, _LLM()).run(ch, later)

    assert posted == 0 and later.posted == []


@pytest.mark.asyncio
async def test_a_failed_send_is_recorded_and_not_counted(db_session) -> None:  # noqa: ANN001
    bid, ch, _lead = await _seed(db_session)
    port = _Port([_post()], ok=False)

    posted = await _svc(db_session, bid, _LLM()).run(ch, port)
    row = (await db_session.execute(_all_rows())).scalars().first()

    assert posted == 0
    assert row.status == "error" and "feedback_required" in (row.skip_reason or "")


@pytest.mark.asyncio
async def test_the_daily_cap_stops_the_mission(db_session) -> None:  # noqa: ANN001
    bid, ch, lead = await _seed(db_session)
    for i in range(_Settings.proactive_comment_daily_cap):
        db_session.add(OutboundComment(
            branch_id=bid, channel_id=ch.id, lead_id=lead.id, media_id=f"old{i}",
            author_pk="999", status="sent", handled_at=_NOW - timedelta(hours=1)))
    await db_session.flush()
    port = _Port([_post()])

    posted = await _svc(db_session, bid, _LLM()).run(ch, port)

    assert posted == 0 and port.posted == []


@pytest.mark.asyncio
async def test_a_blocked_lead_is_never_a_candidate(db_session) -> None:  # noqa: ANN001
    bid, ch, lead = await _seed(db_session)
    lead.is_blocked = True
    db_session.add(lead)
    await db_session.flush()

    got = await OutboundRepo(db_session, bid).candidates(ch.id, 25, quiet_days=30)

    assert got == []


@pytest.mark.asyncio
async def test_another_branch_s_leads_are_never_candidates(db_session) -> None:  # noqa: ANN001
    """Tenant isolation, asserted rather than assumed — and here a leak would mean writing
    under the posts of somebody else's customers."""
    bid, ch, _lead = await _seed(db_session)
    other_bid, _other_ch, _other_lead = await _seed(db_session, ig_user_id="888")

    got = await OutboundRepo(db_session, bid).candidates(ch.id, 25, quiet_days=30)

    assert other_bid != bid
    assert [x.ig_user_id for x in got] == ["777"]


# --------------------------------------------------------------------------- the switches


def test_the_mission_does_not_run_without_a_line_describing_who_we_are() -> None:
    """The judge measures a post against that line. Empty, it has no standard at all and would
    wave through anything that merely looked cheerful."""
    ch = Channel(branch_id=1, kind=ChannelKind.INSTAGRAM, handle="x")

    assert not runs_on(ch, _Settings(), "   ")
    assert runs_on(ch, _Settings(), "coding courses in Jakarta")


def test_the_mission_does_not_run_on_a_connector_that_cannot_write_first() -> None:
    """The website has no feed to comment under. That falls out of the connector declaration,
    not out of a branch-id check."""
    ch = Channel(branch_id=1, kind=ChannelKind.WEBSITE, handle="site")

    assert not runs_on(ch, _Settings(), "coding courses in Jakarta")


def test_the_switch_off_stops_it() -> None:
    class _Off(_Settings):
        proactive_comments_enabled = False

    ch = Channel(branch_id=1, kind=ChannelKind.INSTAGRAM, handle="x")

    assert not runs_on(ch, _Off(), "coding courses in Jakarta")


def _all_rows():  # noqa: ANN202
    from sqlalchemy import select

    return select(OutboundComment)
