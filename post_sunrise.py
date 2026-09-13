#!/usr/bin/env python3
"""在台灣日出的那一刻，於 Threads 發一則貼文。

準時的關鍵不在「準時觸發」，而在「提早觸發、然後睡到準點」：
外部 cron 只要在日出前某個固定時間把這支叫起來，
剩下的由它自己算出今天日出幾點幾分幾秒，睡到那一刻再發。

這樣一來，觸發端晚個十幾二十分鐘完全不影響發文時間，
精準度只受網路延遲影響，大約是秒級。

貼文內容由設定決定（見 POST_CHARS），不寫死在程式裡。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import calendar_tw
import threads_api

# Windows 主控台預設是 cp950，遇到編不出來的字會讓整支腳本崩掉。
# 改成用 ? 取代，寧可字印壞也不要因為印訊息而發不出文。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

TZ = ZoneInfo("Asia/Taipei")
# 預設台北 101 附近；想換城市改這兩個數字或設環境變數即可。
DEFAULT_LAT = 25.0330
DEFAULT_LON = 121.5654


class ConfigError(RuntimeError):
    pass


def load_dotenv(path: Path = Path(".env")) -> None:
    """本機測試用的簡易 .env 讀取；GitHub Actions 上沒這個檔，直接略過。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


class TextMaker:
    """依設定隨機組出貼文內容，並判斷一篇貼文是不是本程式發的。

    字元集刻意放在設定裡而不是程式裡，這樣公開的原始碼看不出實際內容。
    `POST_BANNED` 可以把特定排列從平常的抽籤裡拿掉，留給連假前一天
    （見 `PRE_BREAK_TEXT`）這種特殊場合專用，讓那個組合平常不會出現。
    """

    def __init__(
        self, chars: str, length: int, suffix: str, banned: frozenset[str] = frozenset()
    ) -> None:
        if not chars:
            raise ConfigError(
                "缺少 POST_CHARS：貼文用的字元集。\n"
                "本機請寫進 .env，GitHub 請設成 repository secret。"
            )
        if length < 1:
            raise ConfigError("POST_LENGTH 至少要是 1")
        self.chars = chars
        self.length = length
        self.suffix = suffix
        # 封殺的是「不含字尾」的排列本身，跟 PRE_BREAK_TEXT 這種完整字串不同層級——
        # 平常抽籤永遠跳過它，讓它只在特殊日子被指定使用時才出現。
        self.banned = banned

    @classmethod
    def from_env(cls) -> "TextMaker":
        banned_raw = os.environ.get("POST_BANNED", "").strip()
        banned = frozenset(b.strip() for b in banned_raw.split(",") if b.strip())
        return cls(
            chars=os.environ.get("POST_CHARS", "").strip(),
            length=int(os.environ.get("POST_LENGTH", "3")),
            suffix=os.environ.get("POST_SUFFIX", ""),
            banned=banned,
        )

    def make(self) -> str:
        """隨機組一則，跳過 POST_BANNED 裡封殺的排列。

        可重複排列，所以整串同一個字本來就是合法的——封殺是額外加上去的
        規則，不是排列本身不合法。組合空間夠大時幾次就會抽到，
        真的抽不出來（例如把所有排列都封殺了）才會放棄。
        """
        for _ in range(1000):
            body = "".join(random.choices(self.chars, k=self.length))
            if body not in self.banned:
                return body + self.suffix
        raise ConfigError("POST_BANNED 封殺了所有排列，抽不出東西——檢查一下是不是設太多了")

    def matches(self, text: str) -> bool:
        """這篇貼文是不是本程式發的。

        內文每天都不一樣，所以不能比對固定字串，改成比對「形狀」：
        後綴對、長度對、而且每個字都在字元集裡。
        """
        if self.suffix:
            if not text.endswith(self.suffix):
                return False
            text = text[: -len(self.suffix)]
        return len(text) == self.length and all(ch in self.chars for ch in text)


