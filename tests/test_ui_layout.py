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


def test_the_tablet_range_gives_the_chat_back_what_it_can() -> None:
    """At 768px the plain desktop layout leaves the chat 245px. The thread list narrows in this
    range to return some of that — the sidebar deliberately does not, since collapsing it hides
    the switches (see below), so this buys what it can rather than everything.

    A small tablet is still tight afterwards, and that is the honest trade: the remedy there is
    the collapse button, which is the person's call to make."""
    narrow = _media_block("@media (min-width:761px) and (max-width:1150px)")
    assert _width_of(".thr", narrow) < _width_of(".thr")     # the list gives ground
    assert ".sid{" not in narrow                             # the sidebar does not

    desktop_chrome = _width_of(".sid") + _width_of(".thr") + 2 * _HANDLE
    narrow_chrome = _width_of(".sid") + _width_of(".thr", narrow) + 2 * _HANDLE
    assert desktop_chrome - narrow_chrome >= 50
    assert 1024 - narrow_chrome > _MIN_USABLE_CHAT


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


def test_the_sidebar_only_collapses_when_someone_asks_it_to() -> None:
    """The controls people need most — branch picker, bot/sending/comment switches, language —
    live in .sid-ft, which the icons-only sidebar hides. That is fine as the result of pressing
    a button you can press again; it is not fine as something a window width decides.

    A width-driven collapse shipped on 2026-07-28 and came back out the same day: the first
    person to open the panel on a phone could not find the switches. This is what says the
    only route to that state is the class."""
    assert _icons_only_sidebar(".sid.collapsed") in _CSS
    for block in re.findall(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", _CSS):
        assert ".sid-ft{display:none}" not in block, block[:80]
        assert ".sid-ft," not in block, block[:80]
        assert "width:48px" not in block, block[:80]


def test_the_collapsed_sidebar_still_beats_its_own_default_width() -> None:
    """.sid carries width:210px unconditionally, so the collapsed form needs !important or it
    silently loses the cascade and the icons-only layout is 210px wide."""
    assert "width:48px!important" in _icons_only_sidebar(".sid")


def test_wide_content_scrolls_inside_itself_rather_than_the_page() -> None:
    """A report table is wider than a phone. It must scroll in its own box — a horizontally
    scrolling document moves the whole UI sideways, including the nav."""
    assert "html,body{height:100%;overflow-x:hidden}" in _CSS
    assert ".tbl{min-width:520px}" in _media_block("@media (max-width:760px)")


# ── метка канала над сообщением ───────────────────────────────────────────────


def test_a_bubble_names_the_connector_it_came_through() -> None:
    """Сегодня тред принадлежит одному каналу и метка внутри него одинакова. Она нужна для
    слитой ленты лида: там Instagram, WhatsApp и сайт лежат вперемешку, и строка без
    происхождения не читается — непонятно, что клиент видел и куда отвечать."""
    from app.api._ui_html import _bubble

    row = (1, "in", "lead", "halo", None, None, None, None, None, None, None, None)
    assert "WhatsApp" in _bubble(row, 7, None, "WhatsApp")


def test_the_tag_is_on_the_sent_side_too() -> None:
    """Не только над вопросом лида: в слитой ленте ответ мог уйти другим каналом, чем
    пришёл вопрос, и это ровно то, что нужно видеть."""
    from app.api._ui_html import _bubble

    row = (2, "out", "agent", "baik", None, None, None, None, None, None, None, None)
    assert "WhatsApp" in _bubble(row, 7, None, "WhatsApp")


def test_no_label_means_no_empty_separator() -> None:
    from app.api._ui_html import _bubble

    row = (3, "in", "lead", "halo", None, None, None, None, None, None, None, None)
    assert "bm-ch" not in _bubble(row, 7, None, "")


def test_a_hostile_channel_name_cannot_inject_markup() -> None:
    from app.api._ui_html import _bubble

    row = (4, "in", "lead", "halo", None, None, None, None, None, None, None, None)
    assert "<script>" not in _bubble(row, 7, None, "<script>alert(1)</script>")
