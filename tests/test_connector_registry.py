"""The contract every ConnectorSpec must satisfy.

The point of the registry is that adding TikTok or Telegram is a NEW FILE. That only holds
if the new file is CHECKED rather than trusted, so these tests run over `all_specs()` and
never name a connector — a spec that lies about its capabilities, forgets an icon, or claims
a kind that no adapter serves fails here on the day it is written, not in production.

The last group pins branch 1: its Instagram port must be built with exactly the constructor
kwargs it was built with before this registry existed.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.connectors import instagram as ig_connector
from app.connectors.registry import REGISTRY, all_specs, spec_for, supports
from app.connectors.spec import (
    BASELINE_METHODS,
    CAPABILITY_METHODS,
    Capability,
    ConnectorSpec,
)
from app.domain.enums import ChannelKind

_SPECS = all_specs()


def test_every_channel_kind_has_a_spec_and_no_spec_is_orphaned() -> None:
    """A kind with no spec is a channel the operator can create and nothing can ever serve."""
    assert set(REGISTRY) == set(ChannelKind)
    for kind, spec in REGISTRY.items():
        assert spec.kind is kind  # a spec filed under someone else's kind


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_spec_adapter_advertises_its_own_kind(spec: ConnectorSpec) -> None:
    assert spec.adapter.kind is spec.kind


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_adapter_implements_the_baseline_every_connector_promises(spec: ConnectorSpec) -> None:
    for method in BASELINE_METHODS:
        assert callable(getattr(spec.adapter, method, None)), f"{spec.kind}: no {method}()"


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_declared_capabilities_exist_on_the_adapter(spec: ConnectorSpec) -> None:
    """A claimed capability whose method is missing would crash at the call site, in a cron,
    on a live branch."""
    for cap in spec.capabilities:
        for method in CAPABILITY_METHODS[cap]:
            assert callable(getattr(spec.adapter, method, None)), \
                f"{spec.kind} claims {cap} but has no {method}()"


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_undeclared_capabilities_are_absent_from_the_adapter(spec: ConnectorSpec) -> None:
    """The other direction, and the one that keeps capabilities honest: an adapter that grows
    revoke() without the spec saying so is a power the system silently will not use (and used
    to use, via hasattr, without anyone deciding)."""
    for cap, methods in CAPABILITY_METHODS.items():
        if cap in spec.capabilities:
            continue
        for method in methods:
            assert getattr(spec.adapter, method, None) is None, \
                f"{spec.kind} implements {method}() but does not declare {cap}"


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_spec_carries_everything_the_ui_renders(spec: ConnectorSpec) -> None:
    """Labels, icons and the credential panel used to live in seven other places; a spec
    missing one of them would render a blank chip or an 'Unknown channel kind' panel."""
    assert spec.label.strip()
    assert spec.label_key.startswith("ch.")
    assert spec.icon_class.strip()
    assert spec.icon_color.startswith("#")
    assert callable(spec.credential_panel)
    panel = spec.credential_panel(4242)
    assert "<form" in panel or "<a " in panel
    assert "4242" in panel  # the panel posts back to THIS channel


def test_labels_icons_and_kinds_are_unique() -> None:
    """Two connectors sharing a label or an icon are indistinguishable in the inbox."""
    assert len({s.label for s in _SPECS}) == len(_SPECS)
    assert len({(s.icon_class, s.icon_color) for s in _SPECS}) == len(_SPECS)
    assert len({s.kind for s in _SPECS}) == len(_SPECS)


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_port_builder_is_an_async_two_arg_callable(spec: ConnectorSpec) -> None:
    """build_channel_port awaits this with (session, channel) and nothing else."""
    assert inspect.iscoroutinefunction(spec.build_port)
    assert len(inspect.signature(spec.build_port).parameters) == 2


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_credential_field_names_are_unique_within_a_connector(spec: ConnectorSpec) -> None:
    names = [f.name for f in spec.credential_fields]
    assert len(names) == len(set(names))
    assert all(f.name and f.label for f in spec.credential_fields)


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.kind.value)
def test_settings_prefixes_are_not_shared_between_connectors(spec: ConnectorSpec) -> None:
    """The channel editor hides a field when ANOTHER connector owns its prefix; two owners of
    the same prefix would make that answer arbitrary."""
    others = [p for s in _SPECS if s is not spec for p in s.settings_prefixes]
    for prefix in spec.settings_prefixes:
        assert prefix not in others


def test_spec_for_tolerates_raw_and_unknown_kinds() -> None:
    """Channel.kind is a VARCHAR — rows written by hand come back as plain strings."""
    assert spec_for("instagram") is REGISTRY[ChannelKind.INSTAGRAM]
    assert spec_for(ChannelKind.WHATSAPP) is REGISTRY[ChannelKind.WHATSAPP]
    assert spec_for("tiktok") is None
    assert spec_for(None) is None
    assert supports("tiktok", Capability.REVOKE) is False


# --- Branch 1 pin -------------------------------------------------------------
#
# 37k live messages run through the Instagram port. Moving its construction out of
# worker/wiring into app/connectors/instagram.py must not change ONE kwarg: a different
# proxy or a missing lang is a fresh device fingerprint to Instagram, i.e. a checkpoint.

_IG_CTOR_SNAPSHOT: dict[str, Any] = {
    "username": "itstep_jakarta",
    "session_settings": {"uuids": {"device_id": "android-1"}},
    "proxy": "http://per-channel:8080",
    "lang": "id",
    "tz_offset_h": 7,
}


class _RecordingTransport:
    """Records what it was constructed with. Deliberately NOT a stand-in that accepts
    anything and returns a fixed object: the thing under test IS the kwargs."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _RecordingTransport.last_kwargs = kwargs


