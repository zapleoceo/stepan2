"""WhatsApp connector — Evolution API instance (private, follow-up channel)."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.transports import EvolutionTransport
from app.adapters.channels.whatsapp import WhatsAppAdapter
from app.adapters.db.models import Channel
from app.config import settings
from app.domain.enums import ChannelKind
from app.ports.channel import ChannelPort

from .session_store import active_session_settings
from .spec import ConnectorSpec
from .whatsapp_ui import _ch_wa_form


async def build_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    dump = await active_session_settings(session, channel.id or 0)
    if dump is None:
        raise RuntimeError(f"no WhatsApp config for channel {channel.id}")
    # Server address and key come from the environment: Evolution is OUR service, one per
    # deployment, and the pairing panel no longer asks for them. A row written by the OLD
    # three-field form still carries its own — honour it, so an existing channel keeps
    # working without being re-paired.
    cfg = settings()
    transport = EvolutionTransport(
        base_url=dump.get("base_url") or cfg.evolution_url,
        instance=dump["instance"],
        api_key=dump.get("api_key") or cfg.evolution_api_key,
    )
    # The channel row, not the dump: one source of truth, and the one the reply dispatcher
    # and the funnel can both read. The dump's copy stays only for rows written before the
    # column existed and is honoured when the column is still at its default.
    read_only = bool(getattr(channel, "read_only", False) or dump.get("read_only"))
    return WhatsAppAdapter(transport, instance=dump["instance"], read_only=read_only)


SPEC = ConnectorSpec(
    kind=ChannelKind.WHATSAPP,
    label="WhatsApp",
    label_key="ch.kind_wa",
    icon_class="fa-brands fa-whatsapp",
    icon_color="#25d366",
    adapter=WhatsAppAdapter,
    build_port=build_port,
    credential_panel=_ch_wa_form,
)
