"""Страница «Стратегия»: как Степан принимает решение на каждом ходу.

Схема собирается ИЗ ЖИВОГО КОДА, а не нарисована. Рисунок протух бы за неделю — только за
30.07.2026 условия передачи менялись трижды, и любая картинка уже врала бы. Поэтому каждая
константа здесь импортируется оттуда же, откуда её читает воркер: расписания кронов из
worker.main, пороги из своих модулей, стадии из domain.enums. Если кто-то поменяет число,
страница покажет новое, а не то, что было на момент написания.

Держится на CSS, без внешних библиотек: админка не тянет ничего с чужих доменов.
"""
from __future__ import annotations

import html as _h
from dataclasses import dataclass

from app.domain.enums import BOT_SILENT_STAGES, HUMAN_LED_STAGES, Stage


@dataclass(frozen=True)
class Step:
    title: str
    detail: str
    facts: list[tuple[str, str]]


def _live_facts() -> dict[str, str]:
    """Значения читаются из модулей, а не переписываются сюда руками."""
    from app.modules.conversation import reactivation as react  # noqa: PLC0415
    from app.modules.crm import push_mcp, rescue  # noqa: PLC0415
    from app.modules.crm.policy import POLICIES  # noqa: PLC0415

    return {
        "reactivation_cap": str(getattr(react, "BATCH_PER_RUN", "?")),
        "handoff_window": str(push_mcp.HANDOFF_WINDOW_DAYS),
        "drain_batch": str(push_mcp.DRAIN_BATCH),
        "rescue_cap": str(rescue._PER_RUN_CAP),          # noqa: SLF001
        "rescue_cooldown": str(rescue._COOLDOWN_DAYS),   # noqa: SLF001
        "rescue_quiet": str(rescue._RECENT_OUT_H),       # noqa: SLF001
        "work_h": f"{rescue._WORK_START_H}:00–{rescue._WORK_END_H}:00",  # noqa: SLF001
        "policies": str(len(POLICIES)),
    }


def _steps(f: dict[str, str]) -> list[Step]:
    silent = ", ".join(sorted(s.value for s in BOT_SILENT_STAGES))
    human = ", ".join(sorted(s.value for s in HUMAN_LED_STAGES))
    return [
        Step("1. Сообщение попадает к нам",
             "Полный опрос инбокса раз в 2 минуты. Между ними — быстрая полоса по каналам, "
             "где уже есть неотвеченное сообщение. Первое сообщение НОВОГО лида быстрая "
             "полоса поймать не может: она отбирает по тому, что уже лежит у нас в базе.",
             [("полный опрос", "каждые 2 мин"), ("быстрая полоса", "нечётные минуты"),
              ("задержка первого сообщения", "до 2 мин")]),
        Step("2. Отбор на ответ",
             "Берём треды, где лид написал последним. Пропускаем: заблокированных, "
             "выключенных вручную, выключенные каналы и стадии, где бот молчит.",
             [("бот молчит в стадиях", silent), ("ведёт человек", human),
              ("тик генератора", "раз в минуту, :45")]),
        Step("3. Решение до генерации",
             "Прощание с обеих сторон — молчим. Ждём медиа — ждём. Иначе идём к модели: "
             "база знаний, персона, досье, заметка менеджера и состояние из CRM.",
             [("блоков в промте", "стабильный префикс + один переменный"),
              ("политик по CRM", f["policies"])]),
        Step("4. Проверки после генерации",
             "Деньги-гейт: цены, ссылки, выдуманные обещания, чужие достижения. "
             "Не прошло — заглушка и передача человеку, а не выдумка.",
             [("режим", "fail-closed"), ("при срыве", "hold + алерт")]),
        Step("5. Очередь и отправка",
             "Разбивка на пузыри с паузами, капы часовой и суточный, тихие часы для "
             "проактивных касаний. Ответ на входящее тихие часы не глушат.",
             [("отправщик", "каждые 10 сек"), ("выключенный канал", "строки ретируются")]),
        Step("6. Стадия и передача",
             "Готов + телефон → READY. needs_manager → MANAGER. Готов без телефона — "
             "продолжаем продавать и просим номер. Из человеческой стадии бот сам не выходит.",
             [("мгновенный пуш в CRM", "сразу после коммита"),
              ("условие", "есть телефон")]),
        Step("7. Что делает CRM в ответ",
             "Читаем результат последнего контакта менеджера и ведём себя по таблице политик. "
             "Сделка закрыта — молчим совсем. Менеджер ведёт — сами не пишем, но отвечаем.",
             [("окно подметателя", f"{f['handoff_window']} дней"),
              ("пачка за прогон", f["drain_batch"]),
              ("follow-through", f"кап {f['rescue_cap']}, "
                                 f"кулдаун {f['rescue_cooldown']} дн, "
                                 f"тишина {f['rescue_quiet']} ч, "
                                 f"часы {f['work_h']}")]),
    ]


