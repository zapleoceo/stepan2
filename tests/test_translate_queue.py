"""Translate-all walks the thread one bubble at a time.

It used to be a forEach over trMsg with nothing awaited: click it on a long thread and thirty
broker calls left in the same instant, of which a share never came back. Reported from the chat
header, twice.

The KB translate-all has been sequential from the start — same button, same broker, ten lines
apart in the same file — so this is not a new idea, only one that never reached the chat.

There is no JS test harness here, so these read the shipped script out of the source. That is
weaker than driving a browser, and it is what actually catches THIS regression: the bug was a
missing await, and its absence is plain in the source.
"""
from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "app" / "api" / "_ui_html.py"


def _shipped_js() -> str:
    """The `script = (...)` block of app_shell, un-quoted back into plain JavaScript.

    Python-side comments are dropped and each string literal is unwrapped; f-string prefixes
    are kept as text since nothing here asserts on an interpolated value."""
    lines = _SRC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "script = (")
    end = next(i for i, ln in enumerate(lines[start + 1:], start + 1) if ln == "    )")
    out: list[str] = []
    for raw in lines[start + 1:end]:
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        ln = re.sub(r"\s*#.*$", "", ln)
        m = re.fullmatch(r'f?"(.*)"', ln) or re.fullmatch(r"f?'(.*)'", ln)
        if m:
            out.append(m.group(1))
    js = "".join(out)
    assert "function trAll(" in js, "the script block was not reconstructed"
    return js


def _fn(name: str) -> str:
    js = _shipped_js()
    start = js.index(f"function {name}(")
    depth, i = 0, js.index("{", start)
    for j in range(i, len(js)):
        depth += (js[j] == "{") - (js[j] == "}")
        if depth == 0:
            return js[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_the_batch_never_fires_every_request_at_once() -> None:
    """The exact shape of the original bug: iterate, call, await nothing.

    Strict one-at-a-time replaced it and was too far the other way — minutes on a long chat.
    The pool is bounded instead: a fixed number of workers pulling from one queue, so the
    broker is never handed the whole thread and never left idle either."""
    body = _fn("trAll")
    assert ".forEach(" not in body, "a forEach over trMsg is the burst this replaced"
    assert re.search(r"for\(var w=0;w<_TRPAR", body), body


def test_no_request_is_fired_without_being_awaited() -> None:
    """What actually keeps translations from going missing: a worker starts the next bubble
    only from inside the previous one's callback, so an unanswered request holds its slot
    instead of being replaced by another."""
    body = _fn("trAll")
    assert re.search(r"trMsg\([^)]*\)\.then\(", body), body
    assert "pump()" in body and "tick()" in body


def test_the_newest_message_is_translated_first() -> None:
    """The operator is looking at the bottom of the chat. Starting from the top spends the
    first minute on messages from three weeks ago while the line that prompted the click
    sits untranslated."""
    assert ".reverse()" in _fn("trAll")


def test_a_failed_bubble_is_retried_before_being_given_up_on() -> None:
    """"So that they all come back" was the actual request. One retry after a pause is the
    difference between most of them and all of them."""
    body = _fn("trAll")
    assert "setTimeout(" in body
    assert body.count("trMsg(") >= 2


def test_failures_are_reported_once_not_once_per_bubble() -> None:
    body = _fn("trAll")
    assert "failed++" in body
    assert body.count("toast(") == 1, "one summary, not a toast storm"


def test_the_button_cannot_start_a_second_queue_over_the_same_bubbles() -> None:
    body = _fn("trAll")
    assert "disabled=true" in body and "disabled=false" in body
    assert "+'/'+" in body  # progress, so a slow thread does not look stuck


def test_a_single_bubble_click_still_reports_its_own_failure() -> None:
    """The batch silences per-bubble toasts with `quiet`. A lone click must not be silenced with
    it — no summary is coming for that one."""
    body = _fn("trMsg")
    assert "if(!quiet)toast(" in body


def test_translating_one_bubble_resolves_so_the_queue_can_wait_on_it() -> None:
    """Every exit returns a promise, including the cached and toggle paths. A bare `return`
    anywhere would have the queue treat that bubble as finished-with-undefined and race on."""
    body = _fn("trMsg")
    assert body.count("Promise.resolve(true)") == 3  # missing element, untoggle, cached
    assert "return trFetch(" in body


def test_the_header_passes_the_button_so_progress_has_somewhere_to_go() -> None:
    src = _SRC.read_text(encoding="utf-8")
    assert 'onclick="trAll({tid},this)"' in src
