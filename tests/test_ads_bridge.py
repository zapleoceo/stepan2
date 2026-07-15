"""pk→shortcode bridge. Vectors are REAL pairs verified against prod data + Graph:
our channel_thread.ad_media_id on the left, the shortcode in the matching adcreative's
instagram_permalink_url on the right. They pin the alphabet and the digit order — a silent
change to either would break ad attribution while still returning a plausible string."""
from __future__ import annotations

import pytest

from app.modules.ads.bridge import pk_to_shortcode, shortcode_from_permalink

# (media pk from instagrapi, shortcode in Meta's instagram_permalink_url)
REAL_PAIRS = [
    (3932267938260790752, "DaSONsVsS3g"),
    (3931661706982573994, "DaQEX3ds8eq"),
    (3932264179182956279, "DaSNW_bMW73"),
    (3902640133392596802, "DYo9oY1DH9C"),
    (3910685743601433877, "DZFi_bPjD0V"),
    (3927914342304182186, "DaCwUiJM--q"),  # exercises '-' (62) twice
    (3930243273984118861, "DaLB28ysRBN"),
]


@pytest.mark.parametrize(("pk", "code"), REAL_PAIRS)
def test_pk_to_shortcode_matches_real_meta_permalinks(pk: int, code: str) -> None:
    assert pk_to_shortcode(pk) == code


@pytest.mark.parametrize(("pk", "code"), REAL_PAIRS)
def test_accepts_pk_as_string(pk: int, code: str) -> None:
    # ad_media_id is stored as a VARCHAR column, so the str path is the one used in prod.
    assert pk_to_shortcode(str(pk)) == code


def test_shortcodes_are_unique_per_pk() -> None:
    codes = [pk_to_shortcode(pk) for pk, _ in REAL_PAIRS]
    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize("bad", [0, -1, "0"])
def test_rejects_non_positive_pk(bad: int | str) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        pk_to_shortcode(bad)


def test_rejects_garbage_pk() -> None:
    with pytest.raises(ValueError):
        pk_to_shortcode("not-a-number")


def test_shortcode_from_permalink_real_url() -> None:
    assert shortcode_from_permalink(
        "https://www.instagram.com/p/DZFhgMoDKt5/") == "DZFhgMoDKt5"


def test_shortcode_from_permalink_handles_escaped_slashes_and_reels() -> None:
    assert shortcode_from_permalink("https://www.instagram.com/p/DaQEX3ds8eq") == "DaQEX3ds8eq"


@pytest.mark.parametrize("bad", [None, "", "https://www.instagram.com/", "not a url"])
def test_shortcode_from_permalink_none_when_absent(bad: str | None) -> None:
    assert shortcode_from_permalink(bad) is None


def test_round_trip_pk_to_code_to_permalink() -> None:
    pk, code = REAL_PAIRS[1]
    assert shortcode_from_permalink(f"https://www.instagram.com/p/{pk_to_shortcode(pk)}/") == code
