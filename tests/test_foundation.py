"""Фундамент: изоляция по branch_id, crypto round-trip, доменные enum'ы."""
from sqlmodel import select

from app.adapters import crypto
from app.adapters.db.models import Branch, Lead
from app.domain.enums import BOT_SILENT_STAGES, ChannelKind, Role, Stage


async def test_branch_isolation(db_session):
    s = db_session
    b1, b2 = Branch(name="Indonesia"), Branch(name="Vietnam")
    s.add(b1)
    s.add(b2)
    await s.flush()
    s.add(Lead(branch_id=b1.id, display_name="A"))
    s.add(Lead(branch_id=b2.id, display_name="B"))
    await s.flush()
    rows = (await s.exec(select(Lead).where(Lead.branch_id == b1.id))).all()
    assert [r.display_name for r in rows] == ["A"]   # филиал 1 не видит лида филиала 2


async def test_no_default_branch():
    # У филиала id присваивается БД; «дефолтного» филиала в схеме нет.
    assert Branch(name="X").id is None


def test_crypto_roundtrip():
    enc = crypto.encrypt("session-secret")
    assert enc != "session-secret"
    assert crypto.decrypt(enc) == "session-secret"


def test_domain_enums():
    assert ChannelKind.WHATSAPP == "whatsapp"
    assert Role.SUPER_ADMIN == "super_admin"
    assert Stage.READY in BOT_SILENT_STAGES
    assert Stage.QUALIFYING not in BOT_SILENT_STAGES
