#!/usr/bin/env python3
"""把 Meta 後台產的 1 小時短效 token，換成 60 天的長效 token。

Threads 後台按「Generate token」給的是短效 token，只能活 1 小時。
直接拿它去設 GitHub Secret，隔天就會過期。要先跑這支換成長效的。

過期的短效 token 換不了，所以流程是：後台產一顆 → 馬上跑這支。
"""
from __future__ import annotations

import os
import sys

from post_sunrise import load_dotenv
import threads_api


def main() -> int:
    load_dotenv()
    short = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    secret = os.environ.get("THREADS_APP_SECRET", "").strip()

    if not short:
        print("錯誤：.env 裡沒有 THREADS_ACCESS_TOKEN（放剛產的短效 token）", file=sys.stderr)
        return 2
    if not secret:
        print(
            "錯誤：.env 裡沒有 THREADS_APP_SECRET\n"
            "到 Meta 應用程式後台 → App settings → Basic → App secret 複製過來。",
            file=sys.stderr,
        )
        return 2

    data = threads_api.exchange_token(short, secret)
    long_token = data.get("access_token")
    if not long_token:
        print(f"換取失敗：{data}", file=sys.stderr)
        return 1

    days = int(data.get("expires_in", 0)) // 86400
    print(f"換取成功，長效 token 有效 {days} 天。", file=sys.stderr)
    print("接著要做兩件事：", file=sys.stderr)
    print("  1. 把下面這串貼回 .env 的 THREADS_ACCESS_TOKEN", file=sys.stderr)
    print("  2. 執行 gh secret set THREADS_ACCESS_TOKEN 更新到 GitHub", file=sys.stderr)
    print(file=sys.stderr)

    # token 走 stdout，其餘訊息走 stderr，方便需要時直接接管線。
    print(long_token)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except threads_api.ThreadsError as exc:
        print(f"Threads API 錯誤：{exc}", file=sys.stderr)
        sys.exit(1)
