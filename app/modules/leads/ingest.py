"""IngestService — turn InboundMessages into leads, threads and deduped Messages.

The single write path for inbound traffic: resolve identity, dedup by external id,
persist the message, advance the thread's reply window. Branch-scoped throughout.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel, Lead, MediaAsset, Message, StageEvent
from app.domain.enums import HUMAN_LED_STAGES, Stage
from app.domain.funnel import MANAGER_PHONE_ACTOR, apply_stage
from app.domain.phone import extract_phone
from app.modules.ads import AdMappingService
from app.modules.conversation.signals import is_auto_reply
from app.modules.notifications.alerts import AlertService
from app.ports.channel import InboundMessage
from app.ports.notify import NotifierPort

from .consolidate import merge_by_phone
from .identity import IdentityService
from .repository import MessageRepo

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)  # private-channel reply window (e.g. MBS 24h)
# Our own send recorded at send-time vs the same message polled back with IG's own
# timestamp can drift by a few seconds plus poll latency; a lead/manager never re-sends
# the identical full text within minutes, so this wide window drops only the echo.
_OUT_ECHO_WINDOW = timedelta(minutes=5)


class IngestService:
    """Inbound ingestion for one branch — idempotent on (channel_id, external_id)."""

    def __init__(
        self, session: AsyncSession, branch_id: int, notifier: NotifierPort | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.identity = IdentityService(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self._notifier = notifier

    async def ingest(
        self, channel_id: int, messages: list[InboundMessage]
    ) -> list[Message]:
        """Persist each new inbound; skip duplicates. Returns the rows it created."""
        from app.modules.settings.service import get_channel_settings  # noqa: PLC0415
        created: list[Message] = []
        # Phone country code is per-connector — a lead's local number is parsed with THIS
        # channel's region (a Malaysia IG account vs an Indonesia one stamp different codes).
        cc = (await get_channel_settings(
            self.session, self.branch_id, channel_id)).phone_country_code
        # Read once per batch, not per message: it decides whether a lead first seen here
        # belongs in the funnel at all.
        channel = await self.session.get(Channel, channel_id)
        manager_phone = bool(channel is not None and channel.manager_phone)
        await self._advance_read_receipts(channel_id, messages)
        await self._refresh_identity(channel_id, messages)
        for inbound in messages:
            external_id = inbound.external_id or _external_id(inbound)
            if await self.messages.by_external(channel_id, external_id) is not None:
                continue  # already ingested — idempotent (incl. rows OutboxSender recorded)
            # Two older shapes of the same identity. Without these a re-poll would store a
            # second copy of every message ingested before each change.
            if await self.messages.by_external(
                channel_id, _legacy_external_id(inbound)
            ) is not None:
                continue
            if inbound.external_id and await self.messages.by_external(
                channel_id, _external_id(inbound)
            ) is not None:
                continue  # legacy row stored under the synthetic id — don't duplicate
            if inbound.direction == "out":
                row = await self._store_outgoing(channel_id, external_id, inbound)
                if row is not None:
                    created.append(row)
                continue
            # The channel's own number wins over one scraped from the text: on WhatsApp the
            # address IS the lead's phone, while extract_phone can only ever find a number
            # someone typed — which may well be a friend's, or the school's own.
            phone = inbound.lead_phone or extract_phone(inbound.text, cc)
            lead, thread, thread_created = await self.identity.resolve_or_create(
                inbound.external_thread_id, channel_id,
                display_name=inbound.sender_name,
                phone=phone,
                ig_user_id=inbound.lead_ig_user_id or inbound.sender_id,
                ig_username=inbound.sender_username,
                avatar_url=inbound.sender_avatar,
                first_seen=inbound.occurred_at,
            )
            if manager_phone and thread_created:
                await self._claim_for_manager(lead, thread)
            self.session.add(lead)
            row = await self._store(lead, thread, channel_id, external_id, inbound)
            if row is not None:
                created.append(row)
        return created

    async def _claim_for_manager(self, lead, thread) -> None:  # noqa: ANN001
        """This connector is a manager's own phone. Their first message there means a human
        already has this person — so the lead moves to MANAGER whatever stage they were in,
        and the bot goes quiet.

        On the FIRST message of that connector, not every one: once a manager decides to hand
        the thread back (any funnel stage re-arms the bot, see domain.funnel.apply_stage), a
        later message on the same connector must not silently undo that decision — and that
        decision is the ONLY thing keeping the bot quiet here, now that the channel no longer
        refuses sends of its own.

        Not a flag alongside the stage: the stage IS the answer, it is already outside every
        funnel-stage list (counters, follow-ups, reactivation, the reply queue), and a manager
        can move it. A parallel boolean could only ever disagree with it."""
        if lead.stage == Stage.MANAGER:
            return
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
            from_stage=str(lead.stage), to_stage=str(Stage.MANAGER),
            actor=MANAGER_PHONE_ACTOR,
            reason="manager's own phone: a human owns this conversation",
        ))
        apply_stage(lead, Stage.MANAGER)
        logger.info("branch=%d lead=%s → manager (manager phone, thread=%s)",
                    self.branch_id, lead.id, thread.id)

    async def _we_composed_this(
        self, thread_id: int, body: str, at: datetime,
    ) -> bool:
        """Did WE write this line, whatever the outbox row's fate?

        Matched on the exact text within the echo window: a manager typing by hand produces
        novel wording, and the bot never composes the same line twice in five minutes."""
        clean = (body or "").strip()
        if not clean:
            return False
        row = (await self.session.execute(
            text("SELECT 1 FROM outbox WHERE thread_id = :t AND text = :x"
                 " AND scheduled_at BETWEEN :lo AND :hi LIMIT 1"),
            {"t": thread_id, "x": clean,
             "lo": at - _OUT_ECHO_WINDOW, "hi": at + _OUT_ECHO_WINDOW},
        )).first()
        return row is not None

    async def _refresh_identity(
        self, channel_id: int, messages: list[InboundMessage]
    ) -> None:
        """Backfill name/phone/avatar on KNOWN threads, before dedup drops their messages.

        Identity used to be resolved only while storing a new row, so a thread ingested
        before the channel could report a name stayed anonymous forever — the next poll
        carried the name, dedup dropped the message that carried it, and nothing looked at
        it again. Live: thirteen WhatsApp threads, zero names, one phone.

        Same shape as the read-receipt pass above and for the same reason: a fact that rides
        on EVERY polled item must not be read only on the items that happen to be new."""
        found: dict[str, tuple[str | None, str | None, str | None]] = {}
        for m in messages:
            phone, name, avatar = found.get(m.external_thread_id, (None, None, None))
            # The phone is a property of the CHAT PARTNER, so it counts from either side:
            # WhatsApp attaches the real address to only some items (15 of 50 on a live
            # page), and on a manager's number most of those are the manager's own. Taking
            # it only from inbound items threw away most of what was on offer.
            phone = phone or m.lead_phone
            # The NAME cannot be read that way. On our own items pushName is the school's,
            # and writing it onto leads would rename half the base to "Academy It Step".
            if m.direction != "out":
                name, avatar = name or m.sender_name, avatar or m.sender_avatar
            found[m.external_thread_id] = (phone, name, avatar)

        for ext_id, (phone, name, avatar) in found.items():
            if not (phone or name or avatar):
                continue
            thread = await self.identity.threads.by_external(channel_id, ext_id)
            if thread is None:
                continue  # brand-new: the storing path resolves it with everything at once
            lead = await self.session.get(Lead, thread.lead_id)
            if lead is None:
                continue
            knew_phone = lead.phone_e164
            self.identity.backfill(lead, phone, name, None, None, avatar)
            self.session.add(lead)
            # A phone that has only just become known is the moment two records can be
            # recognised as one person — Stepan's thread and the manager's chat. Merging is
            # retroactive by nature: the number arrives mid-conversation, long after both
            # records exist.
            if lead.phone_e164 and lead.phone_e164 != knew_phone:
                await merge_by_phone(self.session, lead)

    async def _advance_read_receipts(
        self, channel_id: int, messages: list[InboundMessage]
    ) -> None:
        """Advance each known thread's lead_seen_at BEFORE message dedup.

        The receipt rides on every polled item, but the old update lived in _store and
        only ran when a NEW inbound row was written — a lead who READS our replies
        without answering produces no new rows, so their receipt froze at their last
        message (live: thread 452 showed 'read' in the IG app, nothing in our UI).
        Threads not yet in the DB are covered by _store after identity resolution."""
        latest: dict[str, datetime] = {}
        for m in messages:
            if m.lead_seen_at is None:
                continue
            prev = latest.get(m.external_thread_id)
            if prev is None or m.lead_seen_at > prev:
                latest[m.external_thread_id] = m.lead_seen_at
        for ext_id, seen in latest.items():
            thread = await self.identity.threads.by_external(channel_id, ext_id)
            if thread is not None and (
                thread.lead_seen_at is None or seen > thread.lead_seen_at
            ):
                thread.lead_seen_at = seen
                self.session.add(thread)

    async def _store_outgoing(
        self, channel_id: int, external_id: str, inbound: InboundMessage
    ) -> Message | None:
        """Record OUR message seen in the channel (manual reply from the IG app).

        Moves last_out_at so the bot never answers over a human. Skipped when the
        thread is unknown (inbound-only business — we never open conversations)."""
        thread = await self.identity.threads.by_external(
            channel_id, inbound.external_thread_id
        )
        if thread is None:
            return None
        # OutboxSender already recorded every message the bot/manager sent through our
        # queue, tagging it with the send-API's item id. The inbox poll re-surfaces that
        # same message under a DIFFERENT item id, so external-id dedup misses it and we'd
        # store our own send twice (one bubble showed up 2x in the chat + the LLM context).
        # A genuine manual reply typed in the IG app carries novel text, so a wide content
        # window here drops only the poll-back echo, never a real human message. Media items
        # carry the same placeholder text ('🖼 media') regardless of which photo/video it is,
        # so skip this content dedup for them — same guard as _store's lead-side path,
        # otherwise a manager sending two different photos back-to-back would drop the second.
        if inbound.media_url is None and await self.messages.duplicate_by_content(
            thread.id, "out", inbound.text, inbound.occurred_at, window=_OUT_ECHO_WINDOW
        ):
            return None
        # Whose message is this REALLY? The poll cannot tell — every outgoing item looks the
        # same from Instagram's side — so "not one of ours" was inferred from our own stored
        # sends. That inference fails exactly when the bookkeeping did: outbox row 21101 was
        # generated by the bot, reached Instagram, and ended as `canceled`, so no stored send
        # existed to echo-match and Stepan's own answer about the price was filed under the
        # manager (thread 6074, 07.08).
        #
        # The outbox knows better than the message table here: it holds what we composed,
        # whatever became of the row afterwards.
        sent_by = "agent" if await self._we_composed_this(
            thread.id, inbound.text, inbound.occurred_at) else "manager"
        msg = await self.messages.add(
            Message(
                branch_id=self.branch_id,
                thread_id=thread.id,
                channel_id=channel_id,
                external_id=external_id,
                direction="out",
                sent_by=sent_by,
                text=inbound.text,
                occurred_at=inbound.occurred_at,
            )
        )
        if inbound.media_url:
            # Manager-sent media (photo/video from the IG app) — same stub-and-backfill path
            # as lead-sent media (see _store below); ingest can't download inline, the
            # backfill worker fills the bytes later. Previously only the lead-side branch
            # did this, so a manager's own media rendered as a bare '🖼 media' placeholder
            # forever instead of the actual image/video.
            msg.media_pending = True
            self.session.add(MediaAsset(
                branch_id=self.branch_id, message_id=msg.id,
                kind=inbound.media_kind or "image", url=inbound.media_url,
            ))
        thread.msg_out += 1  # counted here, not recounted by every inbox poll
        if thread.last_out_at is None or inbound.occurred_at > thread.last_out_at:
            thread.last_out_at = inbound.occurred_at
        # Recorded and visible in the chat log, but NOT an automatic hand-off to MANAGER stage
        # or an agent_enabled flip. This used to pause the bot on any sent_by="manager" outbound
        # (thread 1761: a manager corrected the bot in the IG app, and it re-pitched over them
        # a minute later) — but "sent_by=manager" only means "an outbound we didn't send
        # ourselves", and IG attributes more than human replies that way: thread 4151, a phone
        # number the BOT itself texted got auto-turned into an IG "share contact" card, which
        # came back through the inbox poll as an unattributed outbound and silently muted the
        # bot mid-conversation. A manager can still step in — that's what this message being
        # in the log is for — but only muting from the UI (Bot OFF) is a deliberate pause now.
        return msg

    async def _store(
        self, lead, thread, channel_id: int, external_id: str, inbound: InboundMessage
    ) -> Message | None:
        # A photo/share reaches us twice, described two incompatible ways: the webhook as
        # '🖼 media' + a MediaAsset (or '🔗 …' + a link_url), the Graph poll as an empty
        # `message` with no attachment. duplicate_by_content compares '' against '🖼 media',
        # finds no match, and writes a SECOND, blank inbound — which re-opens the 24h window,
        # resets the follow-up cycle and enters the model's context as silence. The race has no
        # favourite (the poll fires every 2 min; the webhook job queues behind the worker), so
        # both orders are handled: the blank copy is dropped, the rich copy fills the blank in.
        if _is_contentless(inbound):
            rich = await self.messages.attachment_inbound_at(thread.id, inbound.occurred_at)
            if rich is not None:
                return None
        elif inbound.media_url or inbound.link_url:
            blank = await self.messages.contentless_inbound_at(thread.id, inbound.occurred_at)
            if blank is not None:
                await self._fill_in_blank(blank, external_id, inbound)
                return None  # the row already existed — this call created nothing
        if (
            inbound.media_url is None
            # An empty text is not "the same message" — it is the absence of one, and every
            # blank inbound in a thread matches every other. Two attachments the poll returned
            # a second apart are two messages; matching them dropped the second outright.
            and (inbound.text or "").strip()
            and await self.messages.duplicate_by_content(
                thread.id, "in", inbound.text, inbound.occurred_at
            )
        ):
            return None  # same text already in thread within 2s (pending→main id drift)
        if inbound.media_url is None and await self.messages.echo_of_our_own(
            thread.id, inbound.text, inbound.occurred_at
        ):
            return None  # IG echoed our own outgoing message back as if the lead sent it
        # Structural signal from IG itself (ad_id/ad_media_id/lead_source), not a text guess:
        # The AD PREFILL: the ad's own caption/CTA text, which the lead never typed. It marks
        # exactly ONE message — the first thing that arrives after the tap. Meta's referral
        # metadata (ad_id/ad_media_id/lead_source) is THREAD-level and keeps coming back on
        # every later message, so taking it at face value marked everything the lead
        # subsequently typed as "not their words" (33 live threads on branch 1). That silently
        # disabled the answer gate, told the critic to ignore real questions, and kept the
        # dossier from recording them. The referral only means "this conversation started from
        # an ad" — the prefill is the first inbound and nothing after it.
        has_referral = bool(
            inbound.ad_id or inbound.ad_media_id or inbound.lead_source == "ad_clicktomsg")
        is_ad_referral = has_referral and not await self.messages.has_inbound(thread.id)
        msg = await self.messages.add(
            Message(
                branch_id=self.branch_id,
                thread_id=thread.id,
                channel_id=channel_id,
                external_id=external_id,
                direction="in",
                sent_by="lead",
                text=inbound.text,
                occurred_at=inbound.occurred_at,
                link_url=inbound.link_url,
                preview_url=inbound.preview_url,
                is_ad_referral=is_ad_referral,
            )
        )
        if inbound.media_url:
            # ingest can't download inline; stash a stub the backfill worker fills later
            msg.media_pending = True
            self.session.add(MediaAsset(
                branch_id=self.branch_id, message_id=msg.id,
                kind=inbound.media_kind or "image", url=inbound.media_url,
            ))
        if inbound.lead_seen_at and (
            thread.lead_seen_at is None or inbound.lead_seen_at > thread.lead_seen_at
        ):
            thread.lead_seen_at = inbound.lead_seen_at
        if thread.last_in_at is None or inbound.occurred_at > thread.last_in_at:
            # The 24h IG window really did open — that's a channel fact, true even for a
            # robot. Everything else below treats the inbound as THE LEAD, so an auto-reply
            # must stop here: thread 2503's auto-responder made last_in_at > last_out_at, so
            # the thread read as "lead spoke last" — Stepan answered the robot and the
            # follow-up cycle reset to zero. It's stored as a message either way (history).
            thread.window_until = inbound.occurred_at + WINDOW
            if is_auto_reply(inbound.text or ""):
                logger.info(
                    "ingest: branch=%d thread=%d inbound is the lead's own auto-responder "
                    "— not treating it as the lead speaking", self.branch_id, thread.id)
            else:
                was_off = not lead.agent_enabled  # BEFORE _revive_bot may flip it back on
                thread.last_in_at = inbound.occurred_at
                thread.msg_in += 1
                await self._reset_followup_cycle(thread)
                self._revive_bot(lead, thread)
                # Only ping "Bot is OFF" when the bot STAYS off after the revive attempt —
                # i.e. a human-led (manager/ready/handed_off) or blocked lead the bot won't
                # answer. A dormant lead that just got revived (agent_enabled flipped back
                # ON) WILL be answered this tick, so the old "was_off" ping was a stale,
                # misleading alert ("Bot is OFF" right before the bot replied — thread 2121).
                if was_off and not lead.agent_enabled:
                    await self._notify_bot_off(lead, thread, inbound.text)
        if inbound.product_hint and thread.product_slug is None:
            thread.product_slug = inbound.product_hint
            thread.product_source = "ad"
        if thread.lead_source is None:
            # IG doesn't always tag the referral type even when it DOES send an ad_id (live
            # case, thread 2158) — an ad_id is unambiguous evidence of a click-to-message ad,
            # so fall back to it rather than silently missing the entry-point prompt hint
            # (source_hint) that acknowledges the ad instead of a generic "how can I help".
            if inbound.lead_source:
                thread.lead_source = inbound.lead_source
            elif inbound.ad_id:
                thread.lead_source = "ad_clicktomsg"
        if inbound.ad_id and thread.ad_id is None:
            thread.ad_id = inbound.ad_id
        if inbound.ad_media_id and thread.ad_media_id is None:
            thread.ad_media_id = inbound.ad_media_id
        if inbound.ad_preview_url:
            thread.ad_preview_url = inbound.ad_preview_url  # always refresh (CDN URL)
        if thread.product_slug is None and (thread.ad_id or thread.ad_media_id):
            svc = AdMappingService(self.session, self.branch_id)
            # ad_id first — it is the key the operator actually edits. The creative is the
            # fallback for the threads Instagram sends with a creative and no ad_id at all,
            # which the ad_id-only lookup skipped entirely.
            mapped = await svc.product_for_ad(thread.ad_id)
            if not mapped:
                mapped = await svc.product_for_creative(thread.ad_media_id)
            if mapped:
                thread.product_slug = mapped
                thread.product_source = "ad"
            else:
                # No AdProductMap entry for this ad_id yet (matcher walk pending/incomplete,
                # or a newly launched campaign nobody mapped) — the lead enters the funnel with
                # no product anchor, and the model picks one on its own from conversation
                # content alone (thread 4943, thread 5018: an ad lead got pitched the wrong
                # product / pitched twice with no discovery and escalated to a manager).
                # Waiting for that escalation means Stepan has already fumbled the opener by
                # the time anyone notices — ping ops on the FIRST inbound instead, same turn
                # the gap appears, so the ad can be mapped before Stepan replies at all.
                logger.warning(
                    "ingest: branch=%d thread=%d ad_id=%s media=%s has no AdProductMap "
                    "entry — product will be inferred by the model with no ad anchor",
                    self.branch_id, thread.id, thread.ad_id, thread.ad_media_id)
                if is_ad_referral:  # once per thread — not on every later inbound while unmapped
                    await self._notify_unmapped_ad(lead, thread)
        return msg

    async def _fill_in_blank(
        self, row: Message, external_id: str, inbound: InboundMessage
    ) -> None:
        """Give the poll's blank row the webhook's description of the same attachment.

        The external_id moves to the webhook's mid as well: without it a Meta redelivery would
        find the row no longer blank, skip the guard above and store the photo a second time.
        The poll's own re-read is still recognised — the row now carries an attachment, which is
        exactly what attachment_inbound_at looks for. Nothing else in _store is replayed: the
        blank row already advanced the window, last_in_at and the follow-up cycle to this same
        instant when it was written."""
        row.text = inbound.text
        row.external_id = external_id
        row.link_url = inbound.link_url or row.link_url
        row.preview_url = inbound.preview_url or row.preview_url
        if inbound.media_url:
            row.media_pending = True
            self.session.add(MediaAsset(
                branch_id=self.branch_id, message_id=row.id,
                kind=inbound.media_kind or "image", url=inbound.media_url,
            ))
        self.session.add(row)
        await self.session.flush()

    async def _reset_followup_cycle(self, thread) -> None:
        """Fresh inbound restarts the follow-up cycle and cancels a queued nudge."""
        thread.followups_sent = 0
        thread.next_followup_at = None
        await self.session.execute(
            text(
                "UPDATE outbox SET status='skipped' WHERE thread_id=:tid"
                " AND status='pending' AND source='followup'"
            ),
            {"tid": thread.id},
        )

    def _revive_bot(self, lead, thread) -> None:
        """Fresh inbound re-enables the bot — except when a human leads the stage, or a human
        switched the bot off by hand.

        Dormant leads wake up into qualifying (S1 semantics) with a journal entry.

        agent_off_manual is the difference between the two ways the bot ends up off. The system
        parks a lead (undeliverable, follow-ups exhausted) and that mute SHOULD lift when they
        write again. A person pressing Bot OFF in the chat has decided to take the thread, and
        that decision has to outlive the lead's next message — it used to be reversed silently,
        with nothing anywhere recording that a human had intervened at all."""
        if lead.is_blocked or lead.agent_off_manual or lead.stage in HUMAN_LED_STAGES:
            return
        if lead.stage == Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.QUALIFYING),
                actor="system", reason="lead revived by fresh inbound",
            ))
            lead.stage = Stage.QUALIFYING
            logger.info("branch=%d lead=%d revived dormant → qualifying",
                        self.branch_id, lead.id)
        if not lead.agent_enabled:
            lead.agent_enabled = True
        self.session.add(lead)

    async def _notify_unmapped_ad(self, lead, thread) -> None:
        """This lead clicked an ad Stepan has no AdProductMap entry for — ping ops on the ad's
        FIRST inbound so it can be mapped in the admin Ads panel before the model has to guess
        a product on its own. Best-effort: never blocks/fails ingestion."""
        if self._notifier is None:
            return
        try:
            await AlertService(self.session, self.branch_id, self._notifier).raise_alert(
                lead_id=lead.id, kind="unmapped_ad", thread_id=thread.id,
                lead_phone=lead.phone_e164,
                summary_en=f"New lead from ad_id={thread.ad_id} with no product mapping — "
                           "map it in Ads before Stepan has to guess the product",
                summary_ru=f"Новый лид с ad_id={thread.ad_id} без привязки продукта — "
                           "смапь в разделе Ads, пока Степан не начал угадывать продукт",
            )
        except Exception:
            logger.warning("unmapped-ad alert failed lead=%d thread=%d",
                            lead.id, thread.id, exc_info=True)

    async def _notify_bot_off(self, lead, thread, text: str) -> None:
        """The bot was silent (manually toggled off, or a human-led stage) when this
        inbound arrived, so nothing else will tell a manager the lead just wrote something —
        ping Telegram directly. Best-effort: never blocks/fails ingestion (thread 2274)."""
        if self._notifier is None:
            return
        snippet = (text or "").strip()[:200]
        try:
            # With an LLM: without one the alert body degrades to no summary AND no
            # translation, which is exactly the case a human is being woken up for.
            await AlertService(
                self.session, self.branch_id, self._notifier, llm=_alert_llm()
            ).raise_alert(
                lead_id=lead.id, kind="bot_off_message", thread_id=thread.id,
                lead_phone=lead.phone_e164,
                # The message itself is NOT pasted into the reason. It is already quoted
                # above the reason in both halves of the card — and pasted here it went into
                # the Russian half untranslated, so the one line the manager needed to read
                # was the one line they could not.
                summary_en="Bot is OFF — the lead wrote" if snippet
                else "Bot is OFF — lead wrote (no text/media)",
                summary_ru="Бот выключен — лид написал" if snippet
                else "Бот выключен — лид написал (без текста/медиа)",
            )
        except Exception:
            logger.warning("bot-off alert failed lead=%s", lead.id, exc_info=True)


def _alert_llm():  # noqa: ANN201 — LLMPort, imported lazily to keep the adapter out of ingest
    """The broker, for translating an alert. None when it cannot be built.

    Lazy and forgiving on purpose: an alert is best-effort, and a missing translation must
    cost the manager a translation — never the ping itself."""
    try:
        from app.adapters.llm.broker import BrokerLLM  # noqa: PLC0415

        return BrokerLLM()
    except Exception:  # noqa: BLE001
        logger.warning("alert LLM unavailable — sending without a translation")
        return None


def _is_contentless(inbound: InboundMessage) -> bool:
    """No text and no media — a row nobody, human or model, can read anything from.

    Branch 1 (instagrapi) cannot produce one: ig_parse.item_content always substitutes a
    placeholder, falling back to '[{item_type}]'. Only the Graph poll does, and only for a
    message whose content Graph does not put in `message` — i.e. exactly an attachment."""
    return inbound.media_url is None and not (inbound.text or "").strip()


def _legacy_external_id(inbound: InboundMessage) -> str:
    """The pre-2026-08-03 shape, kept ONLY so rows already stored under it are still
    recognised and never ingested a second time."""
    return f"{inbound.external_thread_id}:{inbound.occurred_at.isoformat()}:{inbound.sender_id}"


def _external_id(inbound: InboundMessage) -> str:
    """Stable per-message id — InboundMessage carries no native id, so derive one.

    The text is part of it because timestamps are second-resolution: someone typing two quick
    lines produces two messages with one timestamp, and an id of thread+time+sender made them
    collide, so the second was discarded as "already ingested". A hash rather than the text
    itself keeps the column short and free of message content.
    """
    digest = hashlib.sha256((inbound.text or "").encode()).hexdigest()[:12]
    return (f"{inbound.external_thread_id}:{inbound.occurred_at.isoformat()}"
            f":{inbound.sender_id}:{digest}")
