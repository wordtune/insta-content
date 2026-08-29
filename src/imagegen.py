"""ساخت تصویر پست: رندر قالب گرافیکی با Pillow و پشتیبانی کامل از متن فارسی (RTL)."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"

# اگر Pillow با libraqm ساخته شده باشد، خودش حروف فارسی را می‌چسباند و جهت را
# درست می‌کند. در غیر این صورت باید دستی با arabic-reshaper و bidi این کار را کرد.
HAVE_RAQM = bool(features.check("raqm"))
_RTL = {"direction": "rtl", "language": "fa"} if HAVE_RAQM else {}


# ---------- متن فارسی ----------

def shape(text: str) -> str:
    """آماده‌سازی متن فارسی برای رندر.

    با libraqm هیچ تغییری لازم نیست (اعمالش باعث برعکس شدن متن می‌شود).
    بدون libraqm، حروف را متصل و ترتیب را معکوس می‌کنیم.
    """
    if not text:
        return ""
    if HAVE_RAQM:
        return str(text)
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(str(text)))


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    name = {"regular": "Vazirmatn-Regular.ttf",
            "bold": "Vazirmatn-Bold.ttf",
            "black": "Vazirmatn-Black.ttf"}[weight]
    path = FONTS / name
    if not path.exists():
        return ImageFont.load_default(size=size)
    return ImageFont.truetype(str(path), size)


def text_width(draw: ImageDraw.ImageDraw, text: str,
               fnt: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(shape(text), font=fnt, **_RTL)


def draw_rtl(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont,
             *, right: int, top: int, fill: str) -> None:
    """یک خط متن راست‌چین رسم می‌کند (مبدأ در لبه راست)."""
    draw.text((right, top), shape(text), font=fnt, fill=fill, anchor="ra", **_RTL)


def wrap_rtl(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont,
             max_width: int) -> list[str]:
    """شکستن متن به خطوط با توجه به عرض واقعی رندرشده."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if text_width(draw, trial, fnt) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_rtl_block(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont,
                   *, right: int, top: int, max_width: int, fill: str,
                   line_spacing: float = 1.45) -> int:
    """متن چندخطی راست‌چین رسم می‌کند و ارتفاع مصرف‌شده را برمی‌گرداند."""
    lines = wrap_rtl(draw, text, fnt, max_width)
    line_h = int(fnt.size * line_spacing)
    y = top
    for line in lines:
        draw_rtl(draw, line, fnt, right=right, top=y, fill=fill)
        y += line_h
    return y - top


# ---------- پس‌زمینه ----------

def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def gradient(size: int, top: str, bottom: str) -> Image.Image:
    t, b = _hex(top), _hex(bottom)
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        r = y / max(size - 1, 1)
        px[0, y] = tuple(int(t[i] + (b[i] - t[i]) * r) for i in range(3))  # type: ignore
    return img.resize((size, size), Image.LANCZOS)


def _mix(c: str, other: str, ratio: float) -> str:
    a, b = _hex(c), _hex(other)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * ratio) for i in range(3))


