"""Section split/reassemble — the editor round-trips a doc through per-`##` textareas
without drifting the markdown (which would move RAG chunk boundaries)."""
from __future__ import annotations

from app.modules.knowledge.sections import reassemble, split_sections


def test_split_keeps_preamble_and_headings() -> None:
    content = "intro line\n\n## Price\nCosts 1M.\n\n## Refund\nNo refund."
    pairs = split_sections(content)
    assert pairs[0] == ("", "intro line")
    assert ("Price", "Costs 1M.") in pairs
    assert ("Refund", "No refund.") in pairs


def test_round_trip_is_stable() -> None:
    content = "## Voice\nBe kind.\n\n## Rules\nNever spam."
    assert reassemble(split_sections(content)) == content


def test_reassemble_drops_empty_sections() -> None:
    out = reassemble([("Price", ""), ("Refund", "No refund."), ("", "")])
    assert out == "## Refund\nNo refund."


def test_split_flat_content_is_one_preamble() -> None:
    assert split_sections("just text") == [("", "just text")]
    assert split_sections("") == []
