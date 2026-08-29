"""تحویل پست‌های آماده به تلگرام.

چون بدون دسترسی به API متا نمی‌شود مستقیم منتشر کرد، خروجی کار به تلگرام
می‌رود: تصویرها به‌صورت گروهی، و کپشن به‌صورت یک پیام متنی جدا — چون روی گوشی
با نگه‌داشتن انگشت روی پیام متنی، «کپی» راحت‌تر از کپی از زیرنویس عکس است.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("autopost")

API = "https://api.telegram.org"
CAPTION_LIMIT = 1024      # سقف زیرنویس عکس در تلگرام
MESSAGE_LIMIT = 4096      # سقف پیام متنی


class Telegram:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 60):
        self.token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    # ---------- پایه ----------

    def _post(self, method: str, *, data: dict[str, Any] | None = None,
              files: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"chat_id": self.chat_id, **(data or {})}
        last: Exception | str = "خطای نامشخص"
        for attempt in range(3):
            try:
                r = requests.post(f"{API}/bot{self.token}/{method}",
                                  data=payload, files=files, timeout=self.timeout)
                body = r.json()
                if body.get("ok"):
                    return body["result"]
                # 429 = محدودیت نرخ؛ تلگرام می‌گوید چقدر صبر کنیم
                if r.status_code == 429:
                    wait = int((body.get("parameters") or {}).get("retry_after", 3))
                    time.sleep(wait + 1)
                    continue
                last = body.get("description", str(body))
            except requests.RequestException as e:
                last = e
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"تلگرام ({method}) ناموفق: {last}")

    # ---------- پیام‌ها ----------

    def text(self, message: str, *, preview: bool = False) -> None:
        for chunk in _split(message, MESSAGE_LIMIT):
            self._post("sendMessage", data={
                "text": chunk,
                "disable_web_page_preview": str(not preview).lower(),
            })

    def photo(self, path: Path, caption: str = "") -> None:
        with open(path, "rb") as f:
            self._post("sendPhoto",
                       data={"caption": caption[:CAPTION_LIMIT]} if caption else {},
                       files={"photo": f})

    def photo_group(self, paths: list[Path], caption: str = "") -> None:
        """تا ۱۰ عکس در یک آلبوم. زیرنویس فقط روی عکس اول می‌نشیند."""
        if len(paths) == 1:
            return self.photo(paths[0], caption)

        handles, files, media = [], {}, []
        try:
            for i, p in enumerate(paths[:10]):
                key = f"file{i}"
                fh = open(p, "rb")
                handles.append(fh)
                files[key] = fh
                item: dict[str, Any] = {"type": "photo", "media": f"attach://{key}"}
                if i == 0 and caption:
                    item["caption"] = caption[:CAPTION_LIMIT]
                media.append(item)
            self._post("sendMediaGroup",
                       data={"media": json.dumps(media, ensure_ascii=False)},
                       files=files)
        finally:
            for fh in handles:
                fh.close()

    def document(self, path: Path, caption: str = "") -> None:
        with open(path, "rb") as f:
            self._post("sendDocument",
                       data={"caption": caption[:CAPTION_LIMIT]} if caption else {},
                       files={"document": f})

    # ---------- تحویل یک پست کامل ----------

    def deliver_post(self, *, index: int, total: int, label: str,
                     images: list[Path], caption: str,
                     scheduled_for: str | None = None) -> None:
        head = f"📌 پست {index} از {total} — {label}"
        if scheduled_for:
            head += f"\n🗓 پیشنهاد زمان انتشار: {scheduled_for}"
        head += f"\n🖼 {len(images)} تصویر" + (" (کاروسل)" if len(images) > 1 else "")

        self.photo_group(images, head)
        # کپشن جدا می‌آید تا با یک لمس طولانی کپی شود
        self.text(caption)


def _split(text: str, limit: int) -> list[str]:
    """متن بلند را روی مرز خط می‌شکند تا از سقف تلگرام رد نشود."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            if cur:
                parts.append(cur)
            cur = line[:limit]
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


def from_settings(settings) -> Telegram:
    return Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
