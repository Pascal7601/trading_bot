"""Render a shareable 'closed trade' PnL card (Pillow). Django-independent.

    png = generate_pnl_card("BTC-USDT", "LONG", 10, Decimal("60000"), Decimal("63000"),
                            brand="@YourCommunity")
Preview:  python -m copier.pnl_card      (writes preview_profit.jpg / preview_loss.jpg)

Robust by design: a missing background image or font must never stop a trade notification,
so both fall back to built-in defaults instead of raising.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from engine.pnl import roi_pct

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
DEFAULT_BACKGROUND = ASSETS / "pnl_bg.jpg"

W, H = 1280, 720
GREEN, RED = (14, 203, 129), (246, 70, 93)
WHITE, MUTED = (255, 255, 255), (152, 163, 180)

# Drop Poppins/Inter .ttf files into assets/fonts/ for the best look; system fonts are the fallback.
_FONT_DIRS = [
    ASSETS / "fonts",
    Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/liberation"),
    Path("C:/Windows/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"),
]
_FONT_FILES = {
    "bold": ["Poppins-Bold.ttf", "Inter-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
             "arialbd.ttf", "Arial Bold.ttf"],
    "medium": ["Poppins-Medium.ttf", "Inter-Medium.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
               "arial.ttf", "Arial.ttf"],
    "regular": ["Poppins-Regular.ttf", "Inter-Regular.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
                "arial.ttf", "Arial.ttf"],
}


@lru_cache(maxsize=64)
def _font(weight: str, size: int) -> ImageFont.ImageFont:
    for folder in _FONT_DIRS:
        for name in _FONT_FILES[weight]:
            path = folder / name
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1: scalable built-in font
    except TypeError:
        return ImageFont.load_default()


def _text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, anchor: str = "ls") -> None:
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)
    except ValueError:  # bitmap fallback font doesn't support anchors
        draw.text(xy, text, font=font, fill=fill)


def _fmt_price(p: Decimal) -> str:
    p = Decimal(p)
    decimals = 2 if p >= 1000 else 4 if p >= 1 else 5 if p >= Decimal("0.01") else 8
    return f"{p:,.{decimals}f}"


def _h_ramp(w: int, h: int, start: float, end: float) -> Image.Image:
    """Horizontal alpha mask: transparent left of `start`, opaque right of `end` (fractions of width)."""
    row = Image.new("L", (w, 1))
    row.putdata([max(0, min(255, int(255 * (x / w - start) / (end - start)))) for x in range(w)])
    return row.resize((w, h), Image.NEAREST)


def _fallback_background() -> Image.Image:
    col = Image.new("L", (1, H))
    col.putdata([int(18 + 22 * (1 - y / H)) for y in range(H)])
    lum = col.resize((W, H), Image.NEAREST)
    return Image.merge("RGB", (lum.point(lambda v: int(v * 0.55)), lum.point(lambda v: int(v * 0.75)), lum)).convert("RGBA")


@lru_cache(maxsize=2)
def _background(path: str) -> Image.Image:
    """Blurred, darkened full-bleed base + the sharp artwork fading in on the right side."""
    try:
        src = Image.open(path).convert("RGB")
    except (OSError, ValueError) as e:
        print(f"IMAGE ERROR: Pillow rejected the file: {e}")
        return _fallback_background()
    scale = W / src.width
    base = src.resize((W, round(src.height * scale)), Image.LANCZOS)
    top = (base.height - H) // 2
    base = base.crop((0, top, W, top + H)).filter(ImageFilter.GaussianBlur(26))
    base = Image.blend(base, Image.new("RGB", (W, H), (5, 8, 14)), 0.5)

    hero_size = round(H * 1.32)
    hero = src.resize((hero_size, hero_size), Image.LANCZOS)
    base.paste(hero, (W - hero_size + 70, -round(hero_size * 0.13)), _h_ramp(hero_size, hero_size, 0.05, 0.5))
    return base.convert("RGBA")


def _pill(draw, x, y, label, font, fg, fill, outline, h=46, pad=24) -> int:
    w = int(draw.textlength(label, font=font)) + pad * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill, outline=outline, width=2)
    _text(draw, (x + pad, y + h // 2 + 1), label, font, fg, anchor="lm")
    return x + w + 14


def generate_pnl_card(
    symbol: str,
    side: str,
    leverage: int,
    entry_price: Decimal,
    exit_price: Decimal,
    *,
    pnl_usdt: Decimal | None = None,
    closed_at: datetime | None = None,
    brand: str = "",
    background: str | Path | None = None,
    image_format: str = "JPEG",
) -> bytes:
    side = side.upper()
    roi = roi_pct(side, leverage, entry_price, exit_price)
    profit = roi >= 0
    accent = GREEN if profit else RED
    side_col = GREEN if side == "LONG" else RED

    img = _background(str(background or DEFAULT_BACKGROUND)).copy()
    img = Image.alpha_composite(img, Image.new("RGBA", (W, H), accent + (22,))).convert("RGB")  # mood tint

    # --- frosted-glass panel -------------------------------------------------
    px, py, pw, ph, radius = 56, 56, 700, 608, 36
    box = (px, py, px + pw, py + ph)
    glass = img.crop(box).filter(ImageFilter.GaussianBlur(22)).convert("RGBA")
    glass = Image.alpha_composite(glass, Image.new("RGBA", glass.size, (8, 12, 20, 170))).convert("RGB")
    mask = Image.new("L", glass.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=radius, fill=255)
    img.paste(glass, (px, py), mask)
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 48), width=2)

    x0, x1 = px + 48, px + pw - 48
    inner_w = x1 - x0

    # --- header: symbol + pills ---------------------------------------------
    # Add BingX text at the top-left of the glass panel
    _text(draw, (x0, py + 40), "BingX", _font("bold", 22), (255, 255, 255, 180))

    base_ccy, _, quote_ccy = symbol.upper().partition("-")
    _text(draw, (x0, py + 82), f"{base_ccy}/{quote_ccy}" if quote_ccy else symbol.upper(), _font("bold", 46), WHITE)
    _text(draw, (x0, py + 118), "Perpetual  ·  Closed trade", _font("regular", 22), MUTED)
    pill_font = _font("bold", 22)
    x = _pill(draw, x0, py + 142, side, pill_font, side_col, side_col + (46,), side_col + (150,))
    _pill(draw, x, py + 142, f"{leverage}x", pill_font, WHITE, (255, 255, 255, 28), (255, 255, 255, 70))

    # --- hero number with glow ----------------------------------------------
    _text(draw, (x0, py + 246), "ROI  ·  estimate, before fees", _font("regular", 22), MUTED)
    roi_text = f"{roi:+,.2f}%"
    size = 170
    while size > 60 and draw.textlength(roi_text, font=_font("bold", size)) > inner_w:
        size -= 6
    roi_font, roi_y = _font("bold", size), py + 388
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _text(ImageDraw.Draw(glow), (x0, roi_y), roi_text, roi_font, accent + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(24))
    glow.putalpha(glow.getchannel("A").point(lambda v: min(255, int(v * 0.85))))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    _text(draw, (x0, roi_y), roi_text, roi_font, accent)

    # --- stats ---------------------------------------------------------------
    draw.line((x0, py + 428, x1, py + 428), fill=(255, 255, 255, 38), width=2)
    stats = [("Entry Price", _fmt_price(entry_price), WHITE), ("Exit Price", _fmt_price(exit_price), WHITE)]
    if pnl_usdt is not None:
        stats.append(("PnL (USDT)", f"{Decimal(pnl_usdt):+,.2f}", accent))
    col_w = inner_w // len(stats)
    for i, (label, value, colour) in enumerate(stats):
        cx = x0 + i * col_w
        vsize = 38
        while vsize > 22 and draw.textlength(value, font=_font("bold", vsize)) > col_w - 16:
            vsize -= 2
        _text(draw, (cx, py + 474), label, _font("regular", 22), MUTED)
        _text(draw, (cx, py + 522), value, _font("bold", vsize), colour)

    # --- footer --------------------------------------------------------------
    when = (closed_at or datetime.now(timezone.utc)).strftime("%d %b %Y  ·  %H:%M UTC")
    _text(draw, (x0, py + 580), brand, _font("medium", 22), (255, 255, 255, 215))
    _text(draw, (x1, py + 580), when, _font("regular", 20), MUTED, anchor="rs")

    buf = io.BytesIO()
    if image_format.upper() == "PNG":
        img.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "JPEG", quality=92, subsampling=0)
    return buf.getvalue()


if __name__ == "__main__":  # python -m copier.pnl_card
    demo = dict(brand="@YourCommunity", closed_at=datetime(2026, 9, 19, 12, 53, tzinfo=timezone.utc))
    Path("preview_profit.jpg").write_bytes(generate_pnl_card(
        "BTC-USDT", "LONG", 10, Decimal("60250.5"), Decimal("63180.2"), pnl_usdt=Decimal("1243.80"), **demo))
    Path("preview_loss.jpg").write_bytes(generate_pnl_card(
        "ETH-USDT", "SHORT", 25, Decimal("3120.40"), Decimal("3188.75"), pnl_usdt=Decimal("-312.55"), **demo))
    print("wrote preview_profit.jpg and preview_loss.jpg")