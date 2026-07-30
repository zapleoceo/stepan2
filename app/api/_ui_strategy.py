"""Страница «Стратегия»: подробные блок-схемы того, как Степан решает и что уезжает в CRM.

Собирается ИЗ ЖИВОГО КОДА, а не рисуется: пороги, расписания и списки стадий импортируются
оттуда же, откуда их читает воркер. Только за 30.07.2026 условия передачи менялись трижды —
нарисованная картинка врала бы уже к вечеру.

Форма — ромб-проверка с двумя подписанными выходами: «да» уводит вбок в терминал (что
произойдёт и почему), «нет» ведёт вниз к следующей проверке. Это та же форма, что и у кода:
цепочка гардов, каждый из которых может прервать ход.

Прокрутка: схема ТЕЧЁТ в странице и по вертикали листается вместе с ней. Вложенный контейнер
с ограниченной высотой (первая версия) съедал прокрутку страницы, и нижнюю часть было не
достать. Свой скролл остаётся только горизонтальным — там, где зум делает схему шире экрана.
"""
from __future__ import annotations

import html as _h

from app.api._i18n import t
from app.domain.enums import BOT_SILENT_STAGES, HUMAN_LED_STAGES

_DW, _DH = 300, 76          # ромб
_TW, _TH = 320, 72          # терминал
_LEFT, _RIGHT = 40, 470     # колонки
_STEP = 132                 # шаг по вертикали


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


# ТРИ фазы, а не одна цепочка, и это не косметика: они идут в РАЗНЫХ процессах и в разное
# время. Отбор — крон раз в минуту; генерация — отдельная задача со своим таймаутом;
# отправка — другой крон, каждые 10 секунд. Между фазами состояние успевает измениться,
# поэтому блокировка проверяется дважды: лид мог быть заблокирован уже после генерации.
#
# Нарисованная одной прямой, схема врала в главном: гейт CRM стоит на ОТПРАВКЕ (allow_send
# в outbox.py), а не до генерации. Значит сообщение уже сочинено и оплачено брокеру, когда
# CRM говорит «стоп» (найдено при сверке 30.07.2026).
_PICK_CHECKS: list[tuple[str, str, str]] = [
    ("stg.q.blocked", "stg.t.blocked", "stop"),
    ("stg.q.channel", "stg.t.channel", "stop"),
    ("stg.q.agent", "stg.t.agent", "stop"),
    ("stg.q.silent", "stg.t.silent", "stop"),
    ("stg.q.pending", "stg.t.pending", "wait"),
    ("stg.q.stale", "stg.t.stale", "wait"),
]

_GEN_CHECKS: list[tuple[str, str, str]] = [
    ("stg.q.media", "stg.t.media", "wait"),
    ("stg.q.bye", "stg.t.bye", "stop"),
    ("stg.q.budget", "stg.t.budget", "wait"),
    ("stg.q.guard", "stg.t.guard", "hand"),
    ("stg.q.rsvp", "stg.t.rsvp", "half"),
    ("stg.q.ready", "stg.t.ready", "hand"),
    ("stg.q.manager", "stg.t.manager", "hand"),
    ("stg.q.discovery", "stg.t.discovery", "half"),
]

_SEND_CHECKS: list[tuple[str, str, str]] = [
    ("stg.q.paused", "stg.t.paused", "wait"),
    ("stg.q.blocked2", "stg.t.blocked2", "stop"),
    ("stg.q.quiet", "stg.t.quiet", "wait"),
    ("stg.q.caps", "stg.t.caps", "wait"),
    ("stg.q.window", "stg.t.window", "stop"),
    ("stg.q.won", "stg.t.won", "stop"),
    ("stg.q.owner", "stg.t.owner", "half"),
    ("stg.q.soft", "stg.t.soft", "wait"),
]

_TURN_CHECKS = _PICK_CHECKS + _GEN_CHECKS + _SEND_CHECKS

