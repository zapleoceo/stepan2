"""Страница «Стратегия»: блок-схема того, как Степан принимает решение на каждом ходу.

Схема собирается ИЗ ЖИВОГО КОДА, а не нарисована. Рисунок протух бы за неделю — только за
30.07.2026 условия передачи менялись трижды, и любая картинка уже врала бы. Пороги,
расписания и списки стадий импортируются оттуда же, откуда их читает воркер.

Рисуется инлайновым SVG: админка ничего не тянет с чужих доменов, а схема должна одинаково
работать и в тёмной теме, и в печати. Тексты идут через t(), поэтому язык страницы совпадает
с языком интерфейса.
"""
from __future__ import annotations

import html as _h

from app.api._i18n import t
from app.domain.enums import BOT_SILENT_STAGES, HUMAN_LED_STAGES

_W, _BOX_H, _GAP = 300, 62, 30
_DEC_H = 74


def _live() -> dict[str, str]:
    from app.modules.conversation import reactivation as react  # noqa: PLC0415
    from app.modules.crm import push_mcp, rescue  # noqa: PLC0415

    return {
        "react_batch": str(react.BATCH_PER_RUN),
        "window": str(push_mcp.HANDOFF_WINDOW_DAYS),
        "batch": str(push_mcp.DRAIN_BATCH),
        "cap": str(rescue._PER_RUN_CAP),           # noqa: SLF001
        "cooldown": str(rescue._COOLDOWN_DAYS),    # noqa: SLF001
        "quiet": str(rescue._RECENT_OUT_H),        # noqa: SLF001
        "work": f"{rescue._WORK_START_H}:00–{rescue._WORK_END_H}:00",  # noqa: SLF001
    }


def _box(x: int, y: int, key: str, note: str, kind: str = "step") -> str:
    """Прямоугольник шага. kind меняет только цвет рамки."""
    label = _h.escape(t(key))
    sub = _h.escape(note)
    return (
        f'<g class="n n-{kind}">'
        f'<rect x="{x}" y="{y}" width="{_W}" height="{_BOX_H}" rx="9"/>'
        f'<text x="{x + 14}" y="{y + 25}" class="t1">{label}</text>'
        f'<text x="{x + 14}" y="{y + 45}" class="t2">{sub}</text>'
        f"</g>"
    )


def _diamond(x: int, y: int, key: str) -> str:
    cx, cy = x + _W / 2, y + _DEC_H / 2
    pts = f"{cx},{y} {x + _W},{cy} {cx},{y + _DEC_H} {x},{cy}"
    return (
        f'<g class="n n-dec"><polygon points="{pts}"/>'
        f'<text x="{cx}" y="{cy + 5}" class="t1 mid">{_h.escape(t(key))}</text></g>'
    )


def _arrow(x: int, y1: int, y2: int, label: str = "") -> str:
    mid = (y1 + y2) / 2
    lbl = (f'<text x="{x + 8}" y="{mid + 4}" class="t3">{_h.escape(label)}</text>'
           if label else "")
    return (f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2 - 8}" class="ar"/>'
            f'<polygon points="{x - 5},{y2 - 8} {x + 5},{y2 - 8} {x},{y2}" class="arh"/>'
            f"{lbl}")


def _elbow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    """Ветка вбок: горизонталь, затем стрелка вниз."""
    lbl = (f'<text x="{(x1 + x2) / 2}" y="{y1 - 8}" class="t3 mid">{_h.escape(label)}</text>'
           if label else "")
    return (f'<path d="M{x1},{y1} H{x2} V{y2 - 8}" class="ar"/>'
            f'<polygon points="{x2 - 5},{y2 - 8} {x2 + 5},{y2 - 8} {x2},{y2}" class="arh"/>'
            f"{lbl}")


