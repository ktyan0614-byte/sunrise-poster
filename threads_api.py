"""Threads API 極簡封裝。

刻意只用 Python 標準函式庫（urllib），不依賴 requests——
這樣發文那條路徑在 GitHub Actions 上完全不用 pip install，
runner 開機後直接跑，少等十幾秒。對「準時發文」來說這十幾秒是有意義的。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.threads.net/v1.0"
EXCHANGE_URL = "https://graph.threads.net/access_token"
REFRESH_URL = "https://graph.threads.net/refresh_access_token"
TIMEOUT = 30


class ThreadsError(RuntimeError):
    pass


def _call(method: str, url: str, params: dict, *, retries: int = 3) -> dict:
    """帶重試的 API 呼叫。5xx 與連線錯誤才重試，4xx 直接丟出（重試也不會好）。"""
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    last_error: str | None = None

    for attempt in range(retries):
        req = urllib.request.Request(
            full_url,
            data=b"" if method == "POST" else None,
            method=method,
            headers={"User-Agent": "sunrise-poster/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:500]
            if exc.code < 500:
                raise ThreadsError(f"HTTP {exc.code}: {body}") from None
            last_error = f"HTTP {exc.code}: {body}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)

        if attempt < retries - 1:
            time.sleep(2**attempt * 3)

    raise ThreadsError(f"呼叫 {url} 失敗：{last_error}")


def get_me(token: str) -> dict:
    """回傳自己的 threads user id 與帳號名稱。"""
    return _call("GET", f"{GRAPH}/me", {"fields": "id,username", "access_token": token})


def recent_posts(token: str, user_id: str, limit: int = 10) -> list[dict]:
    """最近幾篇貼文（含內文與時間），由新到舊。"""
    data = _call(
        "GET",
        f"{GRAPH}/{user_id}/threads",
        {"fields": "id,timestamp,text", "limit": limit, "access_token": token},
    )
    return data.get("data") or []


def publish_text(
    token: str,
    user_id: str,
    text: str,
    *,
    settle_seconds: int = 10,
    reply_to_id: str | None = None,
) -> str:
    """兩段式發文：先建 container，等它就緒，再 publish。回傳貼文 id。

    帶 reply_to_id 就是回覆某則貼文，流程與權限跟一般發文完全相同。
    """
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    container = _call("POST", f"{GRAPH}/{user_id}/threads", params)
    creation_id = container.get("id")
    if not creation_id:
        raise ThreadsError(f"沒拿到 creation_id：{container}")

    # Meta 建議建立 container 後稍等再發布，否則偶爾會 publish 失敗。
    time.sleep(settle_seconds)

    published = _call(
        "POST",
        f"{GRAPH}/{user_id}/threads_publish",
        {"creation_id": creation_id, "access_token": token},
    )
    post_id = published.get("id")
    if not post_id:
        raise ThreadsError(f"發布失敗：{published}")
    return post_id


def exchange_token(short_lived_token: str, app_secret: str) -> dict:
    """把 1 小時的短效 token 換成 60 天的長效 token。

    Meta 後台直接產出來的是短效 token，只能活 1 小時，
    而且一旦過期就換不了，必須重新產一顆再換。
    """
    return _call(
        "GET",
        EXCHANGE_URL,
        {
            "grant_type": "th_exchange_token",
            "client_secret": app_secret,
            "access_token": short_lived_token,
        },
    )


def refresh_token(token: str) -> dict:
    """把長效 token 續期 60 天（token 需已滿 24 小時且未過期）。"""
    return _call("GET", REFRESH_URL, {"grant_type": "th_refresh_token", "access_token": token})
