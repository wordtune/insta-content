"""حالت buffer — خودکارسازی کامل تا لحظه‌ی انتشار.

    ربات محتوا را می‌سازد
        ← تصاویر را روی یک آدرس عمومی آپلود می‌کند
            ← پست‌ها را با تاریخ و ساعت در صف بافر می‌گذارد
                ← بافر سر ساعت روی اینستاگرام منتشر می‌کند

هیچ مرحله‌ی دستی‌ای باقی نمی‌ماند. تلگرام فقط گزارش می‌دهد.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from . import batch, jalali, llm
from .buffer_api import BufferClient
from .config import Settings
from .storage import get_storage
from .telegram import from_settings as telegram_from_settings

log = logging.getLogger("autopost")


def run_schedule(settings: Settings, *, count: int,
                 start: date | None = None) -> dict[str, Any]:
    if not llm.api_key(settings):
        raise SystemExit("کلید مدل زبانی تنظیم نشده است. «python main.py doctor» را اجرا کنید.")

    dry = settings.dry_run
    start = start or (date.today() + timedelta(days=1))
    out_dir = batch._fresh_out_dir("schedule")

    tg = telegram_from_settings(settings)
    storage = get_storage(settings)
    buf_cfg = settings.get("buffer", {}) or {}

    # ۱) کانال را قبل از تولید محتوا پیدا کن — اگر مشکلی هست همین اول معلوم شود.
    #    در حالت آزمایشی هم این کار انجام می‌شود (فقط خواندنی است) تا مطمئن شویم
    #    اتصال و کانال درست‌اند؛ فقط ثبت پست انجام نمی‌شود.
    client = BufferClient(settings.buffer_token)
    channel = client.instagram_channel(
        organization_id=buf_cfg.get("organization_id", "") or "",
        name=buf_cfg.get("channel_name", "") or "",
    )
    log.info("کانال بافر: %s (%s)", channel.get("name"), channel.get("id"))

    if tg.enabled:
        head = (f"🗓 در حال زمان‌بندی {count} پست\n"
                f"از {jalali.format_date(start)} تا "
                f"{jalali.format_date(start + timedelta(days=count - 1))}")
        head += f"\nکانال: {channel.get('name')}"
        if dry:
            head += "\n\n⚠️ حالت آزمایشی — چیزی در بافر ثبت نمی‌شود."
        tg.text(head)

    scheduled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    # ۲) هر پست به‌محض آماده شدن، آپلود و زمان‌بندی می‌شود
    def handle(entry: dict[str, Any], total: int) -> None:
        if dry:
            log.info("  (آزمایشی) %s — %s", entry["when"], entry["title"])
            entry["urls"] = []
            scheduled.append(entry)
            return

        try:
            urls = [storage.upload(p) for p in entry["images"]]
            entry["urls"] = urls
            post = client.create_post(
                channel_id=channel["id"],
                text=entry["caption"],
                image_urls=urls,
                due_at=entry["at"],
            )
            entry["buffer_post_id"] = post.get("id")
            scheduled.append(entry)
            log.info("  ✅ در صف بافر: %s — %s", entry["when"], entry["title"])
        except Exception as e:
            entry["error"] = str(e)
            failed.append(entry)
            log.error("  ❌ پست %d زمان‌بندی نشد: %s", entry["n"], e)

    posts = batch.prepare_posts(settings, count=count, start=start,
                                out_dir=out_dir, on_ready=handle)
    if not posts:
        raise SystemExit("هیچ پستی ساخته نشد. لاگ بالا را ببینید.")

    sheet, archive = batch._write_sidecars(settings, posts, out_dir)

    # ۳) گزارش
    if tg.enabled:
        lines = [f"{'✅' if not failed else '⚠️'} زمان‌بندی تمام شد",
                 f"موفق: {len(scheduled)} از {len(posts)}"]
        if failed:
            lines.append(f"ناموفق: {len(failed)}")
        lines.append("")
        for p in scheduled[:15]:
            lines.append(f"• {p['when']} — {p['title']}")
        if len(scheduled) > 15:
            lines.append(f"… و {len(scheduled) - 15} پست دیگر")
        if failed:
            lines.append("\nپست‌های ناموفق:")
            for p in failed[:5]:
                lines.append(f"✗ {p['when']} — {p.get('error', '')[:120]}")
        if not dry:
            lines.append("\nبرای دیدن یا ویرایش صف: publish.buffer.com")
        try:
            tg.text("\n".join(lines))
        except Exception as e:
            log.warning("ارسال گزارش به تلگرام ناموفق: %s", e)

    return {"count": len(posts), "scheduled": len(scheduled), "failed": len(failed),
            "dir": str(out_dir), "zip": archive, "sheet": str(sheet),
            "channel": channel.get("name", ""), "dry_run": dry,
            "posts": posts, "failures": failed}
