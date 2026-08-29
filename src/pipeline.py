"""ارکستراسیون: از انتخاب موضوع تا انتشار."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from . import content as content_mod
from . import db, imagegen
from .config import Settings, validate
from .instagram import InstagramClient, InstagramError
from .storage import get_storage

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("autopost")


def load_catalog() -> list[dict[str, Any]]:
    p = ROOT / "catalog.json"
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        items = json.load(f)
    # فیلدهای راهنما به مدل داده نمی‌شوند
    return [{k: v for k, v in item.items() if not k.startswith("_")} for item in items]


PLACEHOLDER_MARKS = ("???", "TODO", "xxx", "نمونه‌ی جایگزین‌شونده")


def check_catalog() -> list[str]:
    """مشکلات کاتالوگ را برمی‌گرداند.

    یک اشتباه رایج و پرهزینه: ویژگی‌های یک محصول از فایل نمونه برای محصول
    دیگری جا می‌ماند و ربات آن را در پست واقعی منتشر می‌کند.
    """
    p = ROOT / "catalog.json"
    problems: list[str] = []
    if not p.exists():
        return ["فایل catalog.json پیدا نشد."]

    try:
        with open(p, "r", encoding="utf-8") as f:
            items = json.load(f)
    except json.JSONDecodeError as e:
        return [f"catalog.json ساختار JSON معتبری ندارد: {e}"]

    if not isinstance(items, list) or not items:
        return ["catalog.json خالی است."]

    for i, item in enumerate(items, 1):
        name = item.get("name") or f"قلم {i}"
        if not item.get("name"):
            problems.append(f"قلم {i}: فیلد name خالی است.")

        blob = json.dumps(item, ensure_ascii=False)
        for mark in PLACEHOLDER_MARKS:
            if mark in blob:
                problems.append(f"«{name}»: هنوز مقدار جایگزین‌نشده دارد ({mark}).")
                break

        if not item.get("price"):
            problems.append(f"«{name}»: قیمت وارد نشده.")
        feats = item.get("features") or []
        if not feats:
            problems.append(f"«{name}»: هیچ ویژگی‌ای وارد نشده.")

    return problems


def notify(settings: Settings, text: str) -> None:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception as e:  # اطلاع‌رسانی نباید کل اجرا را خراب کند
        log.warning("ارسال تلگرام ناموفق: %s", e)


def run_once(settings: Settings, *, force_plan_key: str | None = None) -> dict[str, Any]:
    dry = settings.dry_run
    problems = validate(settings, need_publish=not dry)
    if problems:
        raise SystemExit("پیکربندی ناقص است:\n  - " + "\n  - ".join(problems))

    # فضای ذخیره‌سازی زودتر ساخته می‌شود چون ممکن است حافظه‌ی پست‌ها را هم نگه دارد
    storage = get_storage(settings)
    if getattr(storage, "supports_state", False):
        if storage.load_state(db.DB_PATH):
            log.info("حافظه‌ی پست‌ها از گیت‌هاب بازیابی شد.")

    conn = db.connect()
    plan = settings["content_plan"]

    # ۱) انتخاب نوع پست امروز
    if force_plan_key:
        matches = [p for p in plan if p["key"] == force_plan_key]
        if not matches:
            raise SystemExit(f"plan_key نامعتبر: {force_plan_key}")
        plan_item = matches[0]
    else:
        plan_item = plan[db.next_plan_index(conn, len(plan))]
    log.info("نوع پست امروز: %s (%s)", plan_item["label"], plan_item["type"])

    # ۲) بررسی سقف روزانه‌ی محلی
    cap = int(settings["instagram"]["daily_post_cap"])
    already = db.published_last_24h(conn)
    if already >= cap and not dry:
        msg = f"سقف روزانه پر شده ({already}/{cap}) — انتشار انجام نشد."
        log.warning(msg)
        return {"skipped": True, "reason": msg}

    # ۳) تولید محتوا
    log.info("در حال تولید محتوا با مدل زبانی…")
    data = content_mod.generate(
        settings, plan_item,
        recent_titles=db.recent_titles(conn, int(settings["llm"]["history_window"])),
        catalog=load_catalog(),
    )
    media_type = "CAROUSEL" if "slides" in data else "IMAGE"
    post_id = db.create_draft(conn, plan_key=plan_item["key"],
                              media_type=media_type, content=data)
    log.info("پیش‌نویس #%d ساخته شد: %s", post_id, data.get("title"))

    # ۴) ساخت تصاویر
    out_dir = ROOT / "output"
    image_paths = imagegen.build_images(settings.raw, data, post_id=post_id, out_dir=out_dir)
    log.info("%d تصویر ساخته شد.", len(image_paths))

    # ۵) آپلود روی URL عمومی
    urls = [storage.upload(p) for p in image_paths]
    db.attach_media(conn, post_id, [str(p) for p in image_paths], urls)

    result: dict[str, Any] = {
        "post_id": post_id, "plan": plan_item["label"], "media_type": media_type,
        "title": data.get("title"), "caption": data["caption"],
        "images": [str(p) for p in image_paths], "urls": urls, "dry_run": dry,
    }

    def _sync_state() -> None:
        if getattr(storage, "supports_state", False):
            try:
                storage.save_state(db.DB_PATH)
            except Exception as e:
                log.warning("ذخیره‌ی حافظه روی گیت‌هاب ناموفق: %s", e)

    if dry:
        log.info("حالت آزمایشی (dry_run) — چیزی منتشر نشد.")
        db.advance_plan_index(conn, len(plan))
        return result

    # ۶) انتشار
    ig_cfg = settings["instagram"]
    client = InstagramClient(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token,
        api_version=ig_cfg["api_version"], flavor=ig_cfg["api_flavor"],
    )
    quota = client.publishing_quota()
    log.info("سهمیه انتشار متا: %s از %s", quota["used"], quota["total"])
    if quota["used"] >= quota["total"]:
        msg = "سهمیه ۲۴ ساعته اینستاگرام پر شده است."
        db.mark_failed(conn, post_id, msg)
        notify(settings, f"⛔️ {msg}")
        return {**result, "skipped": True, "reason": msg}

    try:
        if media_type == "CAROUSEL":
            media_id = client.publish_carousel(urls, data["caption"])
        else:
            media_id = client.publish_single_image(
                urls[0], data["caption"], alt_text=data.get("alt_text")
            )
    except InstagramError as e:
        db.mark_failed(conn, post_id, str(e))
        _sync_state()
        notify(settings, f"❌ انتشار پست #{post_id} ناموفق بود:\n{e}")
        raise

    db.mark_published(conn, post_id, media_id)
    db.advance_plan_index(conn, len(plan))
    _sync_state()
    link = client.permalink(media_id)
    log.info("منتشر شد ✅ media_id=%s %s", media_id, link)
    notify(settings, f"✅ پست منتشر شد: {data.get('title')}\n{link}")
    return {**result, "ig_media_id": media_id, "permalink": link}
