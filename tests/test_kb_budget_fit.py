"""База знаний, не влезающая в бюджет, выбрасывает блоки целиком, а не режется по символу.

30.07.2026: собиралось 90 744 символа при бюджете 90 000, и `text[:BUDGET]` проходил внутри
карточки Vibe Coding Demo Event. Промт заканчивался обрубком «## PRI» — последним, что модель
читала из всей базы, был сломанный заголовок, а раздел про цену она не видела никогда. На
каждом ответе, молча, только предупреждением в лог.

Оборванный блок строго хуже честно выброшенного: модель не отличает «этого нет» от «это
обрезали» и достраивает недостающее сама — ровно то, против чего написан весь money-gate.
"""
from __future__ import annotations

from app.modules.knowledge.service import KnowledgeService, _FREE_CTX_CHAR_BUDGET


def _fit(blocks: list[str]) -> str:
    svc = object.__new__(KnowledgeService)
    svc.branch_id = 1
    return svc._fit(blocks)  # noqa: SLF001


def test_everything_that_fits_is_kept_untouched() -> None:
    blocks = ["a" * 100, "b" * 100]
    assert _fit(blocks) == "a" * 100 + "\n\n" + "b" * 100


def test_an_overflowing_block_is_dropped_whole_not_sliced() -> None:
    head = "H" * (_FREE_CTX_CHAR_BUDGET - 100)
    tail = "T" * 500
    out = _fit([head, tail])
    assert head in out
    assert "T" not in out            # выброшен целиком
    assert not out.endswith("T")     # и уж точно не обрублен на полуслове


def test_the_result_never_exceeds_the_budget() -> None:
    blocks = ["x" * 30000 for _ in range(5)]
    assert len(_fit(blocks)) <= _FREE_CTX_CHAR_BUDGET


def test_earlier_blocks_win_because_order_is_priority() -> None:
    """Персона и общие документы идут первыми и не выбрасываются; режется хвост карточек."""
    persona = "P" * 40000
    policy = "L" * 40000
    card = "C" * 40000
    out = _fit([persona, policy, card])
    assert "P" in out and "L" in out
    assert "C" not in out


def test_a_later_smaller_block_may_still_fit() -> None:
    """Выбрасываем не всё после первого не влезшего: маленькая карточка за большой должна
    доехать. Иначе один жирный блок обнулял бы весь хвост."""
    head = "H" * (_FREE_CTX_CHAR_BUDGET - 1000)
    big = "B" * 5000
    small = "S" * 100
    out = _fit([head, big, small])
    assert "B" not in out
    assert "S" in out
