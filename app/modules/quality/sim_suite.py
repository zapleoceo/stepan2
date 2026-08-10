"""Раунд симуляции продаж: десять персон, замер, отчёт, сравнение с прошлым раундом.

Запуск в контейнере (ТОЛЬКО филиал 8 — см. docs/dialogue-qa-checklist.md):

    python -m app.modules.quality.sim_suite --round r1 --turns 8 --judge

Каждый раунд пишет sim_runs/<round>.json. Отчёт печатается в stdout и кладётся рядом
в <round>.md. Персоны идут параллельно, потому что раунд из десяти по восемь ходов
последовательно — это больше часа, а параллельно около десяти минут.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.sim import SimService
from app.modules.conversation.sim_persona import PERSONAS, run_persona
from app.modules.quality.personas_live import LIVE_PERSONAS
from app.modules.quality.sim_judge import DIMENSIONS, average_scores, judge_chat
from app.modules.quality.sim_score import ChatScore, score_chat, summarize

logger = logging.getLogger(__name__)

SANDBOX_BRANCH = 8  # ClodeCouch. НИКОГДА не 1 — боевая Индонезия.
OUT_DIR = Path("sim_runs")
_CONCURRENCY = 4  # выше брокер начинает отдавать 503 no-provider


def _register_live() -> None:
    PERSONAS.update(LIVE_PERSONAS)


async def _one(key: str, round_id: str, turns: int, sem: asyncio.Semaphore) -> tuple[str, dict]:
    session_key = f"suite:{round_id}:{key}"
    async with sem:
        try:
            async with session_scope() as s:
                await SimService(s, BrokerLLM()).reset(SANDBOX_BRANCH, session_key)
            async with session_scope() as s:
                run = await run_persona(
                    s, SANDBOX_BRANCH, key, session_key, BrokerLLM(), max_turns=turns)
        except Exception as exc:  # noqa: BLE001 — одна упавшая персона не роняет раунд
            logger.exception("персона %s упала", key)
            return key, {"ok": False, "detail": str(exc), "transcript": []}
    return key, run


async def run_round(round_id: str, keys: list[str], turns: int, judge: bool) -> dict:
    _register_live()
    sem = asyncio.Semaphore(_CONCURRENCY)
    runs = dict(await asyncio.gather(*(_one(k, round_id, turns, sem) for k in keys)))

    scores: list[ChatScore] = [score_chat(k, r) for k, r in runs.items() if r.get("transcript")]
    judged: list[dict] = []
    if judge:
        llm = BrokerLLM()
        jsem = asyncio.Semaphore(_CONCURRENCY)

        async def _j(key: str, run: dict) -> dict:
            async with jsem:
                out = await judge_chat(llm, key, run.get("transcript", []), SANDBOX_BRANCH)
            return {"persona": key, **out} if out else {}

        judged = [j for j in await asyncio.gather(
            *(_j(k, r) for k, r in runs.items() if r.get("transcript"))) if j]

    return {
        "round": round_id,
        "personas": keys,
        "turns_cap": turns,
        "metrics": summarize(scores),
        "judge": average_scores(judged) if judged else {},
        "chats": [s.as_dict() for s in scores],
        "verdicts": judged,
        "transcripts": {k: r.get("transcript", []) for k, r in runs.items()},
        "failed": [k for k, r in runs.items() if not r.get("transcript")],
    }


async def score_existing(round_id: str, judge: bool = False) -> dict:
    """Пересчитать раунд по расшифровкам, уже лежащим в базе.

    Прогон живёт в контейнере, а деплой контейнер перезапускает — так раунд r1 умер на
    середине вместе со своим логом. Расшифровки при этом никуда не делись: sim пишет их в
    обычные треды филиала 8. Значит замер можно повторить когда угодно и бесплатно, а заодно
    пересчитать старый раунд новым счётчиком, если счётчик поправили.

    ВАЖНО про единицы: в базе каждый пузырь лежит ОТДЕЛЬНЫМ сообщением, поэтому здесь
    avg_chars меряет сообщение, а не ход, и avg_bubbles всегда 1.0. У живого прогона наоборот:
    он видит сырую строку с '|||' и меряет ход целиком. Числа двух режимов сравнивать между
    собой нельзя — у r4 вышло 253.4 в пересчёте против 277.7 вживую при 1.09 пузыря, и это
    одна и та же величина, просто поделённая по-разному."""
    from sqlalchemy import text as sql  # noqa: PLC0415

    prefix = f"sim:suite:{round_id}:"
    async with session_scope() as s:
        rows = (await s.execute(sql(
            "SELECT ct.external_thread_id, m.direction, m.text FROM message m"
            " JOIN channel_thread ct ON ct.id = m.thread_id"
            " WHERE ct.external_thread_id LIKE :p AND m.text <> ''"
            " ORDER BY ct.external_thread_id, m.occurred_at, m.id"), {"p": f"{prefix}%"})).all()
    runs: dict[str, dict] = {}
    for ext, direction, text_ in rows:
        key = ext[len(prefix):]
        runs.setdefault(key, {"transcript": []})["transcript"].append(
            {"who": "stepan" if direction == "out" else "lead", "text": text_})

    scores = [score_chat(k, r) for k, r in runs.items()]
    judged: list[dict] = []
    if judge:
        llm = BrokerLLM()
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _j(key: str, run: dict) -> dict:
            async with sem:
                out = await judge_chat(llm, key, run["transcript"], SANDBOX_BRANCH)
            return {"persona": key, **out} if out else {}

        judged = [j for j in await asyncio.gather(*(_j(k, r) for k, r in runs.items())) if j]
    return {
        "round": round_id, "personas": sorted(runs), "turns_cap": None,
        "metrics": summarize(scores), "judge": average_scores(judged) if judged else {},
        "chats": [s.as_dict() for s in scores], "verdicts": judged,
        "transcripts": {k: r["transcript"] for k, r in runs.items()}, "failed": [],
    }


def report(result: dict, previous: dict | None) -> str:
    """Отчёт раунда. Со стрелками к прошлому раунду, потому что смотрят именно на разницу."""
    m, prev = result["metrics"], (previous or {}).get("metrics", {})

    def arrow(key: str, lower_is_better: bool = True) -> str:
        if key not in prev or prev[key] == m.get(key):
            return ""
        better = (m[key] < prev[key]) if lower_is_better else (m[key] > prev[key])
        return f"  ({prev[key]} → {m[key]}, {'лучше' if better else 'ХУЖЕ'})"

    lines = [f"# Раунд {result['round']}", ""]
    if result["failed"]:
        lines += [f"⚠️ не отработали: {', '.join(result['failed'])}", ""]
    lines += [
        "## Стиль",
        f"- длина ОДНОГО сообщения: {m.get('avg_msg_chars')}{arrow('avg_msg_chars')}",
        f"- длина всего хода: {m.get('avg_chars')}{arrow('avg_chars')}",
        f"- медиана: {m.get('median_chars')}{arrow('median_chars')}",
        f"- длиннее 400 знаков: {_pct(m.get('long_share'))}{arrow('long_share')}",
        f"- пузырей на ход: {m.get('avg_bubbles')}",
        f"- штампов: {m.get('stock_hits')}{arrow('stock_hits')} {m.get('stock_kinds') or ''}",
        f"- вёрстки «писал робот»: {m.get('robot_hits')}{arrow('robot_hits')}",
        "",
        "## Ведение",
        f"- ходов со следующим шагом: {_pct(m.get('next_step_share'))}"
        f"{arrow('next_step_share', lower_is_better=False)}",
        f"- заглушек «уточню у команды»: {m.get('stall_turns')}{arrow('stall_turns')}",
        f"- повторённых вопросов: {m.get('repeated_questions')}{arrow('repeated_questions')}",
        f"- ходов с двумя и более вопросами: {m.get('multi_question_turns')}"
        f"{arrow('multi_question_turns')}",
        f"- дошли до готовности: {m.get('ready')} · передач менеджеру: {m.get('handoffs')}",
    ]
    if result.get("judge"):
        lines += ["", "## Судья (0 сломано · 1 сойдёт · 2 хорошо)"]
        pj = (previous or {}).get("judge", {})
        for key in DIMENSIONS:
            now, was = result["judge"].get(key), pj.get(key)
            if now is None:
                continue
            delta = "" if was is None or was == now else \
                f"  ({was} → {now}, {'лучше' if now > was else 'ХУЖЕ'})"
            lines.append(f"- {key}: {now}{delta}")
    if result.get("verdicts"):
        lines += ["", "## Что чинить (по одному худшему месту на разговор)"]
        for v in sorted(result["verdicts"], key=lambda x: _worst_first(x)):
            if v.get("worst_why"):
                lines.append(f"- **{v['persona']}** — {v['worst_why']}")
                if v.get("fix"):
                    lines.append(f"  - предложение судьи: {v['fix']}")
    return "\n".join(lines)


def _worst_first(v: dict) -> float:
    vals = [x for x in (v.get("scores") or {}).values() if x is not None]
    return sum(vals) / len(vals) if vals else 9.0


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


DEFAULT_KEYS = list(LIVE_PERSONAS)


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--compare", default=None, help="id раунда для сравнения")
    ap.add_argument("--personas", default=None, help="через запятую; по умолчанию живые десять")
    ap.add_argument("--rescore", action="store_true",
                    help="не прогонять заново, а пересчитать раунд по расшифровкам из базы")
    args = ap.parse_args()

    keys = args.personas.split(",") if args.personas else DEFAULT_KEYS
    previous = _read_previous(args.compare)
    result = (await score_existing(args.round, args.judge) if args.rescore
              else await run_round(args.round, keys, args.turns, args.judge))
    text = report(result, previous)
    _write(args.round, result, text)
    print(text)  # noqa: T201 — это CLI, отчёт и есть вывод


def _read_previous(round_id: str | None) -> dict | None:
    if not round_id:
        return None
    path = OUT_DIR / f"{round_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write(round_id: str, result: dict, text: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{round_id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / f"{round_id}.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main())
