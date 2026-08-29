"""آپلود تصاویر روی یک آدرس عمومی.

اینستاگرام فایل را از شما نمی‌گیرد؛ یک URL عمومی می‌خواهد و خودش آن را دانلود می‌کند.
پس هر تصویر باید قبل از انتشار جایی آپلود شود که اینترنت به آن دسترسی داشته باشد.

سه گزینه:
  github  → تصاویر در یک شاخه‌ی جدا از همین مخزن ذخیره می‌شوند و از raw.githubusercontent
            سرو می‌شوند. هیچ سرویس دیگری لازم نیست. مخزن باید عمومی (public) باشد.
  s3      → هر سرویس سازگار با S3: Cloudflare R2، آروان‌کلود، لیارا، MinIO، AWS S3.
  local   → فقط برای تست؛ چیزی آپلود نمی‌شود.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


class LocalStorage:
    """فقط برای تست/dry-run — فایل روی دیسک می‌ماند و URL واقعی تولید نمی‌شود."""

    supports_state = False

    def upload(self, path: Path) -> str:
        return f"file://{path.resolve()}"


class S3Storage:
    supports_state = False

    def __init__(self, cfg: dict[str, Any], access_key: str, secret_key: str):
        import boto3
        from botocore.config import Config

        self.bucket = cfg["bucket"]
        self.public_base = cfg["public_base_url"].rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=cfg.get("endpoint_url") or None,
            region_name=cfg.get("region") or "auto",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def upload(self, path: Path) -> str:
        key = f"posts/{datetime.now():%Y/%m/%d}/{path.name}"
        self.client.upload_file(
            str(path), self.bucket, key,
            ExtraArgs={"ContentType": "image/jpeg",
                       "CacheControl": "public, max-age=31536000"},
        )
        return f"{self.public_base}/{key}"


class GitHubStorage:
    """میزبانی تصاویر روی خود مخزن گیت‌هاب.

    فایل‌ها در یک شاخه‌ی جداگانه (پیش‌فرض: media) ذخیره می‌شوند تا تاریخچه‌ی شاخه‌ی
    اصلی شلوغ نشود. آدرس نهایی از نوع raw.githubusercontent.com است که تصاویر را با
    Content-Type درست سرو می‌کند.

    شرط: مخزن باید عمومی باشد، وگرنه اینستاگرام نمی‌تواند تصویر را بردارد.
    """

    supports_state = True
    API = "https://api.github.com"

    def __init__(self, cfg: dict[str, Any], token: str):
        gh = cfg.get("github") or {}
        self.repo = (gh.get("repo") or os.getenv("GITHUB_REPOSITORY") or "").strip("/")
        self.branch = gh.get("branch") or "media"
        self.token = token
        if not self.repo:
            raise RuntimeError(
                "نام مخزن مشخص نیست. در config.yaml مقدار storage.github.repo را "
                "به شکل «نام‌کاربری/نام‌مخزن» بگذارید."
            )
        if not self.token:
            raise RuntimeError("توکن گیت‌هاب پیدا نشد (GH_TOKEN یا GITHUB_TOKEN).")
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._branch_ready = False

    # ---------- کمکی ----------

    def _get(self, path: str, **params):
        return requests.get(f"{self.API}{path}", headers=self._headers,
                            params=params, timeout=45)

    def _ensure_branch(self) -> None:
        if self._branch_ready:
            return
        r = self._get(f"/repos/{self.repo}/git/ref/heads/{self.branch}")
        if r.status_code == 200:
            self._branch_ready = True
            return

        info = self._get(f"/repos/{self.repo}")
        if info.status_code != 200:
            raise RuntimeError(
                f"دسترسی به مخزن {self.repo} ممکن نشد ({info.status_code}). "
                "نام مخزن و دسترسی توکن را بررسی کنید."
            )
        default = info.json().get("default_branch", "main")
        head = self._get(f"/repos/{self.repo}/git/ref/heads/{default}")
        head.raise_for_status()
        sha = head.json()["object"]["sha"]

        made = requests.post(
            f"{self.API}/repos/{self.repo}/git/refs", headers=self._headers,
            json={"ref": f"refs/heads/{self.branch}", "sha": sha}, timeout=45,
        )
        if made.status_code not in (201, 422):  # 422 = از قبل ساخته شده
            raise RuntimeError(f"ساخت شاخه‌ی {self.branch} ناموفق بود: {made.text[:300]}")
        self._branch_ready = True

    def _existing_sha(self, repo_path: str) -> str | None:
        r = self._get(f"/repos/{self.repo}/contents/{repo_path}", ref=self.branch)
        return r.json().get("sha") if r.status_code == 200 else None

    def put_file(self, local: Path, repo_path: str, message: str) -> str:
        self._ensure_branch()
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(local.read_bytes()).decode(),
            "branch": self.branch,
        }
        sha = self._existing_sha(repo_path)
        if sha:
            payload["sha"] = sha

        r = requests.put(
            f"{self.API}/repos/{self.repo}/contents/{repo_path}",
            headers=self._headers, json=payload, timeout=120,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"آپلود {repo_path} ناموفق بود ({r.status_code}): {r.text[:300]}")
        return f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{repo_path}"

    def fetch_file(self, repo_path: str, local: Path) -> bool:
        """اگر فایل روی شاخه باشد دانلودش می‌کند. True یعنی دانلود شد."""
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{repo_path}"
        try:
            r = requests.get(url, timeout=45)
            if r.status_code == 200 and r.content:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(r.content)
                return True
        except requests.RequestException:
            pass
        return False

    # ---------- رابط مشترک ----------

    def upload(self, path: Path) -> str:
        repo_path = f"posts/{datetime.now():%Y/%m}/{path.name}"
        return self.put_file(path, repo_path, f"تصویر پست: {path.name}")

    # ---------- همگام‌سازی حافظه ----------

    STATE_PATH = "state/state.db"

    def load_state(self, local: Path) -> bool:
        return self.fetch_file(self.STATE_PATH, local)

    def save_state(self, local: Path) -> None:
        if local.exists():
            self.put_file(local, self.STATE_PATH,
                          f"به‌روزرسانی حافظه — {datetime.now():%Y-%m-%d}")


def get_storage(settings):
    cfg = settings["storage"]
    provider = cfg.get("provider")

    if settings.dry_run:
        return LocalStorage()

    if provider == "github":
        token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
        return GitHubStorage(cfg, token)

    if provider == "s3":
        return S3Storage(cfg, settings.s3_access_key_id, settings.s3_secret_access_key)

    return LocalStorage()
