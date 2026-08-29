"""تبدیل تاریخ میلادی به شمسی — بدون وابستگی خارجی."""
from __future__ import annotations

from datetime import date

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

WEEKDAYS = {0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه",
            4: "جمعه", 5: "شنبه", 6: "یک‌شنبه"}

_G_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            - 80 + gd + _G_DAYS[gm - 1])

    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def fa_digits(text: str) -> str:
    return str(text).translate(_FA_DIGITS)


def format_date(d: date, *, with_weekday: bool = True) -> str:
    """مثال: «جمعه ۷ شهریور»"""
    _, jm, jd = to_jalali(d.year, d.month, d.day)
    out = f"{fa_digits(jd)} {MONTHS[jm - 1]}"
    if with_weekday:
        out = f"{WEEKDAYS[d.weekday()]} {out}"
    return out


def format_full(d: date) -> str:
    """مثال: «جمعه ۷ شهریور ۱۴۰۵»"""
    jy, jm, jd = to_jalali(d.year, d.month, d.day)
    return f"{WEEKDAYS[d.weekday()]} {fa_digits(jd)} {MONTHS[jm - 1]} {fa_digits(jy)}"
