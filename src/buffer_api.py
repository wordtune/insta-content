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


class BufferQuotaError(BufferError):
    """صف بافر پر است — تلاش برای پست بعدی هم بی‌فایده است."""


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

Q_SCHEDULED = """
query GetScheduledPosts($input: PostsInput!) {
  posts(input: $input) {
    edges { node { id dueAt } }
  }
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

    # ---------- ظرفیت صف ----------

    def scheduled_count(self, organization_id: str = "") -> int | None:
        """چند پست همین حالا در صف بافر است.

        پلن رایگان بافر سقف مشخصی دارد (معمولاً ۱۰ پست). اگر از قبل بدانیم چند
        تا در صف است، به‌جای اینکه چند بار بی‌فایده تلاش کنیم و خطا بگیریم،
        از همان اول فقط به اندازه‌ی ظرفیت آزاد پست می‌سازیم.

        None یعنی نتوانستیم بشماریم — آن وقت محتاطانه جلو می‌رویم.
        """
        try:
            data = self._gql(Q_SCHEDULED, {"input": {
                "organizationId": self.organization_id(organization_id),
                "filter": {"status": ["scheduled"]},
            }})
        except BufferError as e:
            log.warning("شمارش صف بافر ممکن نشد: %s", e)
            return None
        edges = ((data.get("posts") or {}).get("edges")) or []
        return len([e for e in edges if e.get("node")])

    # ---------- ساخت پست ----------

    def create_post(self, *, channel_id: str, text: str,
                    image_urls: list[str] | None = None,
                    due_at: datetime | None = None,
                    draft: bool = False,
                    service: str = "instagram",
                    post_type: str = "post",
                    alt_text: str = "") -> dict[str, Any]:
        """یک پست در صف بافر می‌گذارد.

        due_at داده شود → دقیقاً همان لحظه منتشر می‌شود (customScheduled).
        due_at ندهید      → به انتهای صف بافر اضافه می‌شود (addToQueue).

        اینستاگرام حتماً نوع پست را می‌خواهد (post / story / reel) وگرنه
        بافر درخواست را رد می‌کند.
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
            img_meta = {"altText": alt_text[:100]} if alt_text else None
            post_input["assets"] = [
                {"image": {"url": u, **({"metadata": img_meta} if img_meta else {})}}
                for u in image_urls
            ]

        if (service or "").lower() == "instagram":
            post_input["metadata"] = {
                "instagram": {"type": post_type, "shouldShareToFeed": True}
            }

        if draft:
            post_input["saveToDraft"] = True

        result = (self._gql(M_CREATE_POST, {"input": post_input}) or {}).get("createPost") or {}

        if result.get("__typename") == "PostActionSuccess":
            return result.get("post") or {}

        msg = result.get("message") or f"ساخت پست ناموفق بود: {result}"
        low = msg.lower()
        if "limit reached" in low or "scheduled posts limit" in low:
            raise BufferQuotaError(msg)
        raise BufferError(msg)

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
