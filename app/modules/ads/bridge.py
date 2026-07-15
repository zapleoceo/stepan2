"""Bridge an IG media pk (private API) to a Meta Marketing API ad, via the shortcode.

instagrapi hands us `ad_ig_media_id` — Instagram's internal media **pk** (e.g.
3931661706982573994). Marketing API never exposes that pk: an adcreative carries
`instagram_permalink_url` (…/p/DaQEX3ds8eq/) and `effective_instagram_media_id`, both in a
DIFFERENT id space. The pk, however, IS the shortcode: the shortcode is the pk written in
base64 with Instagram's alphabet. Decoding one side is what joins our leads to real ad spend.

The direct `ad_id` we also store is NOT usable for this join — instagrapi's ad_id lives in yet
another id space and resolves to code 100 against Graph. Match on the media pk, not the ad id.
"""
from __future__ import annotations

import re

# Instagram's base64 alphabet for shortcodes — standard base64 with '-'/'_' as 62/63.
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_PERMALINK_RE = re.compile(r"/p/([A-Za-z0-9_-]+)")


def pk_to_shortcode(pk: int | str) -> str:
    """IG media pk → shortcode. Raises ValueError on a non-positive / non-numeric pk."""
    value = int(pk)
    if value <= 0:
        raise ValueError(f"media pk must be positive, got {pk!r}")
    out = ""
    while value > 0:
        value, rem = divmod(value, 64)
        out = _ALPHABET[rem] + out
    return out


def shortcode_from_permalink(url: str | None) -> str | None:
    """Pull the shortcode out of an instagram_permalink_url; None when absent/unparseable."""
    if not url:
        return None
    match = _PERMALINK_RE.search(url)
    return match.group(1) if match else None
