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


def next_holiday(after: dt.date, horizon_days: int = 120) -> tuple[dt.date, str] | None:
    """從 after 隔天起找下一個「非週末」的休息日，回傳 (日期, 名稱)。

    只算國定假日/連假/補假，不算單純週末——因為「還有 N 天放假」
    對比的基準本來就是平常的上班日，跟每週都有的週末混在一起沒意義。
    """
    for offset in range(1, horizon_days + 1):
        day = after + dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        reason = rest_reason(day)
        if reason and reason not in ("週末", "放假"):
            return day, reason
    return None


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
