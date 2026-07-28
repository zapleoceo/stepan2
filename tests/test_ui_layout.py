"""The admin layout has to survive the widths people actually open it at.

Nothing tested this before. The two ends of the range were fine — a phone gets the slide-in
layout, a wide monitor gets three roomy columns — and that is exactly why the middle went
unnoticed for so long: above the 760px phone breakpoint the desktop columns come back at full
width and NONE of them can shrink (flex-shrink:0). Measured on the live page 2026-07-28:
210 + 305 + 4 + 4 = 523px of chrome, leaving a 768px tablet 245px of chat.

These are arithmetic tests on the stylesheet rather than screenshots: the failure was never
subtle rendering, it was a sum.
"""
from __future__ import annotations

import re

from app.api._ui_css import _CSS, _icons_only_sidebar

_HANDLE = 4  # .sbrz / .thrz, one each side of the thread list
_MIN_USABLE_CHAT = 400  # below this a reply bubble wraps to unreadable ribbons


_MEDIA_BLOCK_RE = re.compile(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}")


def _base_css() -> str:
    """The stylesheet with every media block removed — what a wide screen actually gets. The
    narrow-range rules match the same selectors, so measuring the default without stripping
    them reads the tablet layout back."""
    return _MEDIA_BLOCK_RE.sub("", _CSS)


def _width_of(selector: str, css: str | None = None) -> int:
    """The px width the stylesheet gives a selector — last declaration wins, as in the cascade."""
    matches = re.findall(re.escape(selector) + r"\{[^}]*?width:(\d+)px", css or _base_css())
    assert matches, f"no width found for {selector}"
    return int(matches[-1])


def _media_block(condition: str) -> str:
    start = _CSS.index(condition)
    depth, i = 0, _CSS.index("{", start)
    for j in range(i, len(_CSS)):
        depth += (_CSS[j] == "{") - (_CSS[j] == "}")
        if depth == 0:
            return _CSS[start:j + 1]
    raise AssertionError(f"unbalanced braces after {condition}")


def test_a_wide_screen_leaves_the_chat_the_majority_of_the_window() -> None:
    chrome = _width_of(".sid") + _width_of(".thr") + 2 * _HANDLE
    assert chrome == 523  # the number the middle-range breakpoint exists to fix
    assert 1440 - chrome > 900


def test_the_tablet_range_is_not_left_on_the_desktop_layout() -> None:
    """The bug this file was written for: at 768px the desktop layout gave the chat 245px."""
    narrow = _media_block("@media (min-width:761px) and (max-width:1150px)")
    chrome = _width_of(".sid", narrow) + _width_of(".thr", narrow) + 2 * _HANDLE
    assert chrome < 320
    for viewport in (768, 820, 1024, 1150):
        assert viewport - chrome > _MIN_USABLE_CHAT, viewport


def test_the_narrow_range_starts_where_the_phone_layout_stops() -> None:
    """No gap and no overlap between the two: 760 is the last phone width, 761 the first
    tablet one. An off-by-one either way leaves a viewport with both layouts or neither."""
    assert "@media (min-width:761px) and (max-width:1150px)" in _CSS
    assert "@media (max-width:760px)" in _CSS


def test_a_phone_gets_one_full_width_column_not_three() -> None:
    phone = _media_block("@media (max-width:760px)")
    assert "position:fixed" in phone           # sidebar becomes an overlay
    assert ".thr{width:auto" in phone          # thread list takes the width
    assert ".sbrz,.thrz{display:none}" in phone  # resize handles are meaningless on touch


def test_the_icons_only_sidebar_is_written_once() -> None:
    """Reached two ways — by hand and by width — and it must look the same both times. Two
    copies is how they drift, and nothing would notice until one of them looked wrong."""
    by_hand = _icons_only_sidebar(".sid.collapsed")
    by_width = _icons_only_sidebar(".sid")
    assert by_hand in _CSS and by_width in _CSS
    # Same declarations, different subject: rebuild both from one template with a sentinel
    # selector. (Substring-replacing ".sid" out of the result would also eat ".sid-ft".)
    template = _icons_only_sidebar("SEL")
    assert by_hand == template.replace("SEL", ".sid.collapsed")
    assert by_width == template.replace("SEL", ".sid")


def test_the_collapsed_sidebar_still_beats_its_own_default_width() -> None:
    """.sid carries width:210px unconditionally, so the collapsed form needs !important or it
    silently loses the cascade and the icons-only layout is 210px wide."""
    assert "width:48px!important" in _icons_only_sidebar(".sid")


def test_wide_content_scrolls_inside_itself_rather_than_the_page() -> None:
    """A report table is wider than a phone. It must scroll in its own box — a horizontally
    scrolling document moves the whole UI sideways, including the nav."""
    assert "html,body{height:100%;overflow-x:hidden}" in _CSS
    assert ".tbl{min-width:520px}" in _media_block("@media (max-width:760px)")