def sunrise_at(date: dt.date, lat: float, lon: float) -> dt.datetime:
    """算出指定日期的日出時刻。astral 在這裡才 import，--now 路徑就不需要它。"""
    from astral import LocationInfo
    from astral.sun import sun

    location = LocationInfo(latitude=lat, longitude=lon)
    return sun(location.observer, date=date, tzinfo=TZ)["sunrise"]


def already_posted_today(
    token: str, user_id: str, now: dt.datetime, maker: TextMaker | None, fixed: str | None
) -> bool:
    """今天是否已經發過。

    只認「今天發的、而且形狀對」的貼文——不能只看最後一篇，
    否則帳號今天發過任何其他內容，都會被誤判成已經發過而漏發。
    """
    for post in threads_api.recent_posts(token, user_id):
        stamp = post.get("timestamp")
        if not stamp:
            continue
        # Threads 回傳形如 2026-09-01T21:05:33+0000
        posted_at = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(TZ)
        if posted_at.date() < now.date():
            break
        if posted_at.date() != now.date():
            continue
        text = post.get("text") or ""
        hit = text == fixed if fixed is not None else maker.matches(text)
        if hit:
            return True
    return False


def post_clock_in(
    token: str, user_id: str, post_id: str, posted_at: dt.datetime, settle: int
) -> None:
    """在剛發的貼文底下補一則回覆。兩份樣板都沒設就不做。

    正常用 REPLY_TEMPLATE。如果今天是週一、離下個連假還很遠（超過
    STATUS_MIN_DAYS，預設 10 天），有 STATUS_CHANCE（預設 50%）的機率
    改用 REPLY_TEMPLATE_ALT——連假倒數還很遠時，每次都念一樣的倒數
    顯得敷衍，不如偶爾換成跟連假無關的內容。

    可用欄位：{time} 發文時刻、{holiday_name}/{holiday_days} 見連假
    倒數說明、{status} 只有 REPLY_TEMPLATE_ALT 用得到，從 STATUS_LINES
    （逗號分隔）隨機抽一句。查不到下一個連假時，含 holiday_* 的樣板
    會直接跳過；REPLY_TEMPLATE_ALT 沒設或條件沒中，一律退回用
    REPLY_TEMPLATE。

    刻意做成 best-effort：主貼文這時已經發出去了，
    回覆失敗不該讓整個 workflow 標紅，否則早上會誤以為當天沒發成功。
    """
    template = os.environ.get("REPLY_TEMPLATE", "").strip()
    alt_template = os.environ.get("REPLY_TEMPLATE_ALT", "").strip()
    status_lines = [s.strip() for s in os.environ.get("STATUS_LINES", "").split(",") if s.strip()]

    # 兩份樣板只要有一份可能用到連假資訊、或者有 ALT 樣板要判斷條件，就查一次。
    needs_holiday_info = bool(alt_template) or any(
        f"{{{k}}}" in template for k in ("holiday_name", "holiday_days")
    )
    holiday_info = calendar_tw.next_long_weekend(posted_at.date()) if needs_holiday_info else None

    use_alt = False
    if alt_template and status_lines and holiday_info:
        days_left = (holiday_info[0] - posted_at.date()).days
        min_days = int(os.environ.get("STATUS_MIN_DAYS", "10"))
        chance = float(os.environ.get("STATUS_CHANCE", "0.5"))
        if posted_at.date().weekday() == 0 and days_left > min_days and random.random() < chance:
            use_alt = True

    chosen = alt_template if use_alt else template
    if not chosen:
        return

    fields = {"time": f"{posted_at:%H:%M:%S}"}
    if use_alt:
        fields["status"] = random.choice(status_lines)
    if "{holiday_name}" in chosen or "{holiday_days}" in chosen:
        if not holiday_info:
            print("找不到下一個連假，跳過回覆。", file=sys.stderr)
            return
        block_start, holiday_name = holiday_info
        fields["holiday_name"] = holiday_name
        fields["holiday_days"] = (block_start - posted_at.date()).days

    try:
        text = chosen.format(**fields)
    except (KeyError, IndexError) as exc:
        print(f"REPLY_TEMPLATE 格式有誤，跳過回覆：{exc}", file=sys.stderr)
        return

    try:
        reply_id = threads_api.publish_text(
            token, user_id, text, settle_seconds=settle, reply_to_id=post_id
        )
    except threads_api.ThreadsError as exc:
        print(f"回覆失敗（主貼文已發出，不影響）：{exc}", file=sys.stderr)
        return

    print(f"回覆已發（post id {reply_id}）")