def background(cfg: dict[str, Any], size: int, product_image: Path | None) -> Image.Image:
    colors = cfg["brand"]["colors"]
    base = gradient(size, colors["background"], _mix(colors["background"], colors["accent"], 0.22))

    if product_image and product_image.exists():
        try:
            prod = Image.open(product_image).convert("RGB")
            # پرکردن کادر با حفظ نسبت
            scale = max(size / prod.width, size / prod.height)
            prod = prod.resize((int(prod.width * scale), int(prod.height * scale)), Image.LANCZOS)
            left = (prod.width - size) // 2
            top = (prod.height - size) // 2
            prod = prod.crop((left, top, left + size, top + size))
            prod = prod.filter(ImageFilter.GaussianBlur(radius=size // 90))
            base = Image.blend(base, prod, 0.55)
        except Exception:
            pass

    # سایه‌ی نرم از پایین به بالا، برای خوانایی متن روی هر پس‌زمینه‌ای
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    fade_start = int(size * 0.28)          # از کجا شروع به تیره شدن کند
    span = size - fade_start
    for y in range(fade_start, size):
        r = (y - fade_start) / span
        od.line([(0, y), (size, y)], fill=(0, 0, 0, int(205 * r ** 1.6)))
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


def _paste_logo(img: Image.Image, logo_path: str, size: int) -> None:
    p = Path(logo_path)
    if not logo_path or not p.exists():
        return
    try:
        logo = Image.open(p).convert("RGBA")
        target_w = int(size * 0.13)
        logo = logo.resize((target_w, int(logo.height * target_w / logo.width)), Image.LANCZOS)
        img.paste(logo, (int(size * 0.06), int(size * 0.06)), logo)
    except Exception:
        pass


# ---------- کارت پست ----------

def render_card(cfg: dict[str, Any], *, headline: str, subline: str = "",
                kicker: str = "", product_image: Path | None = None,
                out_path: Path) -> Path:
    size = int(cfg["image"]["size"])
    colors = cfg["brand"]["colors"]
    img = background(cfg, size, product_image)
    draw = ImageDraw.Draw(img)

    margin = int(size * 0.085)
    right = size - margin
    max_w = size - 2 * margin

    _paste_logo(img, cfg["image"].get("logo_path", ""), size)

    # --- اندازه‌گیری قبل از رسم، تا کل بلوک از پایین لنگر بگیرد ---
    f_k = font("bold", int(size * 0.028))
    pad = int(size * 0.018)
    kicker_h = int(f_k.size * 2.6) if kicker else 0

    f_h = font("black", int(size * 0.074))
    h_lines = wrap_rtl(draw, headline, f_h, max_w)
    while len(h_lines) > 3 and f_h.size > int(size * 0.042):
        f_h = font("black", f_h.size - 4)
        h_lines = wrap_rtl(draw, headline, f_h, max_w)
    head_lh = int(f_h.size * 1.32)
    head_h = len(h_lines) * head_lh

    f_s = font("regular", int(size * 0.036))
    s_lines = wrap_rtl(draw, subline, f_s, max_w) if subline else []
    sub_lh = int(f_s.size * 1.58)
    sub_h = (len(s_lines) * sub_lh + int(size * 0.022)) if s_lines else 0

    f_b = font("bold", int(size * 0.026))
    brand_h = int(f_b.size * 2.6)

    block_h = kicker_h + head_h + sub_h
    y = size - margin - brand_h - block_h
    y = max(y, int(size * 0.30))  # اگر متن خیلی بلند بود، بالاتر برو

    # --- رسم ---
    if kicker:
        tw = text_width(draw, kicker, f_k)
        draw.rounded_rectangle(
            [right - tw - 2 * pad, y - pad, right, y + f_k.size + pad],
            radius=pad, fill=colors["accent"])
        draw_rtl(draw, kicker, f_k, right=right - pad, top=y, fill=colors["background"])
        y += kicker_h

    for line in h_lines:
        draw_rtl(draw, line, f_h, right=right, top=y, fill=colors["text"])
        y += head_lh

    if s_lines:
        y += int(size * 0.022)
        for line in s_lines:
            draw_rtl(draw, line, f_s, right=right, top=y, fill=colors["muted"])
            y += sub_lh

    # خط تزئینی + نام برند در پایین
    line_y = size - margin - int(f_b.size * 1.5)
    draw.line([(right - int(size * 0.05), line_y), (right, line_y)],
              fill=colors["accent"], width=max(3, size // 300))
    draw_rtl(draw, cfg["brand"]["name"], f_b, right=right,
             top=size - margin - f_b.size, fill=colors["accent"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # اینستاگرام فقط JPEG می‌پذیرد
    img.convert("RGB").save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def pick_product(cfg: dict[str, Any]) -> Path | None:
    d = ROOT / cfg["image"].get("products_dir", "assets/products")
    if not d.exists():
        return None
    files = [p for p in d.iterdir()
             if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(files) if files else None


def build_images(cfg: dict[str, Any], content: dict[str, Any], *,
                 post_id: int, out_dir: Path,
                 name_prefix: str | None = None) -> list[Path]:
    """بر اساس محتوا، یک یا چند تصویر می‌سازد و مسیرها را برمی‌گرداند."""
    product = pick_product(cfg)
    paths: list[Path] = []
    stem = name_prefix or f"post_{post_id:05d}"

    if "slides" in content:  # کاروسل
        for i, slide in enumerate(content["slides"], 1):
            p = out_dir / f"{stem}_{i:02d}.jpg"
            render_card(cfg,
                        headline=slide.get("headline", ""),
                        subline=slide.get("subline", ""),
                        kicker=slide.get("kicker", ""),
                        product_image=product if i == 1 else None,
                        out_path=p)
            paths.append(p)
    else:  # تک‌تصویر
        p = out_dir / f"{stem}_01.jpg"
        render_card(cfg,
                    headline=content.get("headline", content.get("title", "")),
                    subline=content.get("subline", ""),
                    kicker="",
                    product_image=product,
                    out_path=p)
        paths.append(p)

    return paths
