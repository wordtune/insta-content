#!/usr/bin/env python3
"""
ربات انتشار خودکار پست اینستاگرام
=================================

سه حالت کار (در config.yaml با کلید mode انتخاب می‌شود):

  ▸ buffer — خودکار کامل، بدون نیاز به حساب متا
    محتوا ساخته می‌شود، تصاویر آپلود می‌شوند و پست‌ها با تاریخ و ساعت در صف
    بافر می‌روند. بافر سر ساعت روی اینستاگرام منتشر می‌کند.

        python main.py schedule              # ۷ پست برای یک هفته
        python main.py schedule --count 30   # یک ماه
        python main.py channels              # فهرست کانال‌های بافر

  ▸ batch — بدون هیچ سرویس واسطی (پشتیبان)
    محتوا در تلگرام تحویل داده می‌شود و خودتان در اپ اینستاگرام زمان‌بندی می‌کنید.

        python main.py batch --count 7
        python main.py batch --no-telegram   # فقط بساز، چیزی نفرست

  ▸ api — انتشار مستقیم از طریق متا (نیاز به حساب توسعه‌دهنده‌ی متا)

        python main.py preview               # ساخت یک پست بدون انتشار
        python main.py run                   # ساخت و انتشار
        python main.py quota                 # سهمیه انتشار ۲۴ ساعته
        python main.py refresh-token         # تمدید توکن ۶۰ روزه

  ▸ مشترک
        python main.py doctor                # بررسی سلامت پیکربندی
        python main.py history               # فهرست پست‌های اخیر
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime

from src import db
from src.batch import run_batch
from src.buffer_api import BufferClient, BufferError
from src.config import load_settings, validate
from src.instagram import InstagramClient, InstagramError, refresh_long_lived_token
from src.pipeline import run_once
from src.scheduler import run_schedule


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def _client(settings) -> InstagramClient:
    cfg = settings["instagram"]
    return InstagramClient(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token,
        api_version=cfg["api_version"], flavor=cfg["api_flavor"],
    )


def cmd_run(args, settings) -> int:
    result = run_once(settings, force_plan_key=args.plan)
    print("\n" + "=" * 60)
    if result.get("skipped"):
        print("رد شد:", result["reason"])
        return 0
    print(f"دسته:      {result['plan']}")
    print(f"نوع:       {result['media_type']}")
    print(f"عنوان:     {result['title']}")
    print(f"تصاویر:    {len(result['images'])} فایل در پوشه output/")
    for p in result["images"]:
        print("           " + p)
    print("-" * 60)
    print(result["caption"])
    print("=" * 60)
    if result.get("permalink"):
        print("لینک پست:", result["permalink"])
    elif result["dry_run"]:
        print("حالت آزمایشی بود — برای انتشار واقعی safety.dry_run را false کنید.")
    return 0


def _parse_start(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit("فرمت تاریخ باید YYYY-MM-DD باشد، مثل 2026-09-05")


def cmd_schedule(args, settings) -> int:
    result = run_schedule(settings, count=args.count, start=_parse_start(args.start))

    print("\n" + "=" * 62)
    print(f"کانال بافر: {result['channel']}")
    print(f"ساخته‌شده: {result['count']}  ·  زمان‌بندی‌شده: {result['scheduled']}"
          + (f"  ·  ناموفق: {result['failed']}" if result["failed"] else ""))
    print("-" * 62)
    for p in result["posts"]:
        mark = "✗" if p.get("error") else ("○" if result["dry_run"] else "✓")
        kind = "کاروسل" if p["type"] == "CAROUSEL" else "تک‌تصویر"
        print(f"  {mark} {p['when']:<28} {p['label']:<20} {kind:<9} {p['title']}")
    print("=" * 62)

    if result["dry_run"]:
        print("حالت آزمایشی بود — اتصال و کانال بررسی شد ولی چیزی در بافر ثبت نشد.")
        print("برای ثبت واقعی، در config.yaml مقدار safety.dry_run را false کنید.")
    else:
        print("صف بافر: https://publish.buffer.com")

    for p in result["failures"][:5]:
        print(f"\n✗ پست {p['n']}: {p.get('error')}")
    return 1 if result["failed"] else 0


def cmd_channels(args, settings) -> int:
    client = BufferClient(settings.buffer_token)
    orgs = client.organizations()
    if not orgs:
        print("هیچ سازمانی پیدا نشد.")
        return 1
    for org in orgs:
        print(f"\nسازمان: {org.get('name')}  (id: {org.get('id')})")
        chans = client.channels(org["id"])
        if not chans:
            print("  — هیچ کانالی وصل نیست")
            continue
        for c in chans:
            star = " ←" if (c.get("service") or "").lower() == "instagram" else ""
            print(f"  • {c.get('name'):<28} {c.get('service'):<12} "
                  f"id: {c.get('id')}{star}")
    print("\n(کانال‌های علامت‌خورده اینستاگرام هستند.)")
    return 0


def cmd_batch(args, settings) -> int:
    result = run_batch(settings, count=args.count, start=_parse_start(args.start),
                       deliver=not args.no_telegram)

    print("\n" + "=" * 60)
    print(f"{result['count']} پست ساخته شد.")
    print(f"پوشه:     {result['dir']}")
    print(f"فایل zip: {result['zip']}")
    print(f"برگه‌ی مرور: {result['sheet']}")
    print("-" * 60)
    for p in result["posts"]:
        kind = "کاروسل" if p["type"] == "CAROUSEL" else "تک‌تصویر"
        print(f"  {p['n']:>2}. {p['when']:<28} {p['label']:<20} {kind:<9} {p['title']}")
    print("=" * 60)
    if args.no_telegram:
        print("چیزی به تلگرام فرستاده نشد (--no-telegram).")
    return 0


MODE_LABEL = {
    "buffer": "خودکار کامل — از طریق بافر",
    "batch": "دسته‌ای — تحویل به تلگرام",
    "api": "انتشار مستقیم از طریق متا",
}


def cmd_doctor(args, settings) -> int:
    mode = settings.mode
    print("بررسی پیکربندی…\n")
    print(f"  حالت کار: {MODE_LABEL.get(mode, mode)}\n")

    problems = validate(settings)
    if problems:
        print("مشکلات یافت‌شده:")
        for p in problems:
            print("  ✗", p)
    else:
        print("  ✓ همه‌ی متغیرهای لازم تنظیم شده‌اند.")

    # تلگرام: در حالت batch حیاتی است، در بقیه فقط برای گزارش
    if settings.telegram_bot_token and settings.telegram_chat_id:
        from src.telegram import from_settings as _tg
        try:
            me = _tg(settings)._post("getMe")
            print(f"  ✓ ربات تلگرام متصل است: @{me.get('username')}")
        except Exception as e:
            print(f"  ✗ اتصال به تلگرام ناموفق: {e}")
            if mode == "batch":
                problems.append("اتصال به تلگرام برقرار نشد.")
    elif mode == "batch":
        print("  ✗ تلگرام تنظیم نشده — در حالت batch راه تحویل پست‌ها همین است.")
    else:
        print("  · تلگرام تنظیم نشده (اختیاری — فقط برای گزارش).")

    # بافر
    if mode == "buffer" and settings.buffer_token:
        try:
            client = BufferClient(settings.buffer_token)
            ch = client.instagram_channel(
                organization_id=(settings.get("buffer", {}) or {}).get("organization_id", ""),
                name=(settings.get("buffer", {}) or {}).get("channel_name", ""),
            )
            print(f"  ✓ بافر متصل است — کانال: {ch.get('name')} ({ch.get('service')})")
        except BufferError as e:
            print(f"  ✗ بافر: {e}")
            problems.append("اتصال به بافر برقرار نشد.")
        except Exception as e:
            print(f"  ✗ بافر: {e}")
            problems.append("اتصال به بافر برقرار نشد.")

    print(f"\n  تعداد الگوهای محتوایی: {len(settings['content_plan'])}")
    print(f"  حالت آزمایشی (dry_run): {settings.dry_run}")
    if mode in ("buffer", "api"):
        print(f"  میزبانی تصویر: {settings.get('storage', {}).get('provider')}")
    if mode == "api":
        print(f"  نوع API متا: {settings['instagram']['api_flavor']}")

    if mode == "api" and settings.ig_access_token:
        try:
            c = _client(settings)
            me = c.whoami()
            print(f"  ✓ توکن متعلق است به: @{me.get('username')} "
                  f"(شناسه {me.get('user_id') or me.get('id')})")
            q = c.publishing_quota()
            print(f"  ✓ اتصال برقرار — سهمیه انتشار: {q['used']} از {q['total']}")
        except Exception as e:
            print(f"  ✗ اتصال به اینستاگرام ناموفق: {e}")
            problems.append("اتصال به اینستاگرام برقرار نشد.")

    # بررسی فونت فارسی
    from src.imagegen import FONTS
    missing = [f for f in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf",
                           "Vazirmatn-Black.ttf") if not (FONTS / f).exists()]
    if missing:
        print(f"  ✗ فونت‌های فارسی موجود نیستند: {', '.join(missing)}")
    else:
        print("  ✓ فونت‌های فارسی موجودند.")
    return 0 if not problems else 1


def cmd_quota(args, settings) -> int:
    q = _client(settings).publishing_quota()
    print(f"سهمیه انتشار ۲۴ ساعته: {q['used']} از {q['total']}")
    conn = db.connect()
    print(f"ثبت‌شده در دیتابیس محلی: {db.published_last_24h(conn)}")
    return 0


def cmd_refresh_token(args, settings) -> int:
    data = refresh_long_lived_token(settings.ig_access_token)
    days = int(data.get("expires_in", 0)) // 86400
    print(f"توکن تمدید شد. اعتبار جدید: حدود {days} روز.")
    print("\nاین مقدار را در فایل .env جایگزین IG_ACCESS_TOKEN کنید:\n")
    print(data["access_token"])
    return 0


def cmd_history(args, settings) -> int:
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, created_at, plan_key, media_type, status, title, ig_media_id "
        "FROM posts ORDER BY id DESC LIMIT ?", (args.limit,)
    ).fetchall()
    if not rows:
        print("هنوز هیچ پستی ساخته نشده است.")
        return 0
    for r in rows:
        icon = {"published": "✅", "failed": "❌", "draft": "📝"}.get(r["status"], "•")
        print(f"{icon} #{r['id']:<4} {r['created_at'][:16]}  {r['plan_key']:<18} "
              f"{r['media_type']:<9} {r['title']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ربات انتشار خودکار پست اینستاگرام",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--config", default=None, help="مسیر فایل config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sched = sub.add_parser(
        "schedule", help="ساخت محتوا و زمان‌بندی خودکار در بافر")
    p_sched.add_argument("--count", type=int, default=7,
                         help="تعداد پست‌ها (پیش‌فرض ۷ = یک هفته)")
    p_sched.add_argument("--start", default=None,
                         help="تاریخ شروع، YYYY-MM-DD (پیش‌فرض: فردا)")

    sub.add_parser("channels", help="فهرست کانال‌های وصل‌شده به بافر")

    p_batch = sub.add_parser(
        "batch", help="ساخت یک‌جای محتوای چند هفته و ارسال به تلگرام")
    p_batch.add_argument("--count", type=int, default=30,
                         help="تعداد پست‌ها (پیش‌فرض ۳۰)")
    p_batch.add_argument("--start", default=None,
                         help="تاریخ شروع زمان‌بندی، YYYY-MM-DD (پیش‌فرض: فردا)")
    p_batch.add_argument("--no-telegram", action="store_true",
                         help="فقط بساز، به تلگرام نفرست")

    p_run = sub.add_parser("run", help="ساخت و انتشار پست امروز")
    p_run.add_argument("--plan", default=None, help="کلید نوع پست از content_plan")

    p_prev = sub.add_parser("preview", help="ساخت پست بدون انتشار")
    p_prev.add_argument("--plan", default=None)

    sub.add_parser("doctor", help="بررسی سلامت پیکربندی")
    sub.add_parser("quota", help="سهمیه انتشار ۲۴ ساعته")
    sub.add_parser("refresh-token", help="تمدید توکن بلندمدت")

    p_hist = sub.add_parser("history", help="فهرست پست‌های اخیر")
    p_hist.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    setup_logging(args.verbose)
    settings = load_settings(args.config)

    if args.command == "preview":
        settings.raw.setdefault("safety", {})["dry_run"] = True
        import os
        os.environ["DRY_RUN"] = "1"
        args.command = "run"

    handlers = {
        "schedule": cmd_schedule, "channels": cmd_channels, "batch": cmd_batch,
        "run": cmd_run, "doctor": cmd_doctor, "quota": cmd_quota,
        "refresh-token": cmd_refresh_token, "history": cmd_history,
    }
    try:
        return handlers[args.command](args, settings)
    except (InstagramError, BufferError, SystemExit) as e:
        print(f"\nخطا: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
