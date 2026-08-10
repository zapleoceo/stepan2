"""Судья диалога: то, что счётчиком не берётся.

Восемь измерений, сведённых из чеклиста регрессии (docs/dialogue-qa-checklist.md, 58 строк
живых поломок) и пяти узлов, за которыми следим отдельно: переключение продукта, разбор боли
и ценности, подбор подходящего продукта, передача по воронке и в CRM, переход от согласия на
ивент к согласию на курс.

Шкала намеренно короткая — 0 сломано, 1 сойдёт, 2 хорошо. Полутона судья ставит наугад, а
разницу между раундами видно и на трёх делениях.
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.modules.conversation.routing import SMART
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_TIMEOUT_S = 180.0

DIMENSIONS: dict[str, str] = {
    "opening": "Первый ход: тёплое приветствие и ОДИН вопрос про цель. 0 — питч, список "
               "программ или цена на первом ходу.",
    "answers_the_question": "На прямой вопрос дан прямой ответ в том же ходе. 0 — встречный "
                            "вопрос вместо ответа, просьба уточнить на внятный вопрос, "
                            "просьба дать WhatsApp вместо ответа.",
    "pain_and_value": "Вскрыта настоящая причина, а не только поверхностная цель, и названо, "
                      "что человек получит. 0 — сразу презентация, боль не тронута, или "
                      "страх заглажен утешением вместо вопроса.",
    "right_product": "Предложен продукт, который человеку подходит, и сказано почему. 0 — "
                     "меню из семи программ, или разговор про то, что он кликнул, когда он "
                     "уже сказал, что хочет другое.",
    "objection": "Возражение встречено вопросом, а не скидкой. 0 — рассрочка, DP, скидка или "
                 "курс подешевле в ответ на «дорого»; давление на того, у кого правда нет денег.",
    "next_step": "Один конкретный шаг, от которого легко отказаться. 0 — «есть ещё вопросы?», "
                 "«интересно?», или три просьбы разом.",
    "handoff": "Передача честная: телефон взят до передачи, «готов» стоит только когда человек "
               "согласился именно на то, что обсуждают сейчас. 0 — заявка, которой не было; "
               "согласие на ивент засчитано за согласие на курс; передача без телефона.",
    "human_voice": "Говорит как человек в переписке. 0 — канцелярит, маркерные списки, жирный "
                   "шрифт, штампы вроде «jangan ragu», простыня текста.",
}

_SYSTEM = (
    "Ты придирчивый руководитель отдела продаж. Тебе дают расшифровку переписки между "
    "продавцом курсов (admin) и лидом. Оцени работу ПРОДАВЦА по каждому измерению: "
    "0 — сломано, 1 — сойдёт, 2 — хорошо. Не льсти: 2 ставится только когда придраться не к "
    "чему. Если измерение в этом разговоре не проверялось, ставь null.\n\n"
    "Верни СТРОГО JSON:\n"
    '{"scores": {"<измерение>": 0|1|2|null, ...}, '
    '"worst": "<одна фраза продавца, которая испортила разговор больше всего, дословно>", '
    '"worst_why": "<в чём именно промах, одно предложение по-русски>", '
    '"best": "<лучший ход продавца, дословно>", '
    '"fix": "<что именно поменять в инструкции продавца, одно предложение по-русски>"}'
)


async def judge_chat(llm: LLMPort, persona: str, transcript: list[dict], branch_id: int) -> dict:
    """Оценка одного разговора. Пустой словарь при любом сбое — раунд не должен падать
    из-за судьи, счётчики всё равно посчитаны."""
    if not transcript:
        return {}
    lines = "\n".join(
        f"{'ADMIN' if m['who'] == 'stepan' else 'LEAD'}: {m['text']}" for m in transcript)
    dims = "\n".join(f"- {k}: {v}" for k, v in DIMENSIONS.items())
    user = f"ИЗМЕРЕНИЯ:\n{dims}\n\nПЕРСОНА ЛИДА: {persona}\n\nРАСШИФРОВКА:\n{lines}"
    try:
        raw, _meta = await asyncio.wait_for(
            llm.chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                capability=SMART, require_json_schema=True,
                workflow="sim_judge", thread_id=None, branch_id=branch_id),
            timeout=_TIMEOUT_S)
        return _parse(raw)
    except Exception as exc:  # noqa: BLE001 — судья не обязателен, счётчики важнее
        logger.warning("судья недоступен persona=%s: %s", persona, exc)
        return {}


def _parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    scores = data.get("scores")
    clean = {}
    if isinstance(scores, dict):
        for key in DIMENSIONS:
            v = scores.get(key)
            clean[key] = v if isinstance(v, int) and 0 <= v <= 2 else None
    return {
        "scores": clean,
        "worst": str(data.get("worst") or "")[:300],
        "worst_why": str(data.get("worst_why") or "")[:300],
        "best": str(data.get("best") or "")[:300],
        "fix": str(data.get("fix") or "")[:300],
    }


def average_scores(judged: list[dict]) -> dict[str, float | None]:
    """Среднее по измерению, считая только те разговоры, где оно проверялось."""
    out: dict[str, float | None] = {}
    for key in DIMENSIONS:
        vals = [j["scores"][key] for j in judged
                if j.get("scores", {}).get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 2) if vals else None
    return out
