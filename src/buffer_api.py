"""کلاینت API بافر (GraphQL).

چرا بافر:
پلتفرم توسعه‌دهندگان متا از ایران در دسترس نیست. بافر اپ متای تأییدشده‌ی خودش را
دارد؛ شما فقط اکانت اینستاگرامتان را با یک لاگین معمولی به بافر وصل می‌کنید و بعد
ربات به API بافر پست می‌فرستد. بافر سر ساعت روی پیج منتشر می‌کند.

نکته‌ی مهم: بافر تصویر را از یک «آدرس عمومی» برمی‌دارد، دقیقاً مثل خود اینستاگرام.
پس تصاویر باید قبلش جایی آپلود شوند (ماژول storage همین کار را می‌کند).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("autopost")


class BufferError(RuntimeError):
    pass


# --------------------------------------------------------------- پرس‌وجوها

Q_ORGANIZATIONS = """
query GetOrganizations {
  account { organizations { id name } }
}
"""

Q_CHANNELS = """
query GetChannels($input: ChannelsInput!) {
  channels(input: $input) { id name service timezone }
}
"""

M_CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id text dueAt } }
    ... on MutationError { message }
  }
}
"""


class BufferClient:
    ENDPOINT = "https://api.buffer.com"

    def __init__(self, token: str, *, timeout: int = 60):
        from .llm import clean_key   # همان تمیزکاری کلید، برای همین دلیل
        token = clean_key(token or "")
        if not token:
            raise BufferError("BUFFER_ACCESS_TOKEN تنظیم نشده است.")
        self.token = token
        self.timeout = timeout
        self._org_id: str | None = None

    # ---------- لایه‌ی پایه ----------

    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        last: str = "خطای نامشخص"

        for attempt in range(3):
            try:
                r = requests.post(self.ENDPOINT, json=payload, headers=headers,
                                  timeout=self.timeout)
            except requests.RequestException as e:
                last = f"اتصال برقرار نشد: {type(e).__name__}"
                time.sleep(4 * (attempt + 1))
                continue

            if r.status_code in (401, 403):
                raise BufferError(
                    "کلید API بافر پذیرفته نشد. مطمئن شوید کلید درست و فعال است "
                    "(publish.buffer.com/settings/api)."
                )
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                last = "محدودیت نرخ درخواست"
                continue

            try:
                body = r.json()
            except ValueError:
                last = f"پاسخ نامعتبر ({r.status_code}): {r.text[:200]}"
                time.sleep(3)
                continue

            if body.get("errors"):
                msgs = "؛ ".join(e.get("message", "") for e in body["errors"])
                raise BufferError(f"بافر خطا داد: {msgs}")
            if "data" in body:
                return body["data"]

            last = f"پاسخ بدون data: {str(body)[:200]}"
            time.sleep(3)

        raise BufferError(f"درخواست به بافر پس از ۳ تلاش ناموفق ماند — {last}")

    # ---------- شناسه‌ها ----------

    def organizations(self) -> list[dict[str, Any]]:
        data = self._gql(Q_ORGANIZATIONS)
        return ((data.get("account") or {}).get("organizations")) or []

    def organization_id(self, preferred: str = "") -> str:
        if preferred:
            return preferred
        if self._org_id:
            return self._org_id
        orgs = self.organizations()
        if not orgs:
            raise BufferError("هیچ سازمانی در حساب بافر شما پیدا نشد.")
        self._org_id = orgs[0]["id"]
        return self._org_id

    def channels(self, organization_id: str = "") -> list[dict[str, Any]]:
        oid = self.organization_id(organization_id)
        data = self._gql(Q_CHANNELS, {"input": {"organizationId": oid}})
        return data.get("channels") or []

    def instagram_channel(self, *, organization_id: str = "",
                          name: str = "") -> dict[str, Any]:
        """کانال اینستاگرام را پیدا می‌کند. اگر name بدهید، همان را برمی‌گرداند."""
        chans = self.channels(organization_id)
        if not chans:
            raise BufferError(
                "هیچ کانالی در بافر وصل نیست. اول اکانت اینستاگرام را به بافر وصل کنید."
            )

        if name:
            for c in chans:
                if (c.get("name") or "").lower() == name.lower():
                    return c
            available = "، ".join(c.get("name", "?") for c in chans)
            raise BufferError(f"کانالی به نام «{name}» پیدا نشد. کانال‌های موجود: {available}")

        insta = [c for c in chans if (c.get("service") or "").lower() == "instagram"]
        if not insta:
            available = "، ".join(f"{c.get('name')} ({c.get('service')})" for c in chans)
            raise BufferError(
                f"کانال اینستاگرامی در بافر پیدا نشد. کانال‌های موجود: {available}"
            )
        if len(insta) > 1:
            names = "، ".join(c.get("name", "?") for c in insta)
            log.warning("چند کانال اینستاگرام پیدا شد (%s) — اولی انتخاب شد. "
                        "برای انتخاب دقیق، buffer.channel_name را در config.yaml بگذارید.", names)
        return insta[0]

    # ---------- ساخت پست ----------

    def create_post(self, *, channel_id: str, text: str,
                    image_urls: list[str] | None = None,
                    due_at: datetime | None = None,
                    draft: bool = False) -> dict[str, Any]:
        """یک پست در صف بافر می‌گذارد.

        due_at داده شود → دقیقاً همان لحظه منتشر می‌شود (customScheduled).
        due_at ندهید      → به انتهای صف بافر اضافه می‌شود (addToQueue).
        """
        post_input: dict[str, Any] = {
            "channelId": channel_id,
            "text": text,
            "schedulingType": "automatic",   # خودکار، نه یادآوری
        }

        if due_at is not None:
            post_input["mode"] = "customScheduled"
            post_input["dueAt"] = _iso(due_at)
        else:
            post_input["mode"] = "addToQueue"

        if image_urls:
            post_input["assets"] = [{"image": {"url": u}} for u in image_urls]
        if draft:
            post_input["saveToDraft"] = True

        result = (self._gql(M_CREATE_POST, {"input": post_input}) or {}).get("createPost") or {}

        if result.get("__typename") == "PostActionSuccess":
            return result.get("post") or {}
        raise BufferError(result.get("message") or f"ساخت پست ناموفق بود: {result}")

    # ---------- بررسی سلامت ----------

    def whoami(self) -> dict[str, Any]:
        orgs = self.organizations()
        chans = self.channels() if orgs else []
        return {"organizations": orgs, "channels": chans}


def _iso(dt: datetime) -> str:
    """تبدیل به ISO 8601 با Z — بافر همین قالب را می‌خواهد."""
    if dt.tzinfo is None:
        raise BufferError("زمان انتشار باید منطقه‌ی زمانی داشته باشد.")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
