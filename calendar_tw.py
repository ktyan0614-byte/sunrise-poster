"""台灣行政機關辦公日曆表：判斷某天要不要發文。

「週休二日 + 見紅就休」聽起來像「週六日 + 國定假日」，但台灣還有
補假（國定假日遇假日往後補）和補班日（週六要上班），
所以不能自己算，要看官方的辦公日曆表。

資料來自 ruyut/TaiwanCalendar，它是政府開放資料「行政機關辦公日曆表」的
JSON 版本。抓不到的時候退回「週六日休息」的簡化規則，
免得查不到行事曆就整個停擺。
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request

DATA_URL = "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"
TIMEOUT = 15

# 同一次執行裡可能查好幾天（例如 --show），抓過的年份就別重抓。
_cache: dict[int, dict[str, dict]] = {}


class CalendarUnavailable(RuntimeError):
    pass


def _load_year(year: int) -> dict[str, dict]:
    if year in _cache:
        return _cache[year]

    url = DATA_URL.format(year=year)
    req = urllib.request.Request(url, headers={"User-Agent": "sunrise-poster/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            days = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise CalendarUnavailable(f"抓不到 {year} 年行事曆：{exc}") from None

    table = {d["date"]: d for d in days if "date" in d}
    if not table:
        raise CalendarUnavailable(f"{year} 年行事曆是空的")
    _cache[year] = table
    return table


def _is_generic_reason(reason: str) -> bool:
    """這個原因是不是「單純週末/沒有名字的放假」，不算一個值得倒數的假日。

    退化模式下週末的原因會變成「週末（抓不到...改用簡化規則）」這種
    帶說明的字串，所以用 startswith 而不是完全比對。
    """
    return reason.startswith("週末") or reason == "放假"


def next_long_weekend(
    after: dt.date, horizon_days: int = 180, min_length: int = 2
) -> tuple[dt.date, str, int] | None:
    """從 after 隔天起找下一個連假，回傳（連假第一天, 連假名稱, 連休天數）。

    「連假」定義成：包含至少一個實際假日、總長度至少 min_length 天的
    連續休息日區塊。回傳的日期是整段連續休息日的第一天，不是那個假日
    本身的日期，天數也是整段的——台灣人講「中秋連假」，指的是連著的
    週休二日也算進去的那幾天，不是只算國定假日本身那一天
    （教師節是週一，但連假實際上從週六就開始，是 3 天不是 1 天）。

    名稱固定是「OO連假」。同一段連續假期裡如果不只一個具名假日
    （例如中秋節接教師節連假），優先挑不是「補假」的那個當代表，
    避免顯示成「補假連假」這種空洞的名字。單獨一天、沒接到週末的假日
    不算連假，會被跳過繼續往後找。
    """
    day = after + dt.timedelta(days=1)
    end = after + dt.timedelta(days=horizon_days)
    while day <= end:
        reason = rest_reason(day)
        if reason and not _is_generic_reason(reason):
            start = day
            while rest_reason(start - dt.timedelta(days=1)):
                start -= dt.timedelta(days=1)
            stop = day
            while rest_reason(stop + dt.timedelta(days=1)):
                stop += dt.timedelta(days=1)
            length = (stop - start).days + 1
            if length < min_length:
                day = stop + dt.timedelta(days=1)
                continue

            names: list[str] = []
            cursor = start
            while cursor <= stop:
                r = rest_reason(cursor)
                if r and not _is_generic_reason(r) and r not in names:
                    names.append(r)
                cursor += dt.timedelta(days=1)
            core_name = next((n for n in names if n != MAKEUP_HOLIDAY_REASON), names[0])

            return start, f"{core_name}連假", length
        day += dt.timedelta(days=1)
    return None


MAKEUP_HOLIDAY_REASON = "補假"


def is_makeup_holiday(date: dt.date) -> bool:
    """今天是不是「補假」——國定假日遇週末往後補的那天。

    跟其他假日不同：這天有點像行政上湊出來的，不是真正的節日，
    適合拿來做例外處理（例如照常發文但內容不一樣）。
    """
    return rest_reason(date) == MAKEUP_HOLIDAY_REASON


def rest_reason(date: dt.date) -> str | None:
    """今天要休息的話回傳原因，要上班則回 None。

    查不到行事曆時退回週六日規則，並在原因裡註明是退化模式。
    """
    try:
        entry = _load_year(date.year).get(date.strftime("%Y%m%d"))
    except CalendarUnavailable as exc:
        if date.weekday() >= 5:  # 5=六 6=日
            return f"週末（{exc}，改用簡化規則）"
        return None

    if entry is None:
        # 行事曆有這一年但沒這一天，資料不完整，一樣退回週末規則。
        return "週末（行事曆缺這天，改用簡化規則）" if date.weekday() >= 5 else None

    if not entry.get("isHoliday"):
        return None

    description = (entry.get("description") or "").strip()
    if description:
        return description
    return "週末" if date.weekday() >= 5 else "放假"
