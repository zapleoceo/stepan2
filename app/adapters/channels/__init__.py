"""Channel adapters package — one adapter per ChannelKind, plus a kind→class registry.

Importing this package pulls no third-party channel deps: real transports import
httpx/instagrapi lazily, so wiring code can resolve an adapter class by kind freely."""
from __future__ import annotations

from app.domain.enums import ChannelKind
from app.ports.channel import ChannelPort

from .instagram import InstagramAdapter
from .meta_business import MetaBusinessAdapter
from .whatsapp import WhatsAppAdapter

REGISTRY: dict[ChannelKind, type[ChannelPort]] = {
    ChannelKind.INSTAGRAM: InstagramAdapter,
    ChannelKind.WHATSAPP: WhatsAppAdapter,
    ChannelKind.META_BUSINESS: MetaBusinessAdapter,
}

__all__ = [
    "REGISTRY",
    "InstagramAdapter",
    "MetaBusinessAdapter",
    "WhatsAppAdapter",
]
