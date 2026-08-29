"""ساخت تصویر پست: رندر قالب گرافیکی با Pillow و پشتیبانی کامل از متن فارسی (RTL)."""
from __future__ import annotations

import colorsys
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


# ---------- رنگ ----------

def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _rgb(c: str) -> tuple[int, int, int]:
    return _hex(c)


def _mix(c: str, other: str, ratio: float) -> str:
    a, b = _hex(c), _hex(other)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * ratio) for i in range(3))


def _shift_hue(c: str, degrees: float) -> str:
    """رنگ را کمی می‌چرخاند — برای تنوع بین پست‌ها بدون خروج از هویت برند."""
    r, g, b = (v / 255 for v in _hex(c))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + degrees / 360.0) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


# ---------- بافت و عمق پس‌زمینه ----------

def gradient(size: int, top: str, bottom: str) -> Image.Image:
    t, b = _hex(top), _hex(bottom)
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        r = y / max(size - 1, 1)
        px[0, y] = tuple(int(t[i] + (b[i] - t[i]) * r) for i in range(3))  # type: ignore
    return img.resize((size, size), Image.LANCZOS)


def _glow(size: int, center: tuple[float, float], radius: float,
          color: str, strength: float = 0.55) -> Image.Image:
    """هاله‌ی نرم رنگی — به تصویر عمق می‌دهد و از تخت بودن درش می‌آورد.

    برای سرعت، در ابعاد کوچک ساخته و بزرگ می‌شود.
    """
    small = 96
    layer = Image.new("L", (small, small), 0)
    px = layer.load()
    cx, cy = center[0] * small, center[1] * small
    rad = radius * small
    for y in range(small):
        for x in range(small):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d < rad:
                v = (1 - d / rad) ** 2.2
                px[x, y] = int(255 * v * strength)
    layer = layer.resize((size, size), Image.LANCZOS)
    tint = Image.new("RGB", (size, size), _rgb(color))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(tint, (0, 0), layer)
    return out


