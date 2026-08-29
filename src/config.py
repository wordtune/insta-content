"""بارگذاری تنظیمات از config.yaml و متغیرهای محیطی."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    # --- اسرار ---
    ig_user_id: str = ""
    ig_access_token: str = ""
    fb_page_id: str = ""
    anthropic_api_key: str = ""
    buffer_token: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def mode(self) -> str:
        """batch = تحویل به تلگرام · api = انتشار مستقیم از طریق متا"""
        return str(self.raw.get("mode", "batch")).strip().lower()

    @property
    def dry_run(self) -> bool:
        # متغیر محیطی بر فایل اولویت دارد تا در CI بتوان override کرد.
        # مقدار خالی یعنی «تعیین نشده» — تصمیم با config.yaml است.
        env = (os.getenv("DRY_RUN") or "").strip().lower()
        if env:
            return env in {"1", "true", "yes", "on"}
        return bool(self.raw.get("safety", {}).get("dry_run", True))


def load_settings(config_path: str | Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    path = Path(config_path) if config_path else ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return Settings(
        raw=raw,
        ig_user_id=os.getenv("IG_USER_ID", ""),
        ig_access_token=os.getenv("IG_ACCESS_TOKEN", ""),
        fb_page_id=os.getenv("FB_PAGE_ID", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        buffer_token=os.getenv("BUFFER_ACCESS_TOKEN", ""),
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )


def _check_storage(s: Settings, problems: list[str]) -> None:
    """میزبانی تصویر — هم متا و هم بافر تصویر را از یک URL عمومی برمی‌دارند."""
    st = s.get("storage", {})
    provider = st.get("provider")
    if provider == "s3":
        if not s.s3_access_key_id or not s.s3_secret_access_key:
            problems.append("کلیدهای S3 تنظیم نشده‌اند (تصویر باید روی URL عمومی آپلود شود).")
        if not st.get("public_base_url"):
            problems.append("storage.public_base_url در config.yaml خالی است.")
    elif provider == "github":
        if not (os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")):
            problems.append("توکن گیت‌هاب پیدا نشد (GH_TOKEN یا GITHUB_TOKEN).")
        repo = (st.get("github") or {}).get("repo") or os.getenv("GITHUB_REPOSITORY")
        if not repo:
            problems.append("storage.github.repo در config.yaml خالی است "
                            "(به شکل «نام‌کاربری/نام‌مخزن»).")
    elif provider != "local":
        problems.append(f"storage.provider نامعتبر است: {provider}")


def validate(s: Settings, *, need_publish: bool | None = None) -> list[str]:
    """فهرست مشکلات پیکربندی را برمی‌گرداند (خالی = همه‌چیز درست است)."""
    problems: list[str] = []

    from . import llm as _llm
    if not _llm.api_key(s):
        problems.append(
            "کلید مدل زبانی تنظیم نشده است (LLM_API_KEY یا "
            "ANTHROPIC_API_KEY / OPENAI_API_KEY)."
        )
    llm_cfg = s.get("llm", {}) or {}
    if not llm_cfg.get("model"):
        problems.append("llm.model در config.yaml خالی است.")
    if _llm.provider(s) != "anthropic" and not llm_cfg.get("base_url"):
        problems.append("llm.base_url در config.yaml خالی است "
                        "(آدرس سرویس مدل، معمولاً به /v1 ختم می‌شود).")

    mode = s.mode
    if need_publish is None:
        need_publish = mode == "api"

    if mode == "buffer":
        if not s.buffer_token:
            problems.append("BUFFER_ACCESS_TOKEN تنظیم نشده است.")
        _check_storage(s, problems)
        if (s.get("storage", {}) or {}).get("provider") == "local":
            problems.append("در حالت buffer، storage.provider نمی‌تواند local باشد — "
                            "بافر تصویر را از یک آدرس عمومی برمی‌دارد.")
    elif mode == "batch":
        if not (s.telegram_bot_token and s.telegram_chat_id):
            problems.append("تلگرام تنظیم نشده — در حالت batch راه تحویل پست‌ها همین است.")
    elif need_publish:
        # IG_USER_ID اختیاری است — اگر خالی باشد از روی توکن پیدا می‌شود
        if not s.ig_access_token:
            problems.append("IG_ACCESS_TOKEN تنظیم نشده است.")
        _check_storage(s, problems)

    return problems
