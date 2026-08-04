"""Fitting assembled blocks under the context budget — shared by both pipelines."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fitted:
    text: str
    dropped: list[str]  # first line of each block that did not fit, for the caller's log
    full_chars: int  # what the assembly would have been, so the log can name the overshoot


def fit_blocks(blocks: list[str], budget: int) -> Fitted:
    """Собрать под бюджет, выбрасывая блоки ЦЕЛИКОМ и называя выброшенное.

    Было `text[:BUDGET]` — слепой срез по символу. 30.07.2026 он проходил внутри карточки
    Vibe Coding Demo Event: промт заканчивался обрубком «## PRI», и последним, что модель
    читала из всей базы, был сломанный заголовок. Раздел про цену она не видела никогда,
    на каждом ответе, молча.

    Оборванный блок строго хуже честно выброшенного: модель не отличает «этого нет» от
    «это обрезали», и достраивает недостающее сама — ровно то, против чего написан весь
    money-gate.

    Порядок блоков — приоритет: то, что собрано раньше, не выбрасывается; режется хвост.
    Имя выброшенного возвращаем наверх, иначе рост базы снова окажется невидимым."""
    present = [b for b in blocks if b]
    text = "\n\n".join(present)
    if len(text) <= budget:
        return Fitted(text, [], len(text))
    kept: list[str] = []
    dropped: list[str] = []
    size = 0
    for block in present:
        add = len(block) + (2 if kept else 0)
        if size + add > budget:
            dropped.append(block.split("\n", 1)[0][:60])
            continue
        kept.append(block)
        size += add
    return Fitted("\n\n".join(kept), dropped, len(text))
