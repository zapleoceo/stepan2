"""Illustrative analytics dashboard for the public landing (fake data, English)."""
from __future__ import annotations

_INK = "#f2f4f7"
_MUT = "#9aa3b2"
_CARD = "#15171d"
_LINE = "#20232b"

# name, count, color, won%
_SEGMENTS = [
    ("Warm", 1205, "#4cc38a", "67% · won 3%"),
    ("Unclear", 422, "#8b93a3", "23% · won 1%"),
    ("Off-target", 64, "#5b626f", "4% · won 0%"),
    ("No budget", 45, "#9b8cff", "2% · won 7%"),
    ("Hot", 29, "#ff5c5c", "2% · won 17%"),
    ("Cold", 25, "#4d8dff", "1% · won 4%"),
    ("Referrals", 15, "#f5a623", "1% · won 0%"),
]
_TOTAL = 1805

# name, count, color
_FUNNEL = [
    ("Entry", 1793, "#4d8dff"),
    ("Nurturing", 78, "#f5a623"),
    ("Qualified", 643, "#9b8cff"),
    ("Presenting", 1038, "#4cc38a"),
    ("Objection", 61, "#ff5c5c"),
    ("Ready", 91, "#4cc38a"),
    ("Handed off", 12, "#2dd4bf"),
]

# hourly outgoing / incoming (peak at 08:00), illustrative
_OUT = [40, 22, 14, 9, 7, 18, 210, 640, 1293, 720, 560, 610,
        690, 540, 470, 520, 600, 540, 430, 360, 300, 240, 160, 90]
_IN = [30, 18, 11, 7, 5, 14, 120, 300, 470, 340, 260, 300,
       330, 250, 210, 240, 280, 250, 200, 160, 130, 100, 70, 40]