# Отдельная схема: когда и что именно уезжает в CRM. Раньше это был один узел «передача»,
# по которому нельзя было понять ни момента, ни содержимого.
_CRM_CHECKS: list[tuple[str, str, str]] = [
    ("stg.c.q.phone", "stg.c.t.phone", "stop"),
    ("stg.c.q.blocked", "stg.c.t.blocked", "stop"),
    ("stg.c.q.off", "stg.c.t.off", "stop"),
    ("stg.c.q.flip", "stg.c.t.flip", "hand"),
    ("stg.c.q.same", "stg.c.t.same", "wait"),
    ("stg.c.q.human", "stg.c.t.human", "hand"),
    ("stg.c.q.old", "stg.c.t.old", "wait"),
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
    lines = _wrap(t(key), 32)
    dy = cy - (len(lines) - 1) * 7
    body = "".join(_txt(cx, dy + i * 14 + 4, ln, "t1 mid") for i, ln in enumerate(lines))
    return f'<g class="n n-dec"><polygon points="{pts}"/>{body}</g>'


def _term(x: int, y: int, key: str, kind: str) -> str:
    lines = _wrap(t(key), 42)
    dy = y + _TH / 2 - (len(lines) - 1) * 8 + 4
    body = "".join(_txt(x + 14, dy + i * 15, ln, "t2") for i, ln in enumerate(lines))
    return (f'<g class="n n-{kind}"><rect x="{x}" y="{y}" width="{_TW}" height="{_TH}" '
            f'rx="9"/>{body}</g>')


def _pill(x: int, y: int, key: str, kind: str) -> str:
    return (f'<g class="n n-{kind}"><rect x="{x}" y="{y}" width="{_DW}" height="52" '
            f'rx="26"/>{_txt(x + _DW / 2, y + 31, t(key), "t1 mid")}</g>')


def _down(x: float, y1: float, y2: float, label: str) -> str:
    return (f'<line x1="{x:.0f}" y1="{y1:.0f}" x2="{x:.0f}" y2="{y2 - 9:.0f}" class="ar"/>'
            f'<polygon points="{x - 5:.0f},{y2 - 9:.0f} {x + 5:.0f},{y2 - 9:.0f} '
            f'{x:.0f},{y2:.0f}" class="arh"/>'
            f'{_txt(x + 9, (y1 + y2) / 2 + 4, label, "t3") if label else ""}')


def _side(x1: float, y: float, x2: float, label: str) -> str:
    return (f'<line x1="{x1:.0f}" y1="{y:.0f}" x2="{x2 - 9:.0f}" y2="{y:.0f}" class="ar"/>'
            f'<polygon points="{x2 - 9:.0f},{y - 5:.0f} {x2 - 9:.0f},{y + 5:.0f} '
            f'{x2:.0f},{y:.0f}" class="arh"/>'
            f'{_txt((x1 + x2) / 2, y - 9, label, "t3 mid")}')


def _chart(checks: list[tuple[str, str, str]], start_key: str, end_key: str,
           svg_id: str) -> str:
    yes, no = t("stg.yes"), t("stg.no")
    parts = [_pill(_LEFT, 14, start_key, "start")]
    y = 14 + 52 + 46
    cx = _LEFT + _DW / 2
    for i, (key, term_key, kind) in enumerate(checks):
        parts.append(_down(cx, y - 46, y, ""))
        parts.append(_diamond(_LEFT, y, key))
        mid = y + _DH / 2
        parts.append(_side(_LEFT + _DW, mid, _RIGHT, yes))
        parts.append(_term(_RIGHT, int(mid - _TH / 2), term_key, kind))
        nxt = y + _STEP
        parts.append(_down(cx, mid + _DH / 2, nxt if i < len(checks) - 1 else nxt, no))
        y = nxt
    parts.append(_pill(_LEFT, int(y), end_key, "go"))
    h, w = int(y) + 86, _RIGHT + _TW + 40
    return (f'<div class="stg-scroll"><svg id="{svg_id}" class="stg-svg" '
            f'viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'data-w="{w}" data-h="{h}" role="img" '
            f'aria-label="{_h.escape(t(start_key))}">' + "".join(parts) + "</svg></div>")


_POLICY_KEYS = [("wait_call", "74%"), ("result_think", "11%"),
                ("result_next_enrollment", "9%"), ("result_fail", "9%"),
                ("result_event", "1%")]


def _css() -> str:
    return (
        "<style>"
        ".stg{max-width:1180px;padding-bottom:40px}"
        ".stg h2{margin:26px 0 4px;font-size:19px}"
        ".stg .lead{opacity:.7;margin:0 0 10px;line-height:1.5}"
        # Только горизонтальная прокрутка: вертикаль листается вместе со страницей, иначе
        # низ схемы становится недостижим (живая жалоба 30.07.2026).
        ".stg-scroll{overflow-x:auto;overflow-y:visible;border:1px solid var(--bd,#2a3441);"
        "border-radius:10px;background:var(--bg2,#151b23);padding:6px}"
        ".stg-svg{display:block;max-width:none}"
        ".stg-zoom{display:inline-flex;gap:4px;margin:6px 0 8px}"
        ".stg-zoom button{width:32px;height:30px;border-radius:7px;border:1px solid "
        "var(--bd,#2a3441);background:var(--bg,#0f141a);color:var(--fg,#e8eef4);"
        "cursor:pointer;font-size:15px;line-height:1}"
        ".stg-zoom button:hover{border-color:#4dabf7}"
        ".stg .n rect,.stg .n polygon{fill:var(--bg,#0f141a);stroke:#3a4757;stroke-width:1.5}"
        ".stg .n-dec polygon{stroke:#c9a227}"
        ".stg .n-stop rect{stroke:#e0698a}"
        ".stg .n-wait rect{stroke:#c9a227;stroke-dasharray:5 4}"
        ".stg .n-half rect{stroke:#4dabf7}"
        ".stg .n-hand rect{stroke:#51cf66}"
        ".stg .n-start rect{stroke:#6c7a8c}"
        ".stg .n-go rect{stroke:#51cf66;stroke-width:2}"
        ".stg .t1{fill:var(--fg,#e8eef4);font:600 12.5px system-ui,sans-serif}"
        ".stg .t2{fill:var(--fg,#e8eef4);opacity:.78;font:12px system-ui,sans-serif}"
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
    """Зум меняет РАЗМЕР svg, а не transform: иначе страница не знает, что схема выросла,
    и до низа не долистать. Ванильный JS, ни одной внешней библиотеки."""
    return (
        "<script>(function(){"
        "var box=document.getElementById('stg-page');if(!box||box.dataset.on)return;"
        "box.dataset.on='1';var z=1;"
        "function ap(){box.querySelectorAll('.stg-svg').forEach(function(s){"
        "s.setAttribute('width',s.dataset.w*z);s.setAttribute('height',s.dataset.h*z);});}"
        "function set(v){z=Math.min(2.5,Math.max(0.5,v));ap();}"
        "box.querySelectorAll('.stg-zoom').forEach(function(bar){"
        "var b=bar.querySelectorAll('button');"
        "b[0].onclick=function(){set(z*1.2);};"
        "b[1].onclick=function(){set(z/1.2);};"
        "b[2].onclick=function(){set(1);};});"
        "box.querySelectorAll('.stg-scroll').forEach(function(w){"
        "var d=false,sx=0,l=0;"
        "w.addEventListener('mousedown',function(e){d=true;sx=e.clientX;l=w.scrollLeft;});"
        "document.addEventListener('mouseup',function(){d=false;});"
        "w.addEventListener('mousemove',function(e){if(!d)return;"
        "w.scrollLeft=l-(e.clientX-sx);});});"
        "})();</script>"
    )


def _zoom_bar() -> str:
    return ('<div class="stg-zoom"><button type="button" title="+">+</button>'
            '<button type="button" title="-">&minus;</button>'
            '<button type="button" title="1:1">1:1</button></div>')


def strategy_page_html() -> str:
    f = _live()
    # .pnl-body — единственный скроллер панели, и это несущая деталь, а не аккуратность:
    # #main задан как flex-колонка с overflow:hidden, поэтому содержимое, лежащее в нём
    # напрямую, просто обрезается по высоте окна и не прокручивается ничем. Ровно на это
    # я наступил дважды: сначала вложив схему в контейнер с max-height, потом отдав
    # прокрутку странице, которой её никто не даёт. Та же ловушка описана в _ui_kb.
    out = [_css(), '<div class="pnl-body"><div class="stg" id="stg-page">']

    out.append(f"<h2>{_h.escape(t('stg.title'))}</h2>")
    out.append(f'<p class="lead">{_h.escape(t("stg.sub"))}</p>')
    out.append(f'<p class="lead">{_h.escape(t("stg.phases"))}</p>')

    for title, checks, start, end, ident in (
        ("stg.ph.pick", _PICK_CHECKS, "stg.q.start", "stg.ph.pick.end", "stg-pick"),
        ("stg.ph.gen", _GEN_CHECKS, "stg.ph.gen.start", "stg.ph.gen.end", "stg-gen"),
        ("stg.ph.send", _SEND_CHECKS, "stg.ph.send.start", "stg.q.send", "stg-send"),
    ):
        out.append(f"<h2>{_h.escape(t(title))}</h2>")
        out.append(f'<p class="lead">{_h.escape(t(title + ".sub"))}</p>')
        out.append(_zoom_bar())
        out.append(_chart(checks, start, end, ident))

    out.append(f"<h2>{_h.escape(t('stg.crm.title'))}</h2>")
    out.append(f'<p class="lead">{_h.escape(t("stg.crm.sub"))}</p>')
    out.append(_zoom_bar())
    out.append(_chart(_CRM_CHECKS, "stg.c.start", "stg.c.end", "stg-crm"))

    out.append(f"<h2>{_h.escape(t('stg.when.title'))}</h2>")
    out.append(f"<table><tr><th>{_h.escape(t('stg.when.path'))}</th>"
               f"<th>{_h.escape(t('stg.when.when'))}</th>"
               f"<th>{_h.escape(t('stg.when.what'))}</th>"
               f"<th>{_h.escape(t('stg.when.mark'))}</th></tr>")
    rows = [
        ("stg.w.flip", "stg.w.flip.when", "stg.w.flip.what", "crm_pushed_handoff"),
        ("stg.w.warm", "stg.w.warm.when", "stg.w.warm.what", "crm_pushed:&lt;тип&gt;"),
        ("stg.w.sweep", "stg.w.sweep.when", "stg.w.sweep.what", "crm_pushed_handoff"),
    ]
    for path, when, what, mark in rows:
        out.append(f"<tr><td>{_h.escape(t(path))}</td><td>{_h.escape(t(when))}</td>"
                   f"<td>{_h.escape(t(what))}</td><td><code>{mark}</code></td></tr>")
    out.append("</table>")

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
    out.append(f'<p class="lead">{_h.escape(t("stg.stages.silent"))} '
               f"<code>{_h.escape(silent)}</code><br>"
               f'{_h.escape(t("stg.stages.human"))} <code>{_h.escape(human)}</code></p>')
    out.append(f'<p class="lead">{_h.escape(t("stg.hint"))}</p>')
    out.append("</div></div>")
    out.append(_zoom_js())
    return "".join(out)
