"""Страница «Стратегия»: детальная блок-схема одного хода Степана.

Собирается ИЗ ЖИВОГО КОДА, а не рисуется: пороги, расписания и списки стадий импортируются
оттуда же, откуда их читает воркер. Только за 30.07.2026 условия передачи менялись трижды —
нарисованная картинка врала бы уже к вечеру.

Форма — ромб-проверка с двумя подписанными выходами: «да» уводит вбок в терминальный узел
(что происходит и почему), «нет» ведёт вниз к следующей проверке. Это ровно та форма, которую
имеет сам код: последовательность гардов, каждый из которых может прервать ход.

Инлайновый SVG + немного ванильного JS на зум и панорамирование: админка ничего не тянет с
чужих доменов. Тексты идут через t(), поэтому язык страницы совпадает с языком интерфейса.
"""
from __future__ import annotations

import html as _h

from app.api._i18n import t
from app.domain.enums import BOT_SILENT_STAGES, HUMAN_LED_STAGES

# Геометрия. Числа подобраны так, чтобы схема читалась и на ноутбуке, и в зуме.
_DX, _DY = 340, 96          # шаг ромбов по вертикали
_DW, _DH = 300, 76          # ромб
_TW, _TH = 300, 66          # терминал справа
_RIGHT = 470                # колонка терминалов


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


# Последовательность проверок ровно та, что в коде: ingest → отбор → гарды → генерация →
# money-gate → стадия → очередь. key — вопрос, yes — что будет, если ДА.
_CHECKS: list[tuple[str, str, str]] = [
    ("stg.q.blocked", "stg.t.blocked", "stop"),
    ("stg.q.channel", "stg.t.channel", "stop"),
    ("stg.q.agent", "stg.t.agent", "stop"),
    ("stg.q.silent", "stg.t.silent", "stop"),
    ("stg.q.pending", "stg.t.pending", "wait"),
    ("stg.q.bye", "stg.t.bye", "stop"),
    ("stg.q.media", "stg.t.media", "wait"),
    ("stg.q.won", "stg.t.won", "stop"),
    ("stg.q.owner", "stg.t.owner", "half"),
    ("stg.q.guard", "stg.t.guard", "hand"),
    ("stg.q.ready", "stg.t.ready", "hand"),
    ("stg.q.manager", "stg.t.manager", "hand"),
]


def _txt(x: float, y: float, s: str, cls: str) -> str:
    return f'<text x="{x:.0f}" y="{y:.0f}" class="{cls}">{_h.escape(s)}</text>'


def _wrap(s: str, width: int = 42) -> list[str]:
    words, line, out = s.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out[:3]


def _diamond(x: int, y: int, key: str) -> str:
    cx, cy = x + _DW / 2, y + _DH / 2
    pts = f"{cx},{y} {x + _DW},{cy} {cx},{y + _DH} {x},{cy}"
    lines = _wrap(t(key), 34)
    dy = cy - (len(lines) - 1) * 7
    body = "".join(_txt(cx, dy + i * 14 + 4, ln, "t1 mid") for i, ln in enumerate(lines))
    return f'<g class="n n-dec"><polygon points="{pts}"/>{body}</g>'


def _term(x: int, y: int, key: str, kind: str) -> str:
    lines = _wrap(t(key), 40)
    dy = y + _TH / 2 - (len(lines) - 1) * 8 + 4
    body = "".join(_txt(x + 14, dy + i * 15, ln, "t2") for i, ln in enumerate(lines))
    return (f'<g class="n n-{kind}"><rect x="{x}" y="{y}" width="{_TW}" height="{_TH}" '
            f'rx="9"/>{body}</g>')


def _start(x: int, y: int, key: str, kind: str = "start") -> str:
    return (f'<g class="n n-{kind}"><rect x="{x}" y="{y}" width="{_DW}" height="52" '
            f'rx="26"/>{_txt(x + _DW / 2, y + 31, t(key), "t1 mid")}</g>')


