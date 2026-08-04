"""Instagram connector — instagrapi (private API) over a stored session dump.

Branch 1's revenue channel. The kwargs handed to InstagrapiTransport below are pinned by
tests/test_connector_registry.py against a snapshot: this file moved out of worker/wiring and
must build byte-identically to what it built there."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.instagram import InstagramAdapter
from app.adapters.channels.transports import InstagrapiTransport
from app.adapters.db.models import Branch, Channel
from app.config import settings
from app.domain.enums import ChannelKind
from app.ports.channel import ChannelPort

from .instagram_ui import _ch_ig_form
from .session_store import active_session_settings
from .spec import Capability, ConnectorSpec, CredentialField


async def build_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    dump = await active_session_settings(session, channel.id or 0)
    if dump is None:
        raise RuntimeError(f"no active session for channel {channel.id}")
    proxy = dump.pop("proxy", None) or settings().ig_proxy  # per-channel proxy first
    branch = await session.get(Branch, channel.branch_id)
    transport = InstagrapiTransport(
        username=channel.handle or "", session_settings=dump, proxy=proxy,
        lang=branch.lang if branch else "", tz_offset_h=branch.tz_offset_h if branch else None)
    return InstagramAdapter(transport, handle=channel.handle or "")


SPEC = ConnectorSpec(
    kind=ChannelKind.INSTAGRAM,
    label="Instagram",
    label_key="ch.kind_ig",
    icon_class="fa-brands fa-instagram",
    icon_color="#e1306c",
    adapter=InstagramAdapter,
    build_port=build_port,
    credential_panel=_ch_ig_form,
    credential_fields=(
        CredentialField("username", "ch.username"),
        CredentialField("password", "ch.password", secret=True),
        CredentialField("sessionid", "ch.sessionid", secret=True),
        CredentialField("session_json", "ch.ig_json", secret=True),
    ),
    capabilities=frozenset({
        Capability.REVOKE,
        Capability.MARK_SEEN,
        Capability.FETCH_PROFILE,
        Capability.DOWNLOAD_MEDIA,
        Capability.COMMENTS,
    }),
)
