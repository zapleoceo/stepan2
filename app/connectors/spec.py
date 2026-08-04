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
class CredentialField:
    """One value the operator supplies to connect this channel.

    `name` is the form field name AND the key the stored secret dict is read back by, so the
    connect route and the port builder can no longer disagree about a literal string."""

    name: str
    label: str
    secret: bool = False


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
    credential_fields: tuple[CredentialField, ...] = ()
    capabilities: frozenset[Capability] = frozenset()
    # Setting keys with these prefixes only make sense on this connector (app_setting is
    # shared, so the channel editor has to know whose fields it is showing).
    settings_prefixes: tuple[str, ...] = ()
    # The platform closes its messaging window some time after the lead's last message and
    # rejects automated sends into a closed one — see OutboxSender.send_next.
    enforces_send_window: bool = False
    # Whether an unanswered thread on this connector belongs in the inbox "awaiting reply"
    # split. False = its chats are counted nowhere.
    counts_as_awaiting: bool = True

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities
