"""Comment engine end-to-end over fakes: ingest dedups, triage routes, a grounded reply
posts publicly, an ungrounded draft degrades to a DM invite, and the caps hold."""
from __future__ import annotations

from datetime import datetime

from app.adapters.db.models import Branch, Channel, KnowledgeDoc
from app.domain.enums import ChannelKind
from app.modules.comments.repository import CommentRepo
from app.modules.comments.service import CommentService
from app.modules.settings.service import BranchSettings
from app.ports.channel import InboundComment, SendResult


class _FakePort:
    def __init__(self, comments: list[InboundComment]) -> None:
        self._comments = comments
        self.posted: list[tuple[str, str]] = []
        self.hidden: list[str] = []

    async def fetch_comments(self, *, since=None):  # noqa: ANN001, ANN002
        return self._comments

    async def reply_to_comment(self, comment_external_id: str, text: str) -> SendResult:
        self.posted.append((comment_external_id, text))
        return SendResult(ok=True, external_message_id="reply1")

    async def hide_comment(self, comment_external_id: str) -> SendResult:
        self.hidden.append(comment_external_id)
        return SendResult(ok=True)


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def chat(self, messages, *, workflow=None, **kw):  # noqa: ANN001, ANN003
        if workflow == "guard":  # the fabrication verify — this fake's drafts are all clean
            return "CLEAN", {"model": "x", "cost_usd": 0.0}
        return self._reply, {"model": "x", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003
        return [[0.0] for _ in texts]


async def _seed(s) -> tuple[int, Channel]:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
                       content="SMM Intensive total Rp 1.882.955, DP Rp 500.000."))
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="itstep")
    s.add(ch)
    await s.flush()
    return b.id, ch


def _cfg(**over) -> BranchSettings:
    base = dict(
        agent_enabled=True, hourly_cap=0, daily_cap=0, quiet_start=0, quiet_end=0,
        reply_delay_min_s=0, reply_delay_max_s=0, tz_offset_h=7, tg_group_id="",
        followup_enabled=False, followup_schedule_h=[], daily_budget_usd=0.0,
        crm_enabled=False,
        crm_webhook_url="", meta_pixel_id="", meta_capi_token="",
        comment_replies_enabled=True, comment_hourly_cap=20, comment_per_post_cap=5,
    )
    base.update(over)
    return BranchSettings(**base)


def _comment(cid: str, text: str, media: str = "m1") -> InboundComment:
    return InboundComment(external_id=cid, media_id=media, text=text,
                          occurred_at=datetime(2026, 7, 19, 10, 0), author_username="u")


async def test_ingest_stores_and_dedups(db_session) -> None:
    bid, ch = await _seed(db_session)
    port = _FakePort([_comment("c1", "berapa harganya?"), _comment("c1", "dup")])
    svc = CommentService(db_session, bid, _FakeLLM("x"), _kb(db_session, bid), _cfg())
    n = await svc.ingest(ch, port)
    assert n == 1  # the duplicate c1 is skipped
    again = await svc.ingest(ch, port)
    assert again == 0  # already stored


async def test_question_gets_grounded_public_reply(db_session) -> None:
    bid, ch = await _seed(db_session)
    port = _FakePort([_comment("c1", "berapa harganya kak?")])
    reply = "Investasinya Rp 1.882.955, DP Rp 500.000 ya Kak 😊"
    svc = CommentService(db_session, bid, _FakeLLM(reply), _kb(db_session, bid), _cfg())
    await svc.ingest(ch, port)
    posted = await svc.process(ch, port)
    assert posted == 1
    assert port.posted[0][0] == "m1:c1"  # composite media:comment target
    assert "1.882.955" in port.posted[0][1]


async def test_invented_price_degrades_to_dm_invite(db_session) -> None:
    bid, ch = await _seed(db_session)
    port = _FakePort([_comment("c1", "berapa harganya kak?")])
    # A price NOT in the KB context → guard flags it → we must not publish the number.
    svc = CommentService(db_session, bid, _FakeLLM("Cuma Rp 99.000 kak!"),
                         _kb(db_session, bid), _cfg())
    await svc.ingest(ch, port)
    await svc.process(ch, port)
    assert "99.000" not in port.posted[0][1]
    assert "DM" in port.posted[0][1]