def _segments_svg() -> str:
    rows_h, gap, top = 46, 6, 6
    rx, rw = 372, 372
    parts = ['<svg viewBox="0 0 760 372" width="100%" '
             'xmlns="http://www.w3.org/2000/svg" role="img" '
             'aria-label="Lead segments (illustrative)">']
    # ribbons emanate from around the node (centred on its middle)
    node_mid, stack = 188, 150
    cum = node_mid - stack / 2
    ribbons = []
    for i, (_n, cnt, col, _s) in enumerate(_SEGMENTS):
        w = max(2.0, cnt / _TOTAL * stack)
        oy = cum + w / 2
        cum += w
        ty = top + i * (rows_h + gap) + rows_h / 2
        ribbons.append(
            f'<path d="M142 {oy:.1f} C258 {oy:.1f} 256 {ty:.1f} 372 {ty:.1f}" '
            f'stroke="{col}" stroke-width="{w:.1f}" fill="none" opacity="0.42"/>')
    parts.extend(ribbons)
    # left node
    parts.append(
        f'<rect x="14" y="150" width="128" height="76" rx="12" '
        f'fill="{_CARD}" stroke="{_LINE}"/>'
        f'<text x="78" y="180" text-anchor="middle" fill="{_MUT}" '
        f'font-size="12">Total leads</text>'
        f'<text x="78" y="206" text-anchor="middle" fill="{_INK}" '
        f'font-size="24" font-weight="700">1805</text>')
    # rows
    for i, (name, cnt, col, sub) in enumerate(_SEGMENTS):
        y = top + i * (rows_h + gap)
        parts.append(
            f'<rect x="{rx}" y="{y}" width="{rw}" height="{rows_h}" rx="10" '
            f'fill="{_CARD}" stroke="{_LINE}"/>'
            f'<rect x="{rx}" y="{y}" width="5" height="{rows_h}" rx="2.5" '
            f'fill="{col}"/>'
            f'<text x="{rx + 18}" y="{y + 21}" fill="{col}" font-size="15" '
            f'font-weight="700">{name}</text>'
            f'<text x="{rx + 18}" y="{y + 37}" fill="{_MUT}" '
            f'font-size="11">{sub}</text>'
            f'<text x="{rx + rw - 16}" y="{y + 30}" text-anchor="end" '
            f'fill="{_INK}" font-size="20" font-weight="700">{cnt}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _funnel_svg() -> str:
    xs = [40, 145, 250, 355, 460, 565, 670]
    base, max_h, bw = 190, 150, 30
    top_cnt = max(c for _n, c, _col in _FUNNEL)
    parts = ['<svg viewBox="0 0 760 300" width="100%" '
             'xmlns="http://www.w3.org/2000/svg" role="img" '
             'aria-label="Sales funnel (illustrative)">']
    # flow band through bar tops
    tops = []
    for (x, (_n, cnt, _col)) in zip(xs, _FUNNEL, strict=True):
        h = max(10, cnt / top_cnt * max_h)
        tops.append((x + bw / 2, base - h))
    band = f'M{xs[0]} {base} '
    band += " ".join(f"L{cx:.0f} {ty:.0f}" for cx, ty in tops)
    band += f" L{xs[-1] + bw} {base} Z"
    parts.append(f'<path d="{band}" fill="#4cc38a" opacity="0.1"/>')
    # bars
    for x, (name, cnt, col) in zip(xs, _FUNNEL, strict=True):
        h = max(10, cnt / top_cnt * max_h)
        parts.append(
            f'<rect x="{x}" y="{base - h:.0f}" width="{bw}" height="{h:.0f}" '
            f'rx="5" fill="{col}"/>'
            f'<text x="{x + bw / 2:.0f}" y="{base - h - 16:.0f}" '
            f'text-anchor="middle" fill="{_MUT}" font-size="10">{name}</text>'
            f'<text x="{x + bw / 2:.0f}" y="{base - h - 4:.0f}" '
            f'text-anchor="middle" fill="{_INK}" font-size="11" '
            f'font-weight="700">{cnt}</text>')
    # lower branches
    for x, name, cnt, col in [(250, "Dormant", 945, "#5b626f"),
                              (460, "To manager", 57, "#ff5c5c")]:
        parts.append(
            f'<rect x="{x}" y="235" width="{bw}" height="34" rx="5" '
            f'fill="{col}" opacity="0.85"/>'
            f'<text x="{x + bw / 2:.0f}" y="286" text-anchor="middle" '
            f'fill="{_MUT}" font-size="10">{name} · {cnt}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _messages_svg() -> str:
    n = len(_OUT)
    left, right, bot, top = 8, 752, 92, 6
    slot = (right - left) / n
    bw = slot * 0.62
    peak = max(o + i for o, i in zip(_OUT, _IN, strict=True))
    parts = ['<svg viewBox="0 0 760 108" width="100%" '
             'xmlns="http://www.w3.org/2000/svg" role="img" '
             'aria-label="Messages by hour (illustrative)">']
    for i in range(n):
        x = left + i * slot + (slot - bw) / 2
        hi = (_IN[i] / peak) * (bot - top)
        ho = (_OUT[i] / peak) * (bot - top)
        parts.append(
            f'<rect x="{x:.1f}" y="{bot - hi:.1f}" width="{bw:.1f}" '
            f'height="{hi:.1f}" rx="1.5" fill="#4d8dff" opacity="0.9"/>')
        parts.append(
            f'<rect x="{x:.1f}" y="{bot - hi - ho:.1f}" width="{bw:.1f}" '
            f'height="{ho:.1f}" rx="1.5" fill="#4cc38a" opacity="0.9"/>')
    for hh in (0, 6, 12, 18):
        parts.append(
            f'<text x="{left + hh * slot + slot / 2:.0f}" y="106" '
            f'text-anchor="middle" fill="{_MUT}" font-size="9">'
            f'{hh:02d}</text>')
    parts.append("</svg>")
    return "".join(parts)


def analytics_section() -> str:
    return (
        "<section><div class=\"wrap\">"
        ""
        "<h2>Read your whole pipeline at a glance</h2>"
        "<p class=\"lead\">Every lead segmented by intent, every stage of the funnel, "
        "and message volume by hour, updated as conversations happen.</p>"
        "<div class=\"anl\">"
        f"<div class=\"apanel\"><div class=\"atitle\">Lead segments</div>"
        f"{_segments_svg()}</div>"
        f"<div class=\"apanel\"><div class=\"atitle\">Sales funnel</div>"
        f"{_funnel_svg()}</div>"
        f"<div class=\"apanel\"><div class=\"atitle\">Messages by hour, 0-23"
        f"<span class=\"asub\"> · 4167 in · 9532 out · peak 1293 at 08:00</span>"
        f"</div>{_messages_svg()}</div>"
        "</div>"
        "<p class=\"mnote\">Illustrative: sample data, not a real account.</p>"
        "</div></section>"
    )