def _dot_grid(size: int, color: str, spacing: int, radius: int,
              alpha: int = 26) -> Image.Image:
    """شبکه‌ی نقطه‌ای ظریف — حس کاغذ فنی و آکادمیک می‌دهد."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r, g, b = _rgb(color)
    for y in range(spacing, size, spacing):
        for x in range(spacing, size, spacing):
            d.ellipse([x - radius, y - radius, x + radius, y + radius],
                      fill=(r, g, b, alpha))
    return layer


def _corner_frame(draw: ImageDraw.ImageDraw, size: int, margin: int,
                  color: str, length: int, width: int) -> None:
    """دو گوشه‌ی L شکل — قاب سبک بدون شلوغی."""
    d = draw
    d.line([(margin, margin), (margin + length, margin)], fill=color, width=width)
    d.line([(margin, margin), (margin, margin + length)], fill=color, width=width)
    br = size - margin
    d.line([(br - length, br), (br, br)], fill=color, width=width)
    d.line([(br, br - length), (br, br)], fill=color, width=width)


def background(cfg: dict[str, Any], size: int, product_image: Path | None,
               accent: str, *, texture: bool = True) -> Image.Image:
    colors = cfg["brand"]["colors"]
    bg = colors["background"]
    base = gradient(size, _mix(bg, "#000000", 0.25), _mix(bg, accent, 0.16))

    if product_image and product_image.exists():
        try:
            prod = Image.open(product_image).convert("RGB")
            scale = max(size / prod.width, size / prod.height)
            prod = prod.resize((int(prod.width * scale), int(prod.height * scale)),
                               Image.LANCZOS)
            left = (prod.width - size) // 2
            top = (prod.height - size) // 2
            prod = prod.crop((left, top, left + size, top + size))
            prod = prod.filter(ImageFilter.GaussianBlur(radius=size // 70))
            base = Image.blend(base, prod, 0.42)
        except Exception:
            pass

    out = base.convert("RGBA")
    # هاله‌ی رنگی بالا سمت راست + یک هاله‌ی سردتر پایین چپ
    out = Image.alpha_composite(out, _glow(size, (0.78, 0.16), 0.72, accent, 0.42))
    out = Image.alpha_composite(
        out, _glow(size, (0.12, 0.9), 0.6, _mix(accent, "#FFFFFF", 0.4), 0.16))
    if texture:
        out = Image.alpha_composite(
            out, _dot_grid(size, colors["text"], max(16, size // 34),
                           max(1, size // 720)))

    # تیرگی تدریجی پایین برای خوانایی متن
    shade = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    start = int(size * 0.30)
    for y in range(start, size):
        r = (y - start) / (size - start)
        sd.line([(0, y), (size, y)], fill=(0, 0, 0, int(190 * r ** 1.5)))
    return Image.alpha_composite(out, shade).convert("RGB")


def _paste_logo(img: Image.Image, logo_path: str, size: int) -> None:
    p = Path(logo_path)
    if not logo_path or not p.exists():
        return
    try:
        logo = Image.open(p).convert("RGBA")
        target_w = int(size * 0.13)
        logo = logo.resize((target_w, int(logo.height * target_w / logo.width)),
                           Image.LANCZOS)
        img.paste(logo, (int(size * 0.07), int(size * 0.07)), logo)
    except Exception:
        pass


# ---------- اجزای مشترک ----------

def _chip(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, *,
          right: int, top: int, fill: str, ink: str, pad: int) -> int:
    """برچسب گرد رنگی. ارتفاع مصرف‌شده را برمی‌گرداند."""
    tw = text_width(draw, text, fnt)
    draw.rounded_rectangle([right - tw - 2 * pad, top - pad,
                            right, top + fnt.size + pad],
                           radius=int(pad * 1.6), fill=fill)
    draw_rtl(draw, text, fnt, right=right - pad, top=top, fill=ink)
    return int(fnt.size + 2 * pad)


def _progress_dots(draw: ImageDraw.ImageDraw, size: int, *, index: int, total: int,
                   y: int, accent: str, muted: str) -> None:
    """نشانگر اسلاید کاروسل — مخاطب می‌فهمد چند صفحه مانده."""
    r = max(3, size // 190)
    gap = r * 4
    width = total * (2 * r) + (total - 1) * (gap - 2 * r)
    x = (size - width) // 2 + r
    for i in range(total):
        active = (i + 1) == index
        rr = r if active else int(r * 0.62)
        draw.ellipse([x - rr, y - rr, x + rr, y + rr],
                     fill=accent if active else muted)
        x += gap


def _brand_footer(draw: ImageDraw.ImageDraw, cfg: dict[str, Any], size: int,
                  margin: int, accent: str) -> None:
    colors = cfg["brand"]["colors"]
    f_b = font("bold", int(size * 0.025))
    right = size - margin
    top = size - margin - f_b.size
    draw.line([(right - int(size * 0.055), top - int(size * 0.022)),
               (right, top - int(size * 0.022))],
              fill=accent, width=max(3, size // 300))
    draw_rtl(draw, cfg["brand"]["name"], f_b, right=right, top=top, fill=accent)


def _fit_headline(draw: ImageDraw.ImageDraw, text: str, size: int, max_w: int,
                  *, start: float, floor: float, max_lines: int
                  ) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    f = font("black", int(size * start))
    lines = wrap_rtl(draw, text, f, max_w)
    while len(lines) > max_lines and f.size > int(size * floor):
        f = font("black", f.size - 4)
        lines = wrap_rtl(draw, text, f, max_w)
    return f, lines


# ---------- قالب‌ها ----------

def _layout_editorial(img, draw, cfg, *, size, accent, headline, subline,
                      kicker, margin) -> None:
    """تیتر بزرگ پایین‌چین با نوار تأکید — قالب پیش‌فرض و خوانا."""
    colors = cfg["brand"]["colors"]
    right, max_w = size - margin, size - 2 * margin
    pad = int(size * 0.017)

    f_k = font("bold", int(size * 0.027))
    f_h, h_lines = _fit_headline(draw, headline, size, max_w,
                                 start=0.078, floor=0.044, max_lines=3)
    head_lh = int(f_h.size * 1.30)
    f_s = font("regular", int(size * 0.035))
    s_lines = wrap_rtl(draw, subline, f_s, max_w) if subline else []
    sub_lh = int(f_s.size * 1.55)

    bar_h = int(size * 0.010)
    block = ((int(f_k.size + 2 * pad) + int(size * 0.035)) if kicker else 0) \
        + bar_h + int(size * 0.030) \
        + len(h_lines) * head_lh \
        + ((int(size * 0.024) + len(s_lines) * sub_lh) if s_lines else 0)

    y = max(size - margin - int(size * 0.075) - block, int(size * 0.26))

    if kicker:
        y += _chip(draw, kicker, f_k, right=right, top=y,
                   fill=accent, ink=colors["background"], pad=pad)
        y += int(size * 0.035)

    # نوار تأکید کوتاه بالای تیتر
    draw.rounded_rectangle([right - int(size * 0.11), y,
                            right, y + bar_h], radius=bar_h // 2, fill=accent)
    y += bar_h + int(size * 0.030)

    for line in h_lines:
        draw_rtl(draw, line, f_h, right=right, top=y, fill=colors["text"])
        y += head_lh

    if s_lines:
        y += int(size * 0.024)
        for line in s_lines:
            draw_rtl(draw, line, f_s, right=right, top=y, fill=colors["muted"])
            y += sub_lh


def _layout_statement(img, draw, cfg, *, size, accent, headline, subline,
                      kicker, margin) -> None:
    """تیتر وسط‌چین بین دو خط افقی — برای جملات کوتاه و قاطع."""
    colors = cfg["brand"]["colors"]
    max_w = int(size * 0.80)
    cx = size // 2

    f_h, h_lines = _fit_headline(draw, headline, size, max_w,
                                 start=0.088, floor=0.048, max_lines=4)
    head_lh = int(f_h.size * 1.28)
    f_s = font("regular", int(size * 0.034))
    s_lines = wrap_rtl(draw, subline, f_s, max_w) if subline else []
    sub_lh = int(f_s.size * 1.55)

    rule_gap = int(size * 0.045)
    block = (len(h_lines) * head_lh
             + 2 * rule_gap + 2 * max(2, size // 400)
             + ((int(size * 0.028) + len(s_lines) * sub_lh) if s_lines else 0))
    y = (size - block) // 2 - int(size * 0.03)

    if kicker:
        f_k = font("bold", int(size * 0.026))
        kw = text_width(draw, kicker, f_k)
        draw_rtl(draw, kicker, f_k, right=int(cx + kw / 2),
                 top=y - int(size * 0.075), fill=accent)

    rule_w = int(size * 0.14)
    lw = max(2, size // 400)
    draw.line([(cx - rule_w // 2, y), (cx + rule_w // 2, y)], fill=accent, width=lw)
    y += rule_gap

    for line in h_lines:
        w = text_width(draw, line, f_h)
        draw_rtl(draw, line, f_h, right=int(cx + w / 2), top=y, fill=colors["text"])
        y += head_lh

    if s_lines:
        y += int(size * 0.028)
        for line in s_lines:
            w = text_width(draw, line, f_s)
            draw_rtl(draw, line, f_s, right=int(cx + w / 2), top=y,
                     fill=colors["muted"])
            y += sub_lh
        y += int(size * 0.010)

    y += rule_gap - int(size * 0.020)
    draw.line([(cx - rule_w // 2, y), (cx + rule_w // 2, y)], fill=accent, width=lw)


def _layout_numbered(img, draw, cfg, *, size, accent, headline, subline,
                     kicker, margin, number: str) -> None:
    """شماره‌ی درشت در دایره + تیتر — برای اسلایدهای کاروسل."""
    colors = cfg["brand"]["colors"]
    right, max_w = size - margin, size - 2 * margin

    # دایره‌ی شماره
    d_size = int(size * 0.135)
    cx, cy = right - d_size // 2, int(size * 0.235)
    draw.ellipse([cx - d_size // 2, cy - d_size // 2,
                  cx + d_size // 2, cy + d_size // 2],
                 outline=accent, width=max(3, size // 280))
    f_n = font("black", int(d_size * 0.50))
    nw = text_width(draw, number, f_n)
    draw_rtl(draw, number, f_n, right=int(cx + nw / 2),
             top=cy - int(f_n.size * 0.62), fill=accent)

    f_h, h_lines = _fit_headline(draw, headline, size, max_w,
                                 start=0.072, floor=0.042, max_lines=3)
    head_lh = int(f_h.size * 1.30)
    f_s = font("regular", int(size * 0.034))
    s_lines = wrap_rtl(draw, subline, f_s, max_w) if subline else []
    sub_lh = int(f_s.size * 1.55)

    block = (len(h_lines) * head_lh
             + ((int(size * 0.026) + len(s_lines) * sub_lh) if s_lines else 0))
    y = max(size - margin - int(size * 0.095) - block, int(size * 0.40))

    for line in h_lines:
        draw_rtl(draw, line, f_h, right=right, top=y, fill=colors["text"])
        y += head_lh
    if s_lines:
        y += int(size * 0.026)
        for line in s_lines:
            draw_rtl(draw, line, f_s, right=right, top=y, fill=colors["muted"])
            y += sub_lh


LAYOUTS = ("editorial", "statement", "numbered")


def render_card(cfg: dict[str, Any], *, headline: str, subline: str = "",
                kicker: str = "", product_image: Path | None = None,
                out_path: Path, layout: str = "editorial",
                accent: str | None = None,
                index: int | None = None, total: int | None = None) -> Path:
    size = int(cfg["image"]["size"])
    colors = cfg["brand"]["colors"]
    accent = accent or colors["accent"]
    margin = int(size * 0.088)

    img = background(cfg, size, product_image, accent)
    draw = ImageDraw.Draw(img)

    _corner_frame(draw, size, int(margin * 0.62), _mix(colors["muted"], colors["background"], 0.45),
                  length=int(size * 0.055), width=max(2, size // 460))
    _paste_logo(img, cfg["image"].get("logo_path", ""), size)

    if layout == "statement":
        _layout_statement(img, draw, cfg, size=size, accent=accent, headline=headline,
                          subline=subline, kicker=kicker, margin=margin)
    elif layout == "numbered" and index:
        _layout_numbered(img, draw, cfg, size=size, accent=accent, headline=headline,
                         subline=subline, kicker=kicker, margin=margin,
                         number=jalali_digits(index))
    else:
        _layout_editorial(img, draw, cfg, size=size, accent=accent, headline=headline,
                          subline=subline, kicker=kicker, margin=margin)

    if index and total and total > 1:
        _progress_dots(draw, size, index=index, total=total,
                       y=size - int(margin * 0.55), accent=accent,
                       muted=_mix(colors["muted"], colors["background"], 0.55))

    _brand_footer(draw, cfg, size, margin, accent)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def jalali_digits(n: int) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def pick_product(cfg: dict[str, Any]) -> Path | None:
    d = ROOT / cfg["image"].get("products_dir", "assets/products")
    if not d.exists():
        return None
    files = [p for p in d.iterdir()
             if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(files) if files else None


# قالب بصری هر نوع پست. هدف: فید یکنواخت نشود ولی هویت برند حفظ شود.
PLAN_LAYOUT = {
    "product_spotlight": "editorial",
    "offer":             "editorial",
    "trust":             "editorial",
    "behind_scenes":     "editorial",
    "myth_buster":       "statement",
    "ethics":            "statement",
    "tip":               "numbered",
    "comparison":        "numbered",
    "faq":               "numbered",
}

# چرخش ملایم رنگ تأکید — تنوع بدون خروج از هویت برند
PLAN_HUE = {
    "tip": -14, "comparison": 10, "faq": -6,
    "myth_buster": 18, "ethics": -20, "trust": 6,
    "product_spotlight": 0, "offer": 14, "behind_scenes": 0,
}


def build_images(cfg: dict[str, Any], content: dict[str, Any], *,
                 post_id: int, out_dir: Path,
                 name_prefix: str | None = None,
                 plan_key: str = "") -> list[Path]:
    """بر اساس محتوا، یک یا چند تصویر می‌سازد و مسیرها را برمی‌گرداند."""
    product = pick_product(cfg)
    paths: list[Path] = []
    stem = name_prefix or f"post_{post_id:05d}"

    base_accent = cfg["brand"]["colors"]["accent"]
    accent = _shift_hue(base_accent, PLAN_HUE.get(plan_key, 0))
    layout = PLAN_LAYOUT.get(plan_key, "editorial")

    if "slides" in content:  # کاروسل
        slides = content["slides"]
        total = len(slides)
        for i, slide in enumerate(slides, 1):
            p = out_dir / f"{stem}_{i:02d}.jpg"
            # اسلاید اول جلد است: تیتر وسط‌چین و بدون شماره
            first = i == 1
            render_card(cfg,
                        headline=slide.get("headline", ""),
                        subline=slide.get("subline", ""),
                        kicker="" if first else slide.get("kicker", ""),
                        product_image=product if first else None,
                        out_path=p,
                        layout="statement" if first else layout,
                        accent=accent, index=i, total=total)
            paths.append(p)
    else:  # تک‌تصویر
        p = out_dir / f"{stem}_01.jpg"
        render_card(cfg,
                    headline=content.get("headline", content.get("title", "")),
                    subline=content.get("subline", ""),
                    kicker=content.get("kicker", ""),
                    product_image=product,
                    out_path=p, layout=layout, accent=accent)
        paths.append(p)

    return paths
