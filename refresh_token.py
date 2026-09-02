#!/usr/bin/env python3
"""把長效 token 續期 60 天，並印出新 token。

Threads 的長效 token 只有 60 天壽命，過期就整條自動化默默停掉，
所以固定排程跑這支，順便更新 GitHub Secret。
"""
from __future__ import annotations

import os
import sys

from post_sunrise import load_dotenv
import threads_api


def main() -> int:
    load_dotenv()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        print("錯誤：沒有 THREADS_ACCESS_TOKEN", file=sys.stderr)
        return 2

    data = threads_api.refresh_token(token)
    new_token = data.get("access_token")
    if not new_token:
        print(f"續期失敗：{data}", file=sys.stderr)
        return 1

    days = int(data.get("expires_in", 0)) // 86400
    print(f"續期成功，還有 {days} 天", file=sys.stderr)

    # 新 token 走 stdout，讓 workflow 直接接給 gh secret set；其餘訊息都走 stderr。
    print(new_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
