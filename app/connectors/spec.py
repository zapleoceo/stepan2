"""What one connector IS, as data.

Adding TikTok or Telegram must be a NEW FILE, not edits across ten. Every question the rest
of the system used to answer with an `if channel.kind ==` chain — how to build the port, what
the connector can do, what it is called, which icon, which panel collects its credentials,
whether its send path has a reply window — is answered from a single ConnectorSpec, and
tests/test_connector_registry.py holds every registered spec to the same contract.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.domain.enums import ChannelKind

if TYPE_CHECKING:
    from app.ports.channel import ChannelPort


class Capability(StrEnum):
    """Powers beyond the ChannelPort baseline, DECLARED by the connector.

    They used to be discovered with hasattr(port, "<method name as a string>") — a spelling
    mistake in that string answered "not supported" instead of failing, and a port could grow
    a method nobody had decided it should have. A capability is now a claim the contract test
    checks against the adapter class."""

    REVOKE = "revoke"
    MARK_SEEN = "mark_seen"
    FETCH_PROFILE = "fetch_profile"
    DOWNLOAD_MEDIA = "download_media"
    COMMENTS = "comments"


# Methods a port must expose to honour each capability. The contract test compares this to
# the adapter class both ways: a claimed capability whose methods are missing fails, and a
# port carrying capability methods it never declared fails too.
CAPABILITY_METHODS: dict[Capability, tuple[str, ...]] = {
    Capability.REVOKE: ("revoke",),
    Capability.MARK_SEEN: ("mark_seen",),
    Capability.FETCH_PROFILE: ("fetch_profile",),
    Capability.DOWNLOAD_MEDIA: ("download_media",),
    Capability.COMMENTS: ("fetch_comments", "reply_to_comment", "hide_comment"),
}

# Every connector answers these three — reading, sending, and whether it is alive.
BASELINE_METHODS: tuple[str, ...] = ("fetch_inbound", "send_text", "session_status")


@dataclass(frozen=True)
class SendWindow:
    """A platform that rejects automated sends some time after the lead's last message.

    The two strings live here with the flag because the gate in OutboxSender is shared: it
    used to be `if kind == META_BUSINESS`, and generalising only the condition would have let
    the next connector write Meta's name into its own outbox rows and into the dormancy reason
    an operator reads. `error_code` is stored in outbox.error and matched by the inbox queries
    and the failed-send bubble, so an existing connector's code can never be re-worded."""

    error_code: str
    dormant_reason: str


# (session, channel) -> a live port. Untyped session/channel: typing them here would drag
# SQLModel into a module the UI imports.
PortBuilder = Callable[[Any, Any], Awaitable["ChannelPort"]]
CredentialPanel = Callable[..., str]


@dataclass(frozen=True)
class ConnectorSpec:
    kind: ChannelKind
    label: str                       # proper noun — shown as-is, not translated
    label_key: str                   # i18n key for the "add channel" kind selector
    icon_class: str                  # Font Awesome classes
    icon_color: str
    adapter: type
    build_port: PortBuilder
    credential_panel: CredentialPanel
    capabilities: frozenset[Capability] = frozenset()
    # Setting keys with these prefixes only make sense on this connector (app_setting is
    # shared, so the channel editor has to know whose fields it is showing).
    settings_prefixes: tuple[str, ...] = ()
    # None = sends are never refused for being late (see OutboxSender.send_next).
    send_window: SendWindow | None = None
    # May this connector write to someone who is NOT writing to us right now?
    #
    # False is not a policy switch an operator flips — it is a fact about the correspondent.
    # A website visitor exists for the length of one HTTP request and has no address of any
    # kind afterwards, so every piece of proactive machinery is downstream of that one fact:
    # follow-up timers, dormant reactivation, the wind-down to DORMANT that ends the follow-up
    # ladder, the quiet-hour hold and the anti-ban send caps all shape messages we send
    # UNPROMPTED, and there is nobody to send them to. The owner's S6 decision was to isolate
    # the site as its own branch precisely because "нет фолоапов" — so the fact lives here and
    # the harvests ask the connector, instead of every worker growing an `if branch_id == N`.
    proactive_outreach: bool = True
    # Whether an unanswered thread on this connector belongs in the inbox "awaiting reply"
    # split. False = its chats are counted nowhere.
    counts_as_awaiting: bool = True

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities
