"""ChannelService.purge — FK-safe cascade + orphan-only lead removal + tenant guard.

The tricky invariant: a lead merged by phone across two channels must SURVIVE deletion
of one channel (it still has a thread on the other); a lead that lived only on the
deleted channel must be removed together with its alerts/stage-events."""
from __future__ import annotations

from sqlmodel import select

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelSession,
    ChannelThread,
    CrmLeadState,
    Lead,
    LeadNeedTag,
    ManagerAlert,
    MediaAsset,
    Message,
    NeedEntity,
    NeedLeadState,
    Outbox,
    StageEvent,
)
from app.modules.channels.service import ChannelService


async def _world(s):
    b = Branch(name="M", lang="ms")
    s.add(b)
    await s.flush()
    ig = Channel(branch_id=b.id, kind="instagram")
    wa = Channel(branch_id=b.id, kind="whatsapp")
    s.add(ig)
    s.add(wa)
    await s.flush()
    s.add(ChannelSession(channel_id=ig.id, secret_enc="enc", status="active"))  # noqa: S106
    dual = Lead(branch_id=b.id, phone_e164="+60123", display_name="Dual")  # on IG + WA
    only = Lead(branch_id=b.id, display_name="OnlyIG")  # on IG only
    s.add(dual)
    s.add(only)
    await s.flush()
    t_ig1 = ChannelThread(lead_id=dual.id, channel_id=ig.id, external_thread_id="ig1")
    t_wa1 = ChannelThread(lead_id=dual.id, channel_id=wa.id, external_thread_id="wa1")
    t_ig2 = ChannelThread(lead_id=only.id, channel_id=ig.id, external_thread_id="ig2")
    s.add_all([t_ig1, t_wa1, t_ig2])
    await s.flush()
    m_ig = Message(branch_id=b.id, thread_id=t_ig1.id, channel_id=ig.id,
                   external_id="mig", direction="in")
    m_wa = Message(branch_id=b.id, thread_id=t_wa1.id, channel_id=wa.id,
                   external_id="mwa", direction="in")
    s.add(m_ig)
    s.add(m_wa)
    await s.flush()
    s.add(MediaAsset(branch_id=b.id, message_id=m_ig.id, kind="image", data=b"x"))
    s.add(Outbox(branch_id=b.id, thread_id=t_ig1.id, text="hi"))
    s.add(ManagerAlert(branch_id=b.id, lead_id=only.id, thread_id=t_ig2.id, kind="needs_manager"))
    s.add(StageEvent(branch_id=b.id, lead_id=only.id, thread_id=t_ig2.id,
                     from_stage="new", to_stage="qualifying"))
    # The orphan lead also carries CRM + needs-cloud rows (added by later features). These
    # FK-reference lead.id, so the purge must clear them before deleting the lead — otherwise
    # the whole delete aborts on a ForeignKeyViolation (the "can't delete connector" bug).
    ent = NeedEntity(branch_id=b.id, kind="pains", label="дорого")
    s.add(ent)
    await s.flush()
    s.add(LeadNeedTag(lead_id=only.id, kind="pains", entity_id=ent.id, branch_id=b.id))
    s.add(NeedLeadState(lead_id=only.id, branch_id=b.id, needs_sha="abc"))
    s.add(CrmLeadState(branch_id=b.id, lead_id=only.id))
    await s.flush()
    return b, ig, wa, dual, only


async def _all(s, model):
    return list((await s.exec(select(model))).all())


async def test_purge_cascades_and_keeps_multichannel_lead(db_session) -> None:
    b, ig, wa, dual, only = await _world(db_session)

    res = await ChannelService(db_session, b.id).purge(ig.id)
    assert res is not None
    assert (res.threads, res.messages, res.leads) == (2, 1, 1)

    # the IG channel and its session are gone
    assert (await db_session.exec(select(Channel).where(Channel.id == ig.id))).first() is None
    assert await _all(db_session, ChannelSession) == []
    # IG conversation data cascaded away
    assert (await db_session.exec(
        select(Message).where(Message.channel_id == ig.id))).all() == []
    assert await _all(db_session, MediaAsset) == []   # only IG message had media
    assert await _all(db_session, Outbox) == []
    assert await _all(db_session, ManagerAlert) == []
    assert await _all(db_session, StageEvent) == []
    # the phone-merged lead survives (still has the WA thread); the IG-only lead is gone
    assert (await db_session.exec(select(Lead).where(Lead.id == dual.id))).first() is not None
    assert (await db_session.exec(select(Lead).where(Lead.id == only.id))).first() is None
    # …and the orphan lead's CRM + needs-cloud rows went with it (no FK violation aborting)
    assert await _all(db_session, LeadNeedTag) == []
    assert await _all(db_session, NeedLeadState) == []
    assert await _all(db_session, CrmLeadState) == []
    # the WA channel + its thread + message are untouched
    assert (await db_session.exec(select(Channel).where(Channel.id == wa.id))).first() is not None
    assert (await db_session.exec(
        select(ChannelThread).where(ChannelThread.channel_id == wa.id))).all() != []
    assert (await db_session.exec(
        select(Message).where(Message.channel_id == wa.id))).all() != []


async def test_purge_rejects_other_branch(db_session) -> None:
    b, ig, wa, dual, only = await _world(db_session)
    res = await ChannelService(db_session, 99999).purge(ig.id)  # not this tenant's branch
    assert res is None
    # nothing touched
    assert (await db_session.exec(select(Channel).where(Channel.id == ig.id))).first() is not None
    assert len(await _all(db_session, ChannelThread)) == 3
    assert len(await _all(db_session, Lead)) == 2
