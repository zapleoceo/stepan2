"""WhatsApp connector — Evolution API instance (private, follow-up channel)."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.transports import EvolutionTransport
from app.adapters.channels.whatsapp import WhatsAppAdapter
from app.adapters.db.models import Channel
from app.domain.enums import ChannelKind
from app.ports.channel import ChannelPort

from .session_store import active_session_settings
from .spec import ConnectorSpec, CredentialField
from .whatsapp_ui import _ch_wa_form


async def build_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    dump = await active_session_settings(session, channel.id or 0)
    if dump is None:
        raise RuntimeError(f"no WhatsApp config for channel {channel.id}")
    transport = EvolutionTransport(
        base_url=dump["base_url"],
        instance=dump["instance"],
        api_key=dump["api_key"],
    )
    return WhatsAppAdapter(transport, instance=dump["instance"])


SPEC = ConnectorSpec(
    kind=ChannelKind.WHATSAPP,
    label="WhatsApp",
    label_key="ch.kind_wa",
    icon_class="fa-brands fa-whatsapp",
    icon_color="#25d366",
    adapter=WhatsAppAdapter,
    build_port=build_port,
    credential_panel=_ch_wa_form,
    credential_fields=(
        CredentialField("base_url", "ch.wa_url"),
        CredentialField("instance", "ch.wa_inst"),
        CredentialField("api_key", "ch.wa_key", secret=True),
    ),
)
