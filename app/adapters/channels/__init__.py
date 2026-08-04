"""Channel adapters package — one adapter per ChannelKind.

Importing this package pulls no third-party channel deps: real transports import
httpx/instagrapi lazily.

The kind→class REGISTRY that used to live here is gone: it mapped a kind to an adapter CLASS
and never instantiated anything, so every caller still had to know how to build the transport
by hand and the real dispatch was an if-chain in worker/wiring. app/connectors/registry.py now
maps a kind to a full ConnectorSpec — adapter class included — and is the only registry."""
from __future__ import annotations

from .instagram import InstagramAdapter
from .meta_business import MetaBusinessAdapter
from .whatsapp import WhatsAppAdapter

__all__ = [
    "InstagramAdapter",
    "MetaBusinessAdapter",
    "WhatsAppAdapter",
]
