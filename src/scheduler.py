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
from .buffer_api import BufferClient, BufferQuotaError
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

    # ۲) ظرفیت صف را قبل از تولید محتوا بسنج — تولید محتوایی که جا ندارد
    #    هم هزینه‌ی مدل را هدر می‌دهد هم گزارش را شلوغ می‌کند.
    max_queue = int(buf_cfg.get("max_queue", 10) or 10)
    in_queue = client.scheduled_count(buf_cfg.get("organization_id", "") or "")
    queue_note = ""
    if in_queue is not None:
        free = max(0, max_queue - in_queue)
        log.info("صف بافر: %d از %d پر است (%d جای خالی)", in_queue, max_queue, free)
        if free == 0 and not dry:
            msg = (f"صف بافر پر است ({in_queue} از {max_queue}). "
                   "تا انتشار پست‌های فعلی، جای تازه‌ای باز نمی‌شود.")
            log.warning(msg)
            if tg.enabled:
                tg.text(f"⏸ {msg}\n\nربات این هفته چیزی نساخت تا هزینه‌ی بی‌مورد ندهید.")
            return {"count": 0, "scheduled": 0, "failed": 0, "dir": str(out_dir),
                    "zip": "", "sheet": "", "channel": channel.get("name", ""),
                    "dry_run": dry, "posts": [], "failures": [], "skipped": msg}
        if not dry and count > free:
            queue_note = (f"صف بافر {in_queue} از {max_queue} پر است، "
                          f"پس به‌جای {count} پست، {free} پست ساخته می‌شود.")
            log.warning(queue_note)
            count = free

    if tg.enabled:
        head = (f"🗓 در حال زمان‌بندی {count} پست\n"
                f"از {jalali.format_date(start)} تا "
                f"{jalali.format_date(start + timedelta(days=count - 1))}")
        head += f"\nکانال: {channel.get('name')}"
        if queue_note:
            head += f"\n\nℹ️ {queue_note}"
        if dry:
            head += "\n\n⚠️ حالت آزمایشی — چیزی در بافر ثبت نمی‌شود."
        tg.text(head)

    scheduled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    quota_hit: list[str] = []

    # ۳) هر پست به‌محض آماده شدن، آپلود و زمان‌بندی می‌شود
    def handle(entry: dict[str, Any], total: int) -> None:
        if dry:
            log.info("  (آزمایشی) %s — %s", entry["when"], entry["title"])
            entry["urls"] = []
            scheduled.append(entry)
            return

        if quota_hit:
            # صف پر شده — تلاش بی‌فایده نکن، ولی محتوا در پوشه می‌ماند
            entry["error"] = "صف بافر پر شد؛ این پست ساخته شد ولی زمان‌بندی نشد."
            failed.append(entry)
            return

        try:
            urls = [storage.upload(p) for p in entry["images"]]
            entry["urls"] = urls
            post = client.create_post(
                channel_id=channel["id"],
                text=entry["caption"],
                image_urls=urls,
                due_at=entry["at"],
                service=channel.get("service", "instagram"),
                alt_text=entry.get("alt_text", ""),
            )
            entry["buffer_post_id"] = post.get("id")
            scheduled.append(entry)
            log.info("  ✅ در صف بافر: %s — %s", entry["when"], entry["title"])
        except BufferQuotaError as e:
            quota_hit.append(str(e))
            entry["error"] = str(e)
            failed.append(entry)
            log.warning("  ⏸ صف بافر پر شد — بقیه‌ی پست‌ها زمان‌بندی نمی‌شوند.")
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
        if quota_hit:
            lines.append(f"\n⏸ صف بافر پر شد (سقف {max_queue} پست).")
            lines.append("پست‌های ساخته‌شده در بایگانی گیت‌هاب هستند و از دست نرفته‌اند.")
            lines.append("وقتی چند پست منتشر شد، دوباره اجرا کنید.")
        elif failed:
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