@pytest.mark.asyncio
async def test_instagram_spec_builds_the_transport_with_the_pinned_kwargs(
    monkeypatch: pytest.MonkeyPatch, db_session,  # noqa: ANN001
) -> None:
    from app.adapters.channels.instagram import InstagramAdapter
    from app.adapters.db.models import Branch, Channel, ChannelSession
    from app.domain.enums import SessionStatus

    branch = Branch(name="Indonesia", tz_offset=7, tz_offset_h=7, lang="id")
    db_session.add(branch)
    await db_session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM,
                      handle="itstep_jakarta")
    db_session.add(channel)
    await db_session.flush()
    db_session.add(ChannelSession(
        channel_id=channel.id, status=SessionStatus.ACTIVE,
        secret_enc=_enc({"uuids": {"device_id": "android-1"},
                         "proxy": "http://per-channel:8080"})))
    await db_session.commit()

    monkeypatch.setattr(ig_connector, "InstagrapiTransport", _RecordingTransport)
    _RecordingTransport.last_kwargs = {}

    port = await REGISTRY[ChannelKind.INSTAGRAM].build_port(db_session, channel)

    assert _RecordingTransport.last_kwargs == _IG_CTOR_SNAPSHOT
    assert isinstance(port, InstagramAdapter)


@pytest.mark.asyncio
async def test_instagram_falls_back_to_the_global_proxy_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch, db_session,  # noqa: ANN001
) -> None:
    """A channel with no proxy of its own inherits the global one — the fake above would hide
    this if it ignored what it was handed."""
    from app.adapters.db.models import Branch, Channel, ChannelSession
    from app.domain.enums import SessionStatus

    branch = Branch(name="Indonesia", tz_offset=7, tz_offset_h=7, lang="id")
    db_session.add(branch)
    await db_session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM,
                      handle="itstep_jakarta")
    db_session.add(channel)
    await db_session.flush()
    db_session.add(ChannelSession(
        channel_id=channel.id, status=SessionStatus.ACTIVE,
        secret_enc=_enc({"uuids": {"device_id": "android-1"}})))
    await db_session.commit()

    monkeypatch.setattr(ig_connector, "InstagrapiTransport", _RecordingTransport)
    monkeypatch.setattr(ig_connector, "settings", lambda: _FakeSettings("http://global:9"))
    _RecordingTransport.last_kwargs = {}

    await REGISTRY[ChannelKind.INSTAGRAM].build_port(db_session, channel)

    assert _RecordingTransport.last_kwargs == {**_IG_CTOR_SNAPSHOT,
                                               "proxy": "http://global:9"}


class _FakeSettings:
    def __init__(self, ig_proxy: str) -> None:
        self.ig_proxy = ig_proxy


def _enc(payload: dict) -> str:
    import json

    from app.adapters.crypto import encrypt
    return encrypt(json.dumps(payload))
