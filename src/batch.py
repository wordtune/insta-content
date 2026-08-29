"""حالت دسته‌ای: تولید یک‌جای محتوای چند هفته، بدون نیاز به API متا.

چرا این حالت وجود دارد:
پلتفرم توسعه‌دهندگان متا از ایران در دسترس نیست، پس نمی‌شود مستقیم از طریق API
منتشر کرد. اما خود اینستاگرام زمان‌بندی داخلی دارد (تا ۷۵ روز جلوتر). پس:

    ربات یک‌جا محتوای یک ماه را می‌سازد
        ← در تلگرام تحویلتان می‌دهد
            ← شما یک بار همه را در اپ اینستاگرام زمان‌بندی می‌کنید
                ← اینستاگرام هر روز خودش منتشر می‌کند

سخت‌ترین بخش کار خودکار می‌ماند؛ فقط مرحله‌ی آخر دستی است.
"""
from __future__ import annotations

import html
import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import content as content_mod
from . import db, imagegen, jalali, llm
from .config import Settings
from .pipeline import load_catalog
from .telegram import from_settings as telegram_from_settings

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("autopost")


def post_datetime(settings: Settings, day: date) -> datetime:
    """تاریخ + ساعت انتشار، با منطقه‌ی زمانی تنظیم‌شده."""
    sched = settings.get("schedule", {})
    hh, _, mm = str(sched.get("post_at", "21:00")).partition(":")
    try:
        tz = ZoneInfo(sched.get("timezone") or "Asia/Tehran")
    except Exception:
        tz = ZoneInfo("Asia/Tehran")
    return datetime(day.year, day.month, day.day,
                    int(hh or 21), int(mm or 0), tzinfo=tz)


def prepare_posts(settings: Settings, *, count: int, start: date,
                  out_dir: Path, on_ready=None) -> list[dict[str, Any]]:
    """محتوای count پست را می‌سازد و فهرستشان را برمی‌گرداند.

    on_ready اگر داده شود، بلافاصله بعد از آماده شدن هر پست صدا زده می‌شود —
    این‌طور تحویل (تلگرام یا بافر) همراه تولید پیش می‌رود و کاربر منتظر نمی‌ماند.
    """
    plan = settings["content_plan"]
    post_at = settings.get("schedule", {}).get("post_at", "21:00")
    conn = db.connect()
    posts: list[dict[str, Any]] = []

    for i in range(count):
        plan_item = plan[db.next_plan_index(conn, len(plan))]
        when = start + timedelta(days=i)
        log.info("[%d/%d] %s — %s", i + 1, count, plan_item["label"],
                 jalali.format_date(when))

        try:
            data = content_mod.generate(
                settings, plan_item,
                recent_titles=db.recent_titles(conn, int(settings["llm"]["history_window"])),
                catalog=load_catalog(),
            )
        except Exception as e:
            log.error("تولید محتوای پست %d ناموفق بود: %s", i + 1, e)
            continue

        media_type = "CAROUSEL" if "slides" in data else "IMAGE"
        post_id = db.create_draft(conn, plan_key=plan_item["key"],
                                  media_type=media_type, content=data)

        images = imagegen.build_images(
            settings.raw, data, post_id=post_id, out_dir=out_dir,
            name_prefix=f"{i + 1:02d}",
        )
        db.attach_media(conn, post_id, [str(p) for p in images], [])
        db.advance_plan_index(conn, len(plan))

        (out_dir / f"{i + 1:02d}_caption.txt").write_text(data["caption"], encoding="utf-8")

        entry = {
            "n": i + 1, "post_id": post_id, "plan_key": plan_item["key"],
            "label": plan_item["label"], "type": media_type,
            "title": data.get("title", ""), "caption": data["caption"],
            "alt_text": data.get("alt_text", ""),
            "images": images, "date": when, "at": post_datetime(settings, when),
            "when": f"{jalali.format_date(when)} ساعت {jalali.fa_digits(post_at)}",
        }
        posts.append(entry)

        if on_ready:
            try:
                on_ready(entry, count)
            except Exception as e:
                log.warning("تحویل پست %d ناموفق: %s", i + 1, e)

    return posts


def _fresh_out_dir(kind: str) -> Path:
    out_dir = ROOT / "output" / f"{kind}_{date.today():%Y-%m-%d}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    return out_dir


def _write_sidecars(settings: Settings, posts: list[dict[str, Any]],
                    out_dir: Path) -> tuple[Path, str]:
    sheet = out_dir / "index.html"
    sheet.write_text(_render_sheet(settings, posts), encoding="utf-8")
    (out_dir / "captions.txt").write_text(
        "\n\n".join(f"{'=' * 50}\nپست {p['n']} — {p['label']} — {p['when']}\n"
                    f"{'=' * 50}\n{p['caption']}" for p in posts),
        encoding="utf-8",
    )
    return sheet, shutil.make_archive(str(out_dir), "zip", root_dir=out_dir)


# ------------------------------------------------------------------ حالت batch

