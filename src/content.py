"""تولید محتوای پست (عنوان، متن اسلایدها، کپشن، هشتگ) با مدل زبانی.

سرویس مدل در config.yaml بخش llm انتخاب می‌شود — این فایل به هیچ ارائه‌دهنده‌ای
گره نخورده است.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import llm

SYSTEM_PROMPT = """تو یک کپی‌رایتر حرفه‌ای شبکه‌های اجتماعی هستی که برای پیج‌های فروشگاهی فارسی‌زبان محتوا می‌نویسی.

قوانین سختگیرانه:
1. فقط و فقط یک شیء JSON معتبر برگردان. هیچ متن دیگری، هیچ ```، هیچ توضیحی.
2. هرگز آمار، درصد، جایزه، گارانتی یا ادعایی که در اطلاعات داده‌شده نیست از خودت نساز.
3. هرگز درباره موضوعات ممنوعه‌ی اعلام‌شده چیزی ننویس.
4. متن اسلایدها باید کوتاه و بصری باشد؛ اسلاید = یک ایده.
5. کپشن با یک قلاب یک‌خطی شروع شود، بعد بدنه، بعد دعوت به اقدام.
6. هشتگ‌ها مرتبط و فارسی/انگلیسیِ رایج باشند؛ هشتگ بی‌ربط یا اسپم ممنوع.
7. از ایموجی کم و هدفمند استفاده کن (حداکثر ۴ تا در کل کپشن).
8. عنوان نباید شبیه هیچ‌کدام از عناوین اخیر باشد."""

SCHEMA_SINGLE = """{
  "title": "عنوان کوتاه داخلی برای بایگانی (حداکثر ۶۰ کاراکتر)",
  "headline": "متن درشت روی تصویر (حداکثر ۴۵ کاراکتر)",
  "subline": "زیرنویس روی تصویر (حداکثر ۹۰ کاراکتر)",
  "caption": "کپشن کامل پست",
  "hashtags": ["هشتگ۱", "هشتگ۲"],
  "alt_text": "توضیح تصویر برای دسترس‌پذیری (حداکثر ۱۰۰ کاراکتر)",
  "image_prompt": "توصیف انگلیسی صحنه تصویر، فقط اگر حالت تولید تصویر AI باشد"
}"""

SCHEMA_CAROUSEL = """{
  "title": "عنوان کوتاه داخلی برای بایگانی (حداکثر ۶۰ کاراکتر)",
  "slides": [
    {"headline": "متن درشت اسلاید (حداکثر ۴۵ کاراکتر)",
     "subline": "توضیح کوتاه اسلاید (حداکثر ۱۲۰ کاراکتر)",
     "kicker": "برچسب کوچک بالای اسلاید مثل «۱ از ۵» یا «نکته»"}
  ],
  "caption": "کپشن کامل پست",
  "hashtags": ["هشتگ۱", "هشتگ۲"],
  "alt_text": "توضیح تصویر برای دسترس‌پذیری (حداکثر ۱۰۰ کاراکتر)"
}"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"پاسخ مدل JSON معتبر نبود:\n{text[:500]}")
    return json.loads(text[start:end + 1])


_CATALOG_FIELDS = [
    ("category", "دسته"),
    ("price", "قیمت"),
    ("duration", "مدت"),
    ("what_customer_gets", "مشتری چه تحویل می‌گیرد"),
    ("authorized", "وضعیت نمایندگی"),
    ("guarantee", "گارانتی"),
    ("best_for", "مناسب برای"),
    ("note", "نکته"),
]


def _catalog_block(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return "(کاتالوگ محصولی ارائه نشده — محتوای عمومی مرتبط با حوزه پیج بنویس.)"
    blocks = []
    for p in catalog:
        lines = [f"### {p.get('name', '')}"]
        for key, label in _CATALOG_FIELDS:
            value = p.get(key)
            if value:
                lines.append(f"{label}: {value}")
        if p.get("features"):
            lines.append("ویژگی‌ها: " + "، ".join(p["features"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def generate(settings, plan_item: dict[str, Any], *, recent_titles: list[str],
             catalog: list[dict[str, Any]]) -> dict[str, Any]:
    brand = settings["brand"]
    safety = settings["safety"]

    is_carousel = plan_item["type"] == "carousel"
    n_slides = int(plan_item.get("slides", 5)) if is_carousel else 1
    schema = SCHEMA_CAROUSEL if is_carousel else SCHEMA_SINGLE

    user_prompt = f"""## اطلاعات برند
نام: {brand['name']}
حوزه: {brand['niche']}
مخاطب: {brand['audience']}
لحن: {brand['tone']}
دعوت به اقدام: {brand.get('cta') or '(ندارد)'}
وب‌سایت: {brand.get('website') or '(ندارد)'}
موضوعات ممنوعه: {'، '.join(brand.get('banned_topics', [])) or '(ندارد)'}

## کاتالوگ محصولات (تنها منبع مجاز برای قیمت و ویژگی)
{_catalog_block(catalog)}

## نوع پست امروز
دسته: {plan_item['label']}
بریف: {plan_item['brief']}
قالب: {'کاروسل با دقیقاً ' + str(n_slides) + ' اسلاید' if is_carousel else 'تک‌تصویر'}

## عناوین پست‌های اخیر (تکراری ننویس، زاویه تازه پیدا کن)
{chr(10).join('- ' + t for t in recent_titles) or '(هنوز پستی منتشر نشده)'}

## محدودیت‌ها
- کپشن حداکثر {safety['max_caption_chars']} کاراکتر
- حداکثر {safety['max_hashtags']} هشتگ
- زبان: {'فارسی' if brand.get('language') == 'fa' else brand.get('language')}

## خروجی
دقیقاً با این ساختار JSON پاسخ بده:
{schema}"""

    raw = llm.complete(settings, system=SYSTEM_PROMPT, user=user_prompt)
    data = _extract_json(raw)
    return _sanitize(data, settings, n_slides if is_carousel else 0)


def _sanitize(data: dict[str, Any], settings, n_slides: int) -> dict[str, Any]:
    safety = settings["safety"]
    brand = settings["brand"]

    # هشتگ‌ها: یکتا، با #، محدود به سقف
    tags, seen = [], set()
    for t in data.get("hashtags", []) or []:
        t = str(t).strip().lstrip("#").replace(" ", "_")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            tags.append("#" + t)
    data["hashtags"] = tags[: int(safety["max_hashtags"])]

    # کپشن: افزودن CTA و هشتگ‌ها، سپس برش امن
    caption = (data.get("caption") or "").strip()
    cta = (brand.get("cta") or "").strip()
    if cta and cta not in caption:
        caption += f"\n\n{cta}"
    if data["hashtags"]:
        caption += "\n\n" + " ".join(data["hashtags"])
    limit = int(safety["max_caption_chars"])
    if len(caption) > limit:
        caption = caption[: limit - 1].rsplit(" ", 1)[0] + "…"
    data["caption"] = caption

    # کاروسل: تعداد اسلایدها را دقیقاً تنظیم کن (اینستاگرام حداکثر ۱۰ تا می‌پذیرد)
    if n_slides:
        slides = data.get("slides") or []
        slides = slides[: min(n_slides, 10)]
        while len(slides) < min(n_slides, 10):
            slides.append({"headline": brand["name"], "subline": "", "kicker": ""})
        for i, s in enumerate(slides, 1):
            s.setdefault("kicker", f"{i} از {len(slides)}")
        data["slides"] = slides

    title = (data.get("title") or data.get("headline")
             or (data.get("slides") or [{}])[0].get("headline") or "پست بدون عنوان")
    data["title"] = str(title).strip()[:60]
    return data
