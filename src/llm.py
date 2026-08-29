"""لایه‌ی مدل زبانی — مستقل از یک سرویس خاص.

پروژه به هیچ ارائه‌دهنده‌ای گره نخورده است. دو مسیر پشتیبانی می‌شود:

  provider: "anthropic"  → API رسمی Anthropic
  provider: "openai"     → هر سرویسی که با API استاندارد OpenAI سازگار باشد
                           (اکثر ارائه‌دهنده‌ها همین استاندارد را پیاده کرده‌اند —
                            فقط base_url و نام مدل را بگذارید)

کلید همیشه از متغیر محیطی خوانده می‌شود، هیچ‌وقت از فایل تنظیمات.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger("autopost")


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------- انتخاب کلید

def api_key(settings) -> str:
    """کلید را از متغیر محیطی مناسبِ ارائه‌دهنده می‌خواند.

    LLM_API_KEY همیشه کار می‌کند و بر بقیه اولویت دارد — این‌طور با هر
    سرویسی می‌شود کار کرد بدون اینکه اسم متغیر گیج‌کننده باشد.
    """
    generic = os.getenv("LLM_API_KEY", "").strip()
    if generic:
        return generic
    if provider(settings) == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "").strip()
    return os.getenv("OPENAI_API_KEY", "").strip()


def provider(settings) -> str:
    return str((settings.get("llm", {}) or {}).get("provider", "anthropic")).strip().lower()


# ---------------------------------------------------------------- فراخوانی

def complete(settings, *, system: str, user: str) -> str:
    """یک درخواست به مدل می‌فرستد و متن پاسخ را برمی‌گرداند."""
    cfg = settings.get("llm", {}) or {}
    key = api_key(settings)
    if not key:
        raise LLMError(
            "کلید مدل زبانی تنظیم نشده است. متغیر LLM_API_KEY را بگذارید "
            "(یا ANTHROPIC_API_KEY / OPENAI_API_KEY بسته به سرویستان)."
        )

    kind = provider(settings)
    model = cfg.get("model") or ""
    max_tokens = int(cfg.get("max_tokens", 4000))
    temperature = float(cfg.get("temperature", 1.0))

    if not model:
        raise LLMError("نام مدل در config.yaml (بخش llm.model) خالی است.")

    if kind == "anthropic":
        return _anthropic(key, model, system, user, max_tokens, temperature)
    if kind in ("openai", "openai_compatible", "compatible"):
        base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        return _openai_compatible(key, base, model, system, user, max_tokens, temperature)

    raise LLMError(f"llm.provider نامعتبر است: {kind} (باید anthropic یا openai باشد)")


# ---------------------------------------------------------------- Anthropic

def _anthropic(key: str, model: str, system: str, user: str,
               max_tokens: int, temperature: float) -> str:
    try:
        from anthropic import Anthropic
    except ImportError:
        raise LLMError("کتابخانه‌ی anthropic نصب نیست: pip install anthropic")

    client = Anthropic(api_key=key)
    try:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        raise LLMError(_friendly(e)) from e
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


# ------------------------------------------------------- سازگار با OpenAI

def _openai_compatible(key: str, base_url: str, model: str, system: str,
                       user: str, max_tokens: int, temperature: float) -> str:
    url = f"{base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = "خطای نامشخص"

    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=180)
        except requests.RequestException as e:
            last = f"اتصال به {base_url} برقرار نشد: {type(e).__name__}"
            time.sleep(5 * (attempt + 1))
            continue

        if r.status_code in (401, 403):
            raise LLMError(f"کلید پذیرفته نشد ({r.status_code}). کلید و آدرس سرویس را بررسی کنید.")
        if r.status_code == 404:
            raise LLMError(
                f"آدرس {url} پیدا نشد. معمولاً base_url باید به /v1 ختم شود "
                "و نام مدل باید دقیقاً همان چیزی باشد که سرویس اعلام کرده."
            )
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            last = "محدودیت نرخ درخواست"
            continue
        if r.status_code >= 500:
            last = f"خطای سرور سرویس ({r.status_code})"
            time.sleep(6 * (attempt + 1))
            continue

        try:
            body = r.json()
        except ValueError:
            raise LLMError(f"پاسخ سرویس JSON معتبر نبود ({r.status_code}): {r.text[:200]}")

        if "error" in body:
            raise LLMError(f"سرویس خطا داد: {(body['error'] or {}).get('message', body['error'])}")

        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"ساختار پاسخ سرویس ناشناخته بود: {str(body)[:300]}")

    raise LLMError(f"درخواست به مدل پس از ۳ تلاش ناموفق ماند — {last}")


def _friendly(e: Exception) -> str:
    text = str(e)
    if "authentication" in text.lower() or "401" in text:
        return "کلید مدل زبانی پذیرفته نشد. کلید را بررسی کنید."
    if "not_found" in text.lower() or "404" in text:
        return "نام مدل شناخته نشد. مقدار llm.model را بررسی کنید."
    if "credit" in text.lower() or "quota" in text.lower() or "billing" in text.lower():
        return "اعتبار حساب مدل زبانی تمام شده است."
    return f"فراخوانی مدل ناموفق بود: {text[:300]}"


# ---------------------------------------------------------------- بررسی سلامت

def healthcheck(settings) -> str:
    """یک درخواست خیلی کوچک می‌فرستد تا مطمئن شویم اتصال کار می‌کند."""
    out = complete(settings, system="پاسخ را دقیقاً «سلام» بده و هیچ چیز دیگری ننویس.",
                   user="سلام")
    return (out or "").strip()[:40]