async def test_spam_is_deleted_in_ig_not_just_flagged(db_session) -> None:
    bid, ch = await _seed(db_session)
    port = _FakePort([_comment("c1", "follow akun aku ya promo slot gacor")])
    svc = CommentService(db_session, bid, _FakeLLM("x"), _kb(db_session, bid), _cfg())
    await svc.ingest(ch, port)
    posted = await svc.process(ch, port)
    assert posted == 0 and not port.posted
    assert port.hidden == ["m1:c1"]  # actually deleted in IG, not just a DB flag
    row = (await CommentRepo(db_session, bid).pending(ch.id, 10))
    assert not row  # resolved to hidden, nothing left pending


async def test_per_post_cap_holds(db_session) -> None:
    bid, ch = await _seed(db_session)
    comments = [_comment(f"c{i}", "berapa harganya kak?") for i in range(4)]
    port = _FakePort(comments)
    svc = CommentService(db_session, bid, _FakeLLM("Rp 1.882.955 ya Kak"),
                         _kb(db_session, bid), _cfg(comment_per_post_cap=2))
    await svc.ingest(ch, port)
    posted = await svc.process(ch, port)
    assert posted == 2  # capped at 2 replies under one post


def _kb(session, branch_id: int):  # noqa: ANN001, ANN201
    from app.modules.knowledge.service import KnowledgeService
    return KnowledgeService(session, branch_id, _FakeLLM("x"))


class _BlankThenReal:
    """chat:fast returns empty (the live gpt-oss-120b behaviour), chat:smart answers."""
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, messages, *, capability="chat:fast", **kw):  # noqa: ANN001, ANN003
        self.calls.append(capability)
        if capability == "chat:fast":
            return "", {"model": "fast"}
        return "Kelasnya offline di Menara Sudirman atau online ya Kak 😊", {"model": "smart"}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003
        return [[0.0] for _ in texts]


async def test_empty_fast_retries_on_smart(db_session) -> None:
    bid, ch = await _seed(db_session)
    port = _FakePort([_comment("c1", "kelasnya online atau offline?")])
    llm = _BlankThenReal()
    svc = CommentService(db_session, bid, llm, _kb(db_session, bid), _cfg())
    await svc.ingest(ch, port)
    posted = await svc.process(ch, port)
    assert posted == 1
    assert llm.calls == ["chat:fast", "chat:smart"]  # retried after the blank
    assert "Menara Sudirman" in port.posted[0][1]  # the smart answer shipped, not an invite


class _VerifyingLLM:
    """Draft states a WRONG price (real number, wrong course); the verify pass (workflow=guard)
    flags it; the regen fixes it; the re-verify is clean. Mirrors the DM guard cycle."""
    def __init__(self) -> None:
        self.drafts = 0
        self.verifies = 0

    async def chat(self, messages, *, workflow=None, **kw):  # noqa: ANN001, ANN003
        if workflow == "guard":  # verify_grounding
            self.verifies += 1
            body = messages[-1]["content"]
            # flag only the first draft (the one quoting the wrong 99jt), clean after regen
            return ("CLEAN" if "1.882.955" in body else "harga 99.000.000 tidak sesuai KB"), \
                   {"model": "smart"}
        self.drafts += 1
        if self.drafts == 1:
            return "SMM Intensive harganya Rp 99.000.000 ya Kak", {"model": "fast"}
        return "SMM Intensive harganya Rp 1.882.955 ya Kak", {"model": "smart"}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003
        return [[0.0] for _ in texts]


async def test_verify_catches_wrong_price_and_regen_fixes_it(db_session) -> None:
    bid, ch = await _seed(db_session)
    port = _FakePort([_comment("c1", "berapa harga SMM?")])
    llm = _VerifyingLLM()
    svc = CommentService(db_session, bid, llm, _kb(db_session, bid), _cfg())
    await svc.ingest(ch, port)
    posted = await svc.process(ch, port)
    assert posted == 1
    assert llm.verifies >= 1                      # the LLM fabrication verify ran
    assert "1.882.955" in port.posted[0][1]       # the corrected price shipped
    assert "99.000.000" not in port.posted[0][1]  # the wrong one never went public
