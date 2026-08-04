"""kind -> ConnectorSpec. The one place that knows which connectors exist.

The previous registry mapped kind -> adapter CLASS and never instantiated anything, so it
was decorative: the real wiring was an if-chain in worker/wiring, the labels were dicts in
three UI modules, the icons in a fourth, and the credential panels a fifth if-chain. Adding a
connector meant finding all of them. Now it means adding one module and one line here."""
from __future__ import annotations

from app.domain.enums import ChannelKind

from . import instagram, meta_business, whatsapp
from .spec import Capability, ConnectorSpec

# Insertion order IS the display order — the inbox filter chips and the "add channel"
# selector render in this sequence. It matches what those two lists showed before the
# registry existed; a new connector goes wherever it should appear on screen.
REGISTRY: dict[ChannelKind, ConnectorSpec] = {
    ChannelKind.INSTAGRAM: instagram.SPEC,
    ChannelKind.META_BUSINESS: meta_business.SPEC,
    ChannelKind.WHATSAPP: whatsapp.SPEC,
}


def spec_for(kind: ChannelKind | str | None) -> ConnectorSpec | None:
    """The spec for a channel kind, or None for anything unregistered.

    Tolerates a raw string: `Channel.kind` is stored as VARCHAR and rows written before a
    kind existed (or by hand) come back as plain text."""
    if kind is None:
        return None
    try:
        return REGISTRY.get(ChannelKind(kind))
    except ValueError:
        return None


def all_specs() -> tuple[ConnectorSpec, ...]:
    """Every registered connector, in REGISTRY (= display) order."""
    return tuple(REGISTRY.values())


def supports(kind: ChannelKind | str | None, capability: Capability) -> bool:
    """Does this channel kind's connector do `capability`? Unregistered kinds do nothing."""
    spec = spec_for(kind)
    return spec is not None and spec.supports(capability)
