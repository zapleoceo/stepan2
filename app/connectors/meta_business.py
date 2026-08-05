"""Meta Business connector — the official Graph API over a Page/System User token."""
from __future__ import annotations

import logging

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.meta_business import MetaBusinessAdapter
from app.adapters.channels.transports import GraphTransportHTTP
from app.adapters.db.models import Channel
from app.config import settings
from app.domain.enums import ChannelKind
from app.modules.meta.tokens import page_access_token
from app.modules.settings.service import get_channel_settings
from app.ports.channel import ChannelPort

from .meta_business_ui import _ch_meta_form
from .session_store import active_session_settings
from .spec import Capability, ConnectorSpec, SendWindow

_log = logging.getLogger(__name__)

# channel_id -> (source System User token, page id, derived Page token). Keyed on the source
# token so rotating it in settings invalidates the entry instead of serving a revoked Page
# token until the worker restarts.
_PAGE_TOKENS: dict[int, tuple[str, str, str]] = {}


async def _page_token_cached(system_user_token: str, page_id: str, channel_id: int) -> str:
    """Page token for `page_id`, derived once per (channel, source token, page).

    /{page-id}/conversations and /messages answer "(#190) This method must be called with a
    Page Access Token" for a System User token, and the System User token is the one the
    operator can actually obtain — so the exchange belongs here rather than in their hands.

    A failed exchange returns the original token: Graph's own error on the real call names the
    problem better than an exception raised one layer away from it, and the channel keeps
    behaving exactly as it did before this function existed.
    """
    cached = _PAGE_TOKENS.get(channel_id)
    if cached and cached[0] == system_user_token and cached[1] == page_id:
        return cached[2]
    if not page_id:
        return system_user_token
    try:
        derived = await page_access_token(system_user_token, page_id)
    except (httpx.HTTPError, ValueError) as exc:
        _log.warning("page token exchange failed for channel %s: %s", channel_id, exc)
        return system_user_token
    _PAGE_TOKENS[channel_id] = (system_user_token, page_id, derived)
    return derived


async def build_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    # The token comes from the per-channel SETTING the connector editor writes
    # (app_setting meta_system_user_token). It used to be read from ChannelSession only —
    # but nothing in the codebase ever writes a ChannelSession for this kind, so an
    # operator could paste a valid token, see it saved, and still get "no active token"
    # forever. ChannelSession stays as a fallback for anything that populates it later.
    dump = await active_session_settings(session, channel.id or 0) or {}
    cfg = await get_channel_settings(session, channel.branch_id, channel.id or 0)
    token = dump.get("token") or cfg.meta_system_user_token
    if not token:
        raise RuntimeError(f"no active token for Meta Business channel {channel.id}")
    account_id = (dump.get("account_id") or cfg.meta_page_id
                  or channel.account_id or "")
    # Only when the token came from settings: a token stored in a ChannelSession was put
    # there by the connect form, which already exchanged it.
    if not dump.get("token"):
        token = await _page_token_cached(token, account_id, channel.id or 0)
    transport = GraphTransportHTTP(
        base_url=dump.get("base_url",
                          f"https://graph.facebook.com/{settings().ig_graph_version}"),
        account_id=account_id,
        token=token,
    )
    return MetaBusinessAdapter(transport, account_id=account_id)


SPEC = ConnectorSpec(
    kind=ChannelKind.META_BUSINESS,
    label="Meta Business",
    label_key="ch.kind_meta",
    icon_class="fa-brands fa-facebook",
    icon_color="#1877f2",
    adapter=MetaBusinessAdapter,
    build_port=build_port,
    credential_panel=_ch_meta_form,
    capabilities=frozenset({Capability.DOWNLOAD_MEDIA}),
    settings_prefixes=("meta_", "fb_"),
    # Meta closes the standard messaging window ~24h after the lead's last message and
    # rejects an AUTOMATED send into a closed one. Both strings are load-bearing history:
    # `meta_window_closed` is what every row written since this gate existed carries and what
    # the inbox queries match on, and the reason is what an operator reads on a paused thread.
    send_window=SendWindow(
        error_code="meta_window_closed",
        dormant_reason="Meta 24h window closed — paused until lead writes",
    ),
    # The connector is not finished, so its unanswered chats just hang — counting them in the
    # inbox "awaiting reply" split would put work in the queue nobody can act on. Flip this to
    # True the day it is finished; nothing else has to change.
    counts_as_awaiting=False,
    # One authenticated Graph request per poll against a published rate limit — none of the
    # anti-ban arithmetic that puts instagrapi on a 2-minute cadence applies here. Halves the
    # wait until webhooks are live (blocked on App Review).
    polls_every_minute=True,
)
