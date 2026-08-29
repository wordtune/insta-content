"""کلاینت Instagram Content Publishing API (رسمی متا).

مسیر انتشار همیشه دو مرحله است:
  ۱) ساخت «کانتینر» رسانه   -> POST /{ig-user-id}/media
  ۲) انتشار کانتینر          -> POST /{ig-user-id}/media_publish
برای کاروسل، اول برای هر اسلاید یک کانتینر فرزند ساخته می‌شود، بعد یک کانتینر
والد از نوع CAROUSEL، بعد انتشار.
"""
from __future__ import annotations

import time
from typing import Any

import requests


class InstagramError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, *, ig_user_id: str, access_token: str,
                 api_version: str = "v23.0", flavor: str = "instagram_login",
                 timeout: int = 60):
        self._ig_user_id = (ig_user_id or "").strip()
        self.token = access_token
        self.timeout = timeout
        self.flavor = flavor
        host = "graph.instagram.com" if flavor == "instagram_login" else "graph.facebook.com"
        self.base = f"https://{host}/{api_version}"

    @property
    def ig_user_id(self) -> str:
        """اگر شناسه را وارد نکرده باشید، خودش از روی توکن پیدایش می‌کند."""
        if not self._ig_user_id:
            if self.flavor != "instagram_login":
                raise InstagramError(
                    "در حالت facebook_login باید IG_USER_ID را دستی وارد کنید."
                )
            me = self._request("GET", "me", fields="user_id,username")
            self._ig_user_id = str(me.get("user_id") or me.get("id") or "")
            if not self._ig_user_id:
                raise InstagramError("شناسه‌ی اکانت از روی توکن پیدا نشد.")
        return self._ig_user_id

    def whoami(self) -> dict[str, Any]:
        """نام کاربری و شناسه‌ی اکانتی که توکن به آن تعلق دارد."""
        return self._request("GET", "me", fields="user_id,username")

    # ---------- لایه پایه ----------

    def _request(self, method: str, path: str, **params) -> dict[str, Any]:
        url = f"{self.base}/{path.lstrip('/')}"
        params["access_token"] = self.token
        for attempt in range(3):
            try:
                resp = requests.request(method, url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise InstagramError(
                    f"اتصال به سرور اینستاگرام برقرار نشد: {type(e).__name__}. "
                    "اینترنت یا فیلترشکن را بررسی کنید."
                ) from e
            try:
                data = resp.json()
            except ValueError:
                data = {"error": {"message": resp.text[:500]}}

            if resp.ok and "error" not in data:
                return data

            err = data.get("error", {})
            code = err.get("code")
            # ۴ = محدودیت نرخ برنامه، ۱۷ = محدودیت کاربر، ۳۲ = محدودیت صفحه
            if code in (4, 17, 32, 613) and attempt < 2:
                time.sleep(30 * (attempt + 1))
                continue
            raise InstagramError(
                f"{err.get('type', 'APIError')} ({code}): {err.get('message')} "
                f"| subcode={err.get('error_subcode')}"
            )
        raise InstagramError("درخواست پس از ۳ تلاش ناموفق ماند.")

    # ---------- سقف انتشار ----------

    def publishing_quota(self) -> dict[str, Any]:
        """تعداد پست‌های منتشرشده در ۲۴ ساعت گذشته و سقف مجاز."""
        data = self._request(
            "GET", f"{self.ig_user_id}/content_publishing_limit",
            fields="config,quota_usage",
        )
        item = (data.get("data") or [{}])[0]
        return {
            "used": item.get("quota_usage", 0),
            "total": (item.get("config") or {}).get("quota_total", 100),
        }

    # ---------- کانتینرها ----------

    def create_image_container(self, image_url: str, *, caption: str | None = None,
                               alt_text: str | None = None,
                               is_carousel_item: bool = False) -> str:
        params: dict[str, Any] = {"image_url": image_url}
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        else:
            if caption:
                params["caption"] = caption
            if alt_text:
                params["alt_text"] = alt_text[:100]
        return self._request("POST", f"{self.ig_user_id}/media", **params)["id"]

    def create_carousel_container(self, children: list[str], *, caption: str) -> str:
        if not 2 <= len(children) <= 10:
            raise InstagramError("کاروسل باید بین ۲ تا ۱۰ اسلاید داشته باشد.")
        return self._request(
            "POST", f"{self.ig_user_id}/media",
            media_type="CAROUSEL", children=",".join(children), caption=caption,
        )["id"]

    def create_reel_container(self, video_url: str, *, caption: str,
                              cover_url: str | None = None,
                              share_to_feed: bool = True) -> str:
        params: dict[str, Any] = {
            "media_type": "REELS", "video_url": video_url,
            "caption": caption, "share_to_feed": str(share_to_feed).lower(),
        }
        if cover_url:
            params["cover_url"] = cover_url
        return self._request("POST", f"{self.ig_user_id}/media", **params)["id"]

    # ---------- انتظار و انتشار ----------

    def wait_ready(self, container_id: str, *, timeout: int = 300,
                   interval: int = 5) -> None:
        """کانتینر ویدیو/کاروسل باید قبل از انتشار به وضعیت FINISHED برسد."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._request("GET", container_id, fields="status_code,status")
            code = data.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"کانتینر {container_id} ناموفق: {data.get('status')}")
            time.sleep(interval)
        raise InstagramError(f"کانتینر {container_id} در زمان مقرر آماده نشد.")

    def publish(self, creation_id: str) -> str:
        return self._request(
            "POST", f"{self.ig_user_id}/media_publish", creation_id=creation_id
        )["id"]

    def permalink(self, media_id: str) -> str:
        try:
            return self._request("GET", media_id, fields="permalink").get("permalink", "")
        except InstagramError:
            return ""

    # ---------- مسیرهای کامل ----------

    def publish_single_image(self, image_url: str, caption: str,
                             alt_text: str | None = None) -> str:
        cid = self.create_image_container(image_url, caption=caption, alt_text=alt_text)
        self.wait_ready(cid, timeout=120)
        return self.publish(cid)

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        children = [self.create_image_container(u, is_carousel_item=True) for u in image_urls]
        for c in children:
            self.wait_ready(c, timeout=120)
        parent = self.create_carousel_container(children, caption=caption)
        self.wait_ready(parent, timeout=180)
        return self.publish(parent)

    def publish_reel(self, video_url: str, caption: str,
                     cover_url: str | None = None) -> str:
        cid = self.create_reel_container(video_url, caption=caption, cover_url=cover_url)
        self.wait_ready(cid, timeout=600, interval=10)
        return self.publish(cid)


# ---------- مدیریت توکن ----------

def refresh_long_lived_token(access_token: str) -> dict[str, Any]:
    """تمدید توکن بلندمدت (۶۰ روزه) — باید حداقل هر ۵۰ روز یک‌بار اجرا شود.
    توکن باید حداقل ۲۴ ساعت عمر داشته باشد تا قابل تمدید باشد."""
    resp = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": access_token},
        timeout=30,
    )
    data = resp.json()
    if "error" in data:
        raise InstagramError(f"تمدید توکن ناموفق: {data['error'].get('message')}")
    return data