def sleep_until(target: dt.datetime, max_wait_minutes: int) -> bool:
    """睡到指定時刻。太久或已過期就回 False，讓呼叫端決定怎麼辦。"""
    remaining = (target - dt.datetime.now(TZ)).total_seconds()
    if remaining <= 0:
        return False
    if remaining > max_wait_minutes * 60:
        print(f"要等 {remaining / 60:.0f} 分鐘，超過 {max_wait_minutes} 分鐘上限，中止。")
        return False

    print(f"等到 {target:%H:%M:%S} 再發，還要 {remaining / 60:.1f} 分鐘。")
    # 分段睡並定期回報，免得 Actions 的 log 看起來像當掉了。
    while True:
        remaining = (target - dt.datetime.now(TZ)).total_seconds()
        if remaining <= 0:
            return True
        if remaining > 600:
            time.sleep(300)
            left = (target - dt.datetime.now(TZ)).total_seconds()
            print(f"  還有 {left / 60:.0f} 分鐘…", flush=True)
        else:
            time.sleep(min(remaining, 5))


def main() -> int:
    parser = argparse.ArgumentParser(description="日出時在 Threads 發文")
    parser.add_argument("--wait", action="store_true", help="睡到今天日出那一刻再發（workflow 用）")
    parser.add_argument("--now", action="store_true", help="立刻發，但仍檢查今天是否已發過")
    parser.add_argument("--force", action="store_true", help="立刻發，連休假與重複檢查都跳過（測試用）")
    parser.add_argument("--dry-run", action="store_true", help="只印出判斷結果，不真的發文")
    parser.add_argument("--show", action="store_true", help="印出未來 14 天的日出與排班就結束")
    parser.add_argument("--sample", action="store_true", help="隨機抽幾則看看就結束（只在本機用）")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="把「今天」假裝成這一天，用來預覽補假/連假前一天的實際內容。"
        "只能配合 --dry-run 或 --show，避免不小心真的用假日期發文。",
    )
    args = parser.parse_args()

    load_dotenv()
    lat = float(os.environ.get("LAT", DEFAULT_LAT))
    lon = float(os.environ.get("LON", DEFAULT_LON))
    now = dt.datetime.now(TZ)

    if args.as_of:
        if not (args.dry_run or args.show):
            print("錯誤：--as-of 只能配合 --dry-run 或 --show 使用。", file=sys.stderr)
            return 2
        try:
            fake_date = dt.date.fromisoformat(args.as_of)
        except ValueError:
            print(f"錯誤：--as-of 看不懂這個日期：{args.as_of}", file=sys.stderr)
            return 2
        now = dt.datetime.combine(fake_date, now.timetz())

    if args.sample:
        print("　".join(TextMaker.from_env().make() for _ in range(10)))
        return 0

    makeup_text = os.environ.get("MAKEUP_TEXT", "").strip()
    pre_break_text = os.environ.get("PRE_BREAK_TEXT", "").strip()

    if args.show:
        for offset in range(14):
            day = now.date() + dt.timedelta(days=offset)
            rest = calendar_tw.rest_reason(day)
            found = calendar_tw.next_long_weekend(day) if pre_break_text else None
            is_pre_break = bool(found) and found[0] == day + dt.timedelta(days=1)
            if rest and calendar_tw.is_makeup_holiday(day) and makeup_text:
                mark = "發文（補假，簡短版）"
            elif rest:
                mark = f"休息（{rest}）"
            elif is_pre_break:
                mark = "發文（連假前一天，特別版）"
            else:
                mark = "發文"
            weekday = "一二三四五六日"[day.weekday()]
            print(f"{day}（{weekday}）日出 {sunrise_at(day, lat, lon):%H:%M:%S}  {mark}")
        return 0

    # 補假是行政上湊出來的假，不算真正的節日——設了 MAKEUP_TEXT 的話
    # 這天照樣發文（通常是比平常短、更敷衍的內容），其他假日則完全沉默。
    is_makeup_day = False
    if not args.force:
        rest = calendar_tw.rest_reason(now.date())
        is_makeup_day = bool(rest) and calendar_tw.is_makeup_holiday(now.date()) and bool(makeup_text)
        if rest and not is_makeup_day:
            print(f"{now:%Y-%m-%d} 是休息日（{rest}），今天不發。")
            return 0

    # 連假前一天：今天還是要上班，但明天就是連假第一天了。
    # 設了 PRE_BREAK_TEXT 的話這天發那句固定內容，取代平常的隨機抽籤。
    is_pre_break_day = False
    if pre_break_text and not args.force and not is_makeup_day:
        found = calendar_tw.next_long_weekend(now.date())
        is_pre_break_day = bool(found) and found[0] == now.date() + dt.timedelta(days=1)

    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        print("錯誤：沒有 THREADS_ACCESS_TOKEN", file=sys.stderr)
        return 2

    # 補假特例 > 連假前一天特例 > 固定內文 > 隨機組合，決定今天要發什麼。
    fixed = os.environ.get("POST_TEXT", "").strip() or None
    if is_makeup_day:
        fixed = makeup_text
    elif is_pre_break_day:
        fixed = pre_break_text
    maker = TextMaker.from_env() if fixed is None else None
    text = fixed if fixed is not None else maker.make()

    settle = int(os.environ.get("SETTLE_SECONDS", "10"))
    grace_minutes = int(os.environ.get("GRACE_MINUTES", "60"))
    max_wait_minutes = int(os.environ.get("MAX_WAIT_MINUTES", "330"))

    # 帳號查詢與去重都先做掉，這樣真正到了日出那一秒只剩下發文的兩次呼叫。
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    if not user_id:
        user_id = threads_api.get_me(token)["id"]

    if not args.force and already_posted_today(token, user_id, now, maker, fixed):
        print("今天已經發過了，跳過。")
        return 0

    sunrise = None
    if args.wait:
        sunrise = sunrise_at(now.date(), lat, lon)
        print(f"現在 {now:%Y-%m-%d %H:%M:%S}｜今天日出 {sunrise:%H:%M:%S}")

        # 發文是兩段式的（建 container、等它就緒、才 publish），
        # 所以要提早這段時間起跑，貼文才會剛好落在日出那一刻。
        target = sunrise - dt.timedelta(seconds=settle + 2)

        if not sleep_until(target, max_wait_minutes):
            late_by = (dt.datetime.now(TZ) - sunrise).total_seconds() / 60
            if late_by < 0:
                return 1
            if late_by > grace_minutes:
                print(f"比日出晚了 {late_by:.0f} 分鐘（超過 {grace_minutes} 分鐘上限），今天跳過。")
                return 0
            print(f"已經比日出晚了 {late_by:.0f} 分鐘，補發。")

    if args.dry_run:
        # 只有本機會用到 --dry-run；CI 的 log 是公開的，所以正式路徑不印內文。
        print(f"[dry-run] 會發：{text}")
        return 0

    post_id = threads_api.publish_text(token, user_id, text, settle_seconds=settle)
    posted_at = dt.datetime.now(TZ)
    # 這行會進 Actions 的公開 log，所以只報長度不報內容。
    print(f"發文成功（{len(text)} 字，post id {post_id}）於 {posted_at:%H:%M:%S}")
    if sunrise is not None:
        print(f"與日出誤差 {(posted_at - sunrise).total_seconds():+.1f} 秒")

    post_clock_in(token, user_id, post_id, posted_at, settle)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        sys.exit(2)
    except threads_api.ThreadsError as exc:
        # 去重查詢或發文失敗時就讓這次執行失敗，寧可今天不發，也不要重複發。
        print(f"Threads API 錯誤：{exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中斷。", file=sys.stderr)
        sys.exit(130)