def _down(x: float, y1: float, y2: float, label: str) -> str:
    return (f'<line x1="{x:.0f}" y1="{y1:.0f}" x2="{x:.0f}" y2="{y2 - 9:.0f}" class="ar"/>'
            f'<polygon points="{x - 5:.0f},{y2 - 9:.0f} {x + 5:.0f},{y2 - 9:.0f} '
            f'{x:.0f},{y2:.0f}" class="arh"/>'
            f'{_txt(x + 9, (y1 + y2) / 2 + 4, label, "t3")}')


def _side(x1: float, y: float, x2: float, label: str) -> str:
    return (f'<line x1="{x1:.0f}" y1="{y:.0f}" x2="{x2 - 9:.0f}" y2="{y:.0f}" class="ar"/>'
            f'<polygon points="{x2 - 9:.0f},{y - 5:.0f} {x2 - 9:.0f},{y + 5:.0f} '
            f'{x2:.0f},{y:.0f}" class="arh"/>'
            f'{_txt((x1 + x2) / 2, y - 9, label, "t3 mid")}')


def _flow() -> str:
    yes, no = t("stg.yes"), t("stg.no")
    parts = [_start(_DX - _DW // 2, 16, "stg.q.start")]
    y = 16 + 52 + 44
    cx = _DX
    for key, term_key, kind in _CHECKS:
        parts.append(_down(cx, y - 44, y, ""))
        parts.append(_diamond(_DX - _DW // 2, y, key))
        mid = y + _DH / 2
        parts.append(_side(_DX - _DW // 2 + _DW, mid, _RIGHT, yes))
        parts.append(_term(_RIGHT, int(mid - _TH / 2), term_key, kind))
        y += _DY + _DH - 40
        parts.append(_down(cx, mid + _DH / 2, y, no))
    parts.append(_start(_DX - _DW // 2, int(y), "stg.q.send", "go"))
    height = int(y) + 90
    width = _RIGHT + _TW + 40
    return (f'<svg id="stg-svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" role="img" '
            f'aria-label="{_h.escape(t("stg.title"))}">' + "".join(parts) + "</svg>")


_POLICY_KEYS = [("wait_call", "74%"), ("result_think", "11%"),
                ("result_next_enrollment", "9%"), ("result_fail", "9%"),
                ("result_event", "1%")]


def _css() -> str:
    return (
        "<style>"
        ".stg{max-width:1180px}"
        ".stg h2{margin:20px 0 4px;font-size:19px}"
        ".stg .lead{opacity:.7;margin:0 0 10px;line-height:1.5}"
        ".stg-wrap{position:relative;border:1px solid var(--bd,#2a3441);border-radius:10px;"
        "overflow:auto;max-height:74vh;background:var(--bg2,#151b23);cursor:grab}"
        ".stg-wrap.drag{cursor:grabbing}"
        ".stg-zoom{position:sticky;top:8px;left:8px;z-index:2;display:inline-flex;gap:4px;"
        "margin:8px}"
        ".stg-zoom button{width:30px;height:30px;border-radius:7px;border:1px solid "
        "var(--bd,#2a3441);background:var(--bg,#0f141a);color:var(--fg,#e8eef4);"
        "cursor:pointer;font-size:15px;line-height:1}"
        ".stg-zoom button:hover{border-color:#4dabf7}"
        "#stg-svg{transform-origin:0 0;display:block}"
        ".stg .n rect,.stg .n polygon{fill:var(--bg,#0f141a);stroke:#3a4757;stroke-width:1.5}"
        ".stg .n-dec polygon{stroke:#c9a227}"
        ".stg .n-stop rect{stroke:#e0698a}"
        ".stg .n-wait rect{stroke:#c9a227;stroke-dasharray:5 4}"
        ".stg .n-half rect{stroke:#4dabf7}"
        ".stg .n-hand rect{stroke:#51cf66}"
        ".stg .n-start rect{stroke:#6c7a8c}"
        ".stg .n-go rect{stroke:#51cf66;stroke-width:2}"
        ".stg .t1{fill:var(--fg,#e8eef4);font:600 12.5px system-ui,sans-serif}"
        ".stg .t2{fill:var(--fg,#e8eef4);opacity:.75;font:12px system-ui,sans-serif}"
        ".stg .t3{fill:#8ec5ff;font:11px system-ui,sans-serif}"
        ".stg .mid{text-anchor:middle}"
        ".stg .ar{stroke:#4a5768;stroke-width:1.5}"
        ".stg .arh{fill:#4a5768}"
        ".stg table{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}"
        ".stg th,.stg td{text-align:left;padding:6px 8px;"
        "border-bottom:1px solid var(--bd,#2a3441);vertical-align:top}"
        ".stg th{opacity:.65;font-weight:500}"
        ".stg code{font-size:12px;opacity:.9}"
        ".stg .chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 2px}"
        ".stg .chips span{font-size:12px;padding:2px 9px;border-radius:999px;"
        "background:rgba(77,166,255,.12);color:#8ec5ff}"
        "</style>"
    )


def _zoom_js() -> str:
    """Зум колесом и перетаскивание. Ванильный JS: ни одной внешней библиотеки."""
    return (
        "<script>(function(){"
        "var w=document.getElementById('stg-wrap'),s=document.getElementById('stg-svg');"
        "if(!w||!s||w.dataset.on)return;w.dataset.on='1';var z=1;"
        "function ap(){s.style.transform='scale('+z+')';"
        "s.style.width=(s.getAttribute('width')*z)+'px';"
        "s.style.height=(s.getAttribute('height')*z)+'px';}"
        "function set(v){z=Math.min(2.5,Math.max(0.4,v));ap();}"
        "w.addEventListener('wheel',function(e){if(!e.ctrlKey&&!e.metaKey)return;"
        "e.preventDefault();set(z*(e.deltaY<0?1.1:0.9));},{passive:false});"
        "var d=false,sx=0,sy=0,l=0,tp=0;"
        "w.addEventListener('mousedown',function(e){d=true;w.classList.add('drag');"
        "sx=e.clientX;sy=e.clientY;l=w.scrollLeft;tp=w.scrollTop;});"
        "document.addEventListener('mouseup',function(){d=false;w.classList.remove('drag');});"
        "w.addEventListener('mousemove',function(e){if(!d)return;e.preventDefault();"
        "w.scrollLeft=l-(e.clientX-sx);w.scrollTop=tp-(e.clientY-sy);});"
        "var b=w.querySelectorAll('.stg-zoom button');"
        "b[0].onclick=function(){set(z*1.2);};b[1].onclick=function(){set(z*0.83);};"
        "b[2].onclick=function(){set(1);w.scrollTop=0;w.scrollLeft=0;};"
        "})();</script>"
    )


def strategy_page_html() -> str:
    f = _live()
    out = [_css(), '<div class="stg">']
    out.append(f"<h2>{_h.escape(t('stg.title'))}</h2>")
    out.append(f'<p class="lead">{_h.escape(t("stg.sub"))}</p>')
    out.append('<div class="stg-wrap" id="stg-wrap">'
               '<div class="stg-zoom"><button type="button" title="+">+</button>'
               '<button type="button" title="−">−</button>'
               '<button type="button" title="1:1">⤢</button></div>')
    out.append(_flow())
    out.append("</div>")
    out.append(f'<p class="lead">{_h.escape(t("stg.hint"))}</p>')

    chips = [
        (t("stg.k.window"), f["window"]), (t("stg.k.batch"), f["batch"]),
        (t("stg.k.react"), f["react_batch"]), (t("stg.k.cap"), f["cap"]),
        (t("stg.k.cooldown"), f["cooldown"]), (t("stg.k.quiet"), f["quiet"]),
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

    silent = ", ".join(sorted(s.value for s in BOT_SILENT_STAGES))
    human = ", ".join(sorted(s.value for s in HUMAN_LED_STAGES))
    out.append(f'<p class="lead">{_h.escape(t("stg.stages"))} '
               f"<code>{_h.escape(silent)}</code> · <code>{_h.escape(human)}</code></p>")
    out.append("</div>")
    out.append(_zoom_js())
    return "".join(out)