def _flow(f: dict[str, str]) -> str:
    """Основная вертикаль хода + боковые выходы, где Степан замолкает."""
    lx, rx = 40, 400          # левая колонка — путь, правая — тупики
    y = 20
    parts: list[str] = []
    rows: list[tuple[str, str, str]] = [
        ("stg.in", f'{t("stg.in.n")}', "step"),
        ("stg.pick", f'{t("stg.pick.n")}', "step"),
        ("stg.gen", f'{t("stg.gen.n")}', "step"),
        ("stg.guard", f'{t("stg.guard.n")}', "step"),
        ("stg.queue", f'{t("stg.queue.n")}', "step"),
        ("stg.stage", f'{t("stg.stage.n")}', "step"),
        ("stg.crm", f'{t("stg.crm.n")}', "crm"),
    ]
    exits = {
        1: ("stg.x.block", ""),
        2: ("stg.x.bye", ""),
        3: ("stg.x.hold", ""),
        4: ("stg.x.quiet", ""),
    }
    for i, (key, note, kind) in enumerate(rows):
        if i:
            parts.append(_arrow(lx + _W // 2, y - _GAP, y))
        parts.append(_box(lx, y, key, note, kind))
        if i in exits:
            ek, en = exits[i]
            parts.append(_elbow(lx + _W, y + _BOX_H // 2, rx + _W // 2, y))
            parts.append(_box(rx, y, ek, en, "stop"))
        y += _BOX_H + _GAP
    height = y
    return (f'<svg viewBox="0 0 {rx + _W + 40} {height}" class="stg-svg" '
            f'role="img" aria-label="{_h.escape(t("stg.title"))}">'
            + "".join(parts) + "</svg>")


_POLICY_KEYS = [
    ("wait_call", "74%"),
    ("result_think", "11%"),
    ("result_next_enrollment", "9%"),
    ("result_fail", "9%"),
    ("result_event", "1%"),
]

_SILENCE_KEYS = [
    ("stg.s.blocked", False, False),
    ("stg.s.manual", False, False),
    ("stg.s.won", False, False),
    ("stg.s.manager", True, False),
    ("stg.s.planned", True, False),
    ("stg.s.bye", False, False),
    ("stg.s.quiet", True, False),
    ("stg.s.channel", False, False),
]


def _css() -> str:
    return (
        "<style>"
        ".stg{max-width:1100px}"
        ".stg h2{margin:20px 0 4px;font-size:19px}"
        ".stg .lead{opacity:.7;margin:0 0 12px;line-height:1.5}"
        ".stg-svg{width:100%;height:auto;margin:6px 0 4px}"
        ".stg-svg .n rect,.stg-svg .n polygon{fill:var(--bg2,#151b23);"
        "stroke:#3a4757;stroke-width:1.5}"
        ".stg-svg .n-crm rect{stroke:#4dabf7}"
        ".stg-svg .n-stop rect{stroke:#7a5560;stroke-dasharray:5 4}"
        ".stg-svg .n-dec polygon{stroke:#c9a227}"
        ".stg-svg .t1{fill:var(--fg,#e8eef4);font:600 13px system-ui,sans-serif}"
        ".stg-svg .t2{fill:var(--fg,#e8eef4);opacity:.6;font:12px system-ui,sans-serif}"
        ".stg-svg .t3{fill:#8ec5ff;font:11px system-ui,sans-serif}"
        ".stg-svg .mid{text-anchor:middle}"
        ".stg-svg .ar{stroke:#4a5768;stroke-width:1.5;fill:none}"
        ".stg-svg .arh{fill:#4a5768}"
        ".stg table{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}"
        ".stg th,.stg td{text-align:left;padding:6px 8px;"
        "border-bottom:1px solid var(--bd,#2a3441);vertical-align:top}"
        ".stg th{opacity:.65;font-weight:500}"
        ".stg code{font-size:12px;opacity:.9}"
        ".stg .yes{color:#51cf66}.stg .no{opacity:.45}"
        ".stg .chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}"
        ".stg .chips span{font-size:12px;padding:2px 9px;border-radius:999px;"
        "background:rgba(77,166,255,.12);color:#8ec5ff}"
        "</style>"
    )


def strategy_page_html() -> str:
    f = _live()
    out = [_css(), '<div class="stg">']
    out.append(f"<h2>{_h.escape(t('stg.title'))}</h2>")
    out.append(f'<p class="lead">{_h.escape(t("stg.sub"))}</p>')
    out.append(_flow(f))

    chips = [
        (t("stg.k.window"), f'{f["window"]}'),
        (t("stg.k.batch"), f["batch"]),
        (t("stg.k.react"), f["react_batch"]),
        (t("stg.k.cap"), f["cap"]),
        (t("stg.k.cooldown"), f["cooldown"]),
        (t("stg.k.quiet"), f["quiet"]),
        (t("stg.k.work"), f["work"]),
    ]
    out.append('<div class="chips">' + "".join(
        f"<span>{_h.escape(k)}: {_h.escape(v)}</span>" for k, v in chips) + "</div>")

    out.append(f"<h2>{_h.escape(t('stg.pol.title'))}</h2>")
    out.append(f'<p class="lead">{_h.escape(t("stg.pol.sub"))}</p>')
    out.append(f"<table><tr><th>{_h.escape(t('stg.pol.status'))}</th>"
               f"<th>{_h.escape(t('stg.pol.share'))}</th>"
               f"<th>{_h.escape(t('stg.pol.means'))}</th>"
               f"<th>{_h.escape(t('stg.pol.does'))}</th></tr>")
    for status, share in _POLICY_KEYS:
        out.append(f"<tr><td><code>{status}</code></td><td>{share}</td>"
                   f"<td>{_h.escape(t(f'stg.pol.{status}.m'))}</td>"
                   f"<td>{_h.escape(t(f'stg.pol.{status}.d'))}</td></tr>")
    out.append("</table>")

    out.append(f"<h2>{_h.escape(t('stg.sil.title'))}</h2>")
    out.append(f'<p class="lead">{_h.escape(t("stg.sil.sub"))}</p>')
    out.append(f"<table><tr><th>{_h.escape(t('stg.sil.reason'))}</th>"
               f"<th>{_h.escape(t('stg.sil.answers'))}</th>"
               f"<th>{_h.escape(t('stg.sil.starts'))}</th></tr>")
    yes, no = t("stg.yes"), t("stg.no")
    for key, answers, starts in _SILENCE_KEYS:
        a = f'<span class="yes">{_h.escape(yes)}</span>' if answers else \
            f'<span class="no">{_h.escape(no)}</span>'
        s = f'<span class="yes">{_h.escape(yes)}</span>' if starts else \
            f'<span class="no">{_h.escape(no)}</span>'
        out.append(f"<tr><td>{_h.escape(t(key))}</td><td>{a}</td><td>{s}</td></tr>")
    out.append("</table>")

    silent = ", ".join(sorted(s.value for s in BOT_SILENT_STAGES))
    human = ", ".join(sorted(s.value for s in HUMAN_LED_STAGES))
    out.append(f'<p class="lead">{_h.escape(t("stg.stages"))} '
               f"<code>{_h.escape(silent)}</code> · <code>{_h.escape(human)}</code></p>")
    out.append("</div>")
    return "".join(out)
