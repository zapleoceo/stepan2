"""Social-share card at /og.png — messengers (Telegram, WhatsApp, iMessage) don't render
an SVG og:image, so link previews showed no picture at all. Rendered once with Pillow from
the bundled brand font (Space Grotesk, OFL) and cached for the process lifetime."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

_W, _H = 1200, 630
_BG = (11, 13, 18)          # --bg
_PANEL = (21, 23, 29)       # --panel
_LINE = (43, 47, 56)        # --line2
_INK = (242, 244, 247)      # --ink
_MUT = (154, 163, 178)      # --mut
_ACC = (255, 92, 53)        # --acc

_FONT = Path(__file__).resolve().parent.parent / "assets" / "SpaceGrotesk.ttf"


def _font(size: int, weight: str = "Regular"):
    from PIL import ImageFont  # noqa: PLC0415

    f = ImageFont.truetype(str(_FONT), size)
    f.set_variation_by_name(weight)
    return f


@lru_cache(maxsize=1)
def og_png() -> bytes:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    im = Image.new("RGB", (_W, _H), _BG)
    d = ImageDraw.Draw(im)

    # soft top-left accent wash so the card doesn't read as a flat black slab
    for i in range(220):
        a = int(26 * (1 - i / 220))
        d.ellipse((-500 + i, -520 + i, 700 - i, 380 - i),
                  outline=(_BG[0] + a // 6, _BG[1] + a // 8, _BG[2] + a // 10))

    # logo tile + wordmark
    d.rounded_rectangle((84, 78, 148, 142), radius=16, fill=_INK)
    d.text((116, 106), "S", font=_font(44, "Bold"), fill=(0, 0, 0), anchor="mm")
    d.text((170, 110), "Stepan", font=_font(40, "Medium"), fill=_INK, anchor="lm")
    d.text((334, 114), "AI Sales Agent", font=_font(24), fill=_MUT, anchor="lm")

    # headline (two lines, the tagline of the page)
    d.text((84, 240), "Your best salesperson,", font=_font(74, "Bold"), fill=_INK, anchor="lm")
    d.text((84, 330), "scaled.", font=_font(74, "Bold"), fill=_ACC, anchor="lm")
    d.text((84, 414), "Qualifies and sells in Instagram & WhatsApp DMs.",
           font=_font(30), fill=_MUT, anchor="lm")

    # real production totals strip (matches the landing's stats section)
    d.rounded_rectangle((84, 476, _W - 84, 556), radius=14, fill=_PANEL, outline=_LINE)
    stats = (("3,600+", "leads worked"), ("29,000+", "messages"),
             ("200+", "sales-ready"), ("24/7", "always on"))
    cell = (_W - 168) // 4
    for i, (n, lbl) in enumerate(stats):
        cx = 84 + cell * i + cell // 2
        d.text((cx, 502), n, font=_font(28, "Bold"), fill=_INK, anchor="mm")
        d.text((cx, 534), lbl, font=_font(18), fill=_MUT, anchor="mm")

    buf = BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