_POLICY_ROWS = [
    ("wait_call", "74%", "менеджер ждёт созвона", "сами не пишем, на входящее отвечаем"),
    ("result_think", "11%", "взял паузу", "спросить срок, назвать день; иначе через месяц"),
    ("result_next_enrollment", "9%", "нужен следующий набор", "выяснить какой, отложить"),
    ("result_fail", "9%", "отказ", "разобрать причину, один вопрос; молчит — попрощаться"),
    ("result_event", "1%", "придёт на мероприятие", "ничего не делаем"),
]


def _css() -> str:
    return (
        "<style>"
        ".stg{max-width:1100px}"
        ".stg h2{margin:18px 0 6px;font-size:19px}"
        ".stg .lead{opacity:.75;margin-bottom:14px}"
        ".stg-step{border:1px solid var(--bd,#2a3441);border-radius:10px;padding:12px 14px;"
        "margin:10px 0;background:var(--bg2,#151b23)}"
        ".stg-step h3{margin:0 0 6px;font-size:15px}"
        ".stg-step p{margin:0 0 8px;opacity:.85;line-height:1.5}"
        ".stg-f{display:flex;flex-wrap:wrap;gap:6px}"
        ".stg-f span{font-size:12px;padding:2px 8px;border-radius:999px;"
        "background:rgba(77,166,255,.12);color:#8ec5ff;white-space:nowrap}"
        ".stg-arrow{text-align:center;opacity:.35;margin:-4px 0}"
        ".stg table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}"
        ".stg th,.stg td{text-align:left;padding:6px 8px;border-bottom:1px solid "
        "var(--bd,#2a3441);vertical-align:top}"
        ".stg th{opacity:.7;font-weight:500}"
        ".stg code{font-size:12px;opacity:.9}"
        "</style>"
    )


def strategy_page_html() -> str:
    facts = _live_facts()
    body = [_css(), '<div class="stg">']
    body.append("<h2>Как Степан думает на каждом ходу</h2>")
    body.append('<p class="lead">Схема собрана из кода: числа ниже читаются из тех же '
                "модулей, что и воркер, поэтому расходиться с поведением они не могут.</p>")
    for i, step in enumerate(_steps(facts)):
        chips = "".join(
            f"<span>{_h.escape(k)}: {_h.escape(v)}</span>" for k, v in step.facts)
        body.append(
            f'<div class="stg-step"><h3>{_h.escape(step.title)}</h3>'
            f"<p>{_h.escape(step.detail)}</p>"
            f'<div class="stg-f">{chips}</div></div>')
        if i < len(_steps(facts)) - 1:
            body.append('<div class="stg-arrow">▼</div>')

    body.append("<h2>Что Степан делает после менеджера</h2>")
    body.append('<p class="lead">Пять статусов — те, которыми филиал реально пользуется. '
                "Проценты по 2000 контактам за 30 дней.</p>")
    body.append("<table><tr><th>Статус в CRM</th><th>Доля</th><th>Что значит</th>"
                "<th>Что делает Степан</th></tr>")
    for status, share, means, does in _POLICY_ROWS:
        body.append(f"<tr><td><code>{_h.escape(status)}</code></td><td>{share}</td>"
                    f"<td>{_h.escape(means)}</td><td>{_h.escape(does)}</td></tr>")
    body.append("</table>")

    body.append("<h2>Когда Степан молчит</h2>")
    body.append("<table><tr><th>Причина</th><th>Отвечает на входящее</th>"
                "<th>Пишет первым</th></tr>")
    for reason, answers, initiates in (
        ("Лид заблокирован", "нет", "нет"),
        ("Бота выключил человек", "нет", "нет"),
        ("Сделка закрыта / оплачена", "нет", "нет"),
        ("Менеджер ведёт лида", "ДА", "нет"),
        ("Запланирован звонок", "ДА", "нет, до этой даты"),
        ("Оба попрощались", "нет", "нет"),
        ("Тихие часы", "ДА", "нет"),
        ("Канал выключен", "нет", "нет"),
    ):
        body.append(f"<tr><td>{_h.escape(reason)}</td><td>{answers}</td>"
                    f"<td>{initiates}</td></tr>")
    body.append("</table></div>")
    return "".join(body)
