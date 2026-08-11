"""kind -> ConnectorSpec. The one place that knows which connectors exist.

The previous registry mapped kind -> adapter CLASS and never instantiated anything, so it
was decorative: the real wiring was an if-chain in worker/wiring, the labels were dicts in
three UI modules, the icons in a fourth, and the credential panels a fifth if-chain. Adding a
connector meant finding all of them. Now it means adding one module and one line here."""
from __future__ import annotations

from app.domain.enums import ChannelKind

from . import crm_sender, instagram, meta_business, website, whatsapp
from .spec import Capability, ConnectorSpec

# Insertion order IS the display order — the inbox filter chips and the "add channel"
# selector render in this sequence. It matches what those two lists showed before the
# registry existed; a new connector goes wherever it should appear on screen.
REGISTRY: dict[ChannelKind, ConnectorSpec] = {
    ChannelKind.INSTAGRAM: instagram.SPEC,
    ChannelKind.META_BUSINESS: meta_business.SPEC,
    ChannelKind.WHATSAPP: whatsapp.SPEC,
    ChannelKind.CRM_SENDER: crm_sender.SPEC,
    ChannelKind.WEBSITE: website.SPEC,
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


def addable_specs() -> tuple[ConnectorSpec, ...]:
    """The connectors a branch panel may offer, in display order. See operator_addable."""
    return tuple(s for s in REGISTRY.values() if s.operator_addable)


def is_operator_addable(kind: ChannelKind | str | None) -> bool:
    """May an operator create a channel of this kind? Unregistered kinds may not.

    Stricter than `does_proactive_outreach` on purpose: this answers a WRITE request, and the
    safe answer to "I do not recognise this kind" is no."""
    spec = spec_for(kind)
    return spec is not None and spec.operator_addable


def supports(kind: ChannelKind | str | None, capability: Capability) -> bool:
    """Does this channel kind's connector do `capability`? Unregistered kinds do nothing."""
    spec = spec_for(kind)
    return spec is not None and spec.supports(capability)


def does_proactive_outreach(kind: ChannelKind | str | None) -> bool:
    """May the proactive machinery write to a silent lead on this connector?

    An UNREGISTERED kind answers True, unlike `supports`. The two defaults differ on purpose:
    a capability is an extra power, so not knowing about it means not using it; outreach is
    what every DM connector has always done, so a channel row this build does not recognise
    (a kind added by a newer deploy, a hand-written row) must keep behaving as it did rather
    than have its follow-ups silently switched off."""
    spec = spec_for(kind)
    return spec is None or spec.proactive_outreach


def non_outreach_kinds() -> tuple[str, ...]:
    """Kind values whose connector declares no proactive outreach — for the harvest queries.

    Never empty: the harvests bind this into `ch.kind NOT IN (:kinds)` before their LIMIT, and
    an empty list is a SQL hole rather than a portable no-op. The sentinel is a kind no row can
    carry, so "every connector does outreach" reads as "exclude nothing"."""
    kinds = tuple(s.kind.value for s in REGISTRY.values() if not s.proactive_outreach)
    return kinds or ("",)


def windowed_kinds() -> tuple[str, ...]:
    """Kind values whose connector is REFUSED outside the platform's messaging window.

    The window itself is written for every kind (thread.window_until); being refused outside it
    is what differs. Same sentinel rule as above — an empty IN list is a SQL hole, so "nobody
    has a window" has to read as "match no row"."""
    kinds = tuple(s.kind.value for s in REGISTRY.values() if s.send_window is not None)
    return kinds or ("",)