def run_batch(settings: Settings, *, count: int, start: date | None = None,
              deliver: bool = True) -> dict[str, Any]:
    if not llm.api_key(settings):
        raise SystemExit("کلید مدل زبانی تنظیم نشده است. «python main.py doctor» را اجرا کنید.")

    start = start or (date.today() + timedelta(days=1))
    out_dir = _fresh_out_dir("batch")
    tg = telegram_from_settings(settings)
    if deliver and not tg.enabled:
        log.warning("تلگرام تنظیم نشده — فایل‌ها فقط در پوشه‌ی خروجی می‌مانند.")
        deliver = False

    if deliver:
        tg.text(
            f"🗂 بسته‌ی محتوای جدید — {count} پست\n"
            f"از {jalali.format_date(start)} تا "
            f"{jalali.format_date(start + timedelta(days=count - 1))}\n\n"
            "برای هر پست، اول تصویرها می‌آید و بعد کپشن در یک پیام جدا "
            "(روی پیام نگه دارید تا «کپی» بیاید)."
        )

    def deliver_one(entry: dict[str, Any], total: int) -> None:
        tg.deliver_post(index=entry["n"], total=total, label=entry["label"],
                        images=entry["images"], caption=entry["caption"],
                        scheduled_for=entry["when"])

    posts = prepare_posts(settings, count=count, start=start, out_dir=out_dir,
                          on_ready=deliver_one if deliver else None)
    if not posts:
        raise SystemExit("هیچ پستی ساخته نشد. لاگ بالا را ببینید.")

    sheet, archive = _write_sidecars(settings, posts, out_dir)

    if deliver:
        try:
            tg.document(Path(archive),
                        f"📦 همه‌ی {len(posts)} پست در یک فایل — "
                        "اگر خواستید روی کامپیوتر کار کنید.")
            tg.text("✅ بسته کامل شد.\n\nحالا در اپ اینستاگرام: دکمه‌ی + ← "
                    "انتخاب تصویر ← کپشن را بچسبانید ← Advanced settings ← "
                    "Schedule this post ← تاریخ و ساعت را بگذارید.")
        except Exception as e:
            log.warning("ارسال فایل نهایی ناموفق: %s", e)

    return {"count": len(posts), "dir": str(out_dir), "zip": archive,
            "sheet": str(sheet), "posts": posts}


# ---------------------------------------------------------------- برگه‌ی خلاصه

def _render_sheet(settings: Settings, posts: list[dict[str, Any]]) -> str:
    brand = settings["brand"]
    rows = []
    for p in posts:
        imgs = "".join(
            f'<img src="{html.escape(img.name)}" alt="">' for img in p["images"]
        )
        rows.append(f"""
    <article>
      <header>
        <span class="n">{jalali.fa_digits(p['n'])}</span>
        <div>
          <h2>{html.escape(p['title'])}</h2>
          <p class="meta">{html.escape(p['label'])} ·
             {'کاروسل' if p['type'] == 'CAROUSEL' else 'تک‌تصویر'} ·
             {html.escape(p['when'])}</p>
        </div>
      </header>
      <div class="shots">{imgs}</div>
      <button class="copy" data-caption="{html.escape(p['caption'])}">کپی کپشن</button>
      <pre>{html.escape(p['caption'])}</pre>
    </article>""")

    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>بسته‌ی محتوا — {html.escape(brand['name'])}</title>
<style>
  :root {{ color-scheme: light dark; --bg:#F1F2EE; --card:#FBFCF9; --ink:#15181A;
           --ink2:#4A5250; --rule:#D3D7CE; --accent:#1C6B4A; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0E1211; --card:#161B19; --ink:#E9ECE7; --ink2:#A8B2AE;
             --rule:#2A312E; --accent:#5FCF95; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.8;
         font-family:Vazirmatn,"Segoe UI",Tahoma,sans-serif; padding:1.5rem 1rem 4rem; }}
  .wrap {{ max-width:52rem; margin:0 auto; }}
  h1 {{ font-size:1.6rem; margin:0 0 .3rem; }}
  .lede {{ color:var(--ink2); margin:0 0 2rem; font-size:.95rem; }}
  article {{ background:var(--card); border:1px solid var(--rule); border-radius:6px;
             padding:1.1rem; margin-bottom:1.2rem; }}
  header {{ display:flex; gap:.9rem; align-items:flex-start; margin-bottom:.9rem; }}
  .n {{ flex:0 0 auto; width:2rem; height:2rem; border-radius:50%; background:var(--accent);
        color:var(--card); display:grid; place-items:center; font-weight:700; font-size:.85rem; }}
  h2 {{ font-size:1.05rem; margin:0; }}
  .meta {{ margin:.1rem 0 0; font-size:.82rem; color:var(--ink2); }}
  .shots {{ display:flex; gap:.5rem; overflow-x:auto; padding-bottom:.4rem; }}
  .shots img {{ width:150px; height:150px; object-fit:cover; border-radius:4px;
                border:1px solid var(--rule); flex:0 0 auto; }}
  pre {{ white-space:pre-wrap; font-family:inherit; font-size:.9rem; background:transparent;
         border-top:1px solid var(--rule); margin:.8rem 0 0; padding-top:.8rem; }}
  button.copy {{ margin-top:.8rem; background:var(--accent); color:var(--card); border:0;
                 border-radius:4px; padding:.45rem 1rem; font-family:inherit;
                 font-size:.85rem; cursor:pointer; }}
  button.copy:focus-visible {{ outline:2px solid var(--ink); outline-offset:2px; }}
</style></head><body><div class="wrap">
<h1>بسته‌ی محتوا — {html.escape(brand['name'])}</h1>
<p class="lede">{jalali.fa_digits(len(posts))} پست آماده — تصویرها در همین پوشه‌اند.<br>
در اپ اینستاگرام: دکمه‌ی + ← انتخاب تصویر ← چسباندن کپشن ← بخش
<span dir="ltr">Advanced settings</span> ← گزینه‌ی
<span dir="ltr">Schedule this post</span> ← انتخاب تاریخ و ساعت</p>
{''.join(rows)}
</div>
<script>
document.querySelectorAll('button.copy').forEach(function (b) {{
  b.addEventListener('click', function () {{
    navigator.clipboard.writeText(b.dataset.caption).then(function () {{
      var old = b.textContent; b.textContent = 'کپی شد ✓';
      setTimeout(function () {{ b.textContent = old; }}, 1600);
    }});
  }});
}});
</script></body></html>"""
