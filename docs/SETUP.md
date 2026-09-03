# 設定與維運

## 架構

```
每天 04:40（固定）          job 起來後                 日出那一秒
外部 cron ──dispatch──▶ 算出今天日出、裝好套件 ──睡覺──▶ 發文
```

外部 cron 只要「每天固定時間打一次」，**不需要知道日出幾點**。
job 起來後自己算出當天日出時刻，把準備工作全做完（裝套件、查帳號、
檢查今天有沒有發過、確認今天不是假日），然後睡到日出前 12 秒才醒來發文。
提早 12 秒是因為 Threads 發文是兩段式的，要讓貼文剛好落在日出那一刻。

代價是 job 每天要空睡 25～120 分鐘。公開 repo 的 Actions 分鐘數不計費，
所以這個代價是零。私有 repo 一個月約 1400 分鐘，會吃掉免費額度的七成。

## 檔案

| 檔案 | 用途 |
| --- | --- |
| `post_sunrise.py` | 算日出、睡到準點、發文 |
| `threads_api.py` | Threads API 封裝（純標準函式庫） |
| `calendar_tw.py` | 查台灣辦公日曆表，決定今天要不要發 |
| `exchange_token.py` | 把 1 小時短效 token 換成 60 天長效 token |
| `refresh_token.py` | 把長效 token 再續期 60 天 |
| `.github/workflows/sunrise.yml` | 收到 dispatch 就起跑 |
| `.github/workflows/refresh-token.yml` | 每月自動續期 token |

## 安裝

指令以 cmd 為準（PowerShell 5.1 不支援 `&&`，所以一行一個指令）。

### 1. 先把短效 token 換成長效

**這步很容易漏掉。** Meta 後台按「Generate token」給的是短效 token，
**只能活 1 小時**，直接拿去用很快就會收到 `Session has expired`。

到 Meta 應用程式後台複製兩樣東西填進 `.env`：

- `THREADS_ACCESS_TOKEN`：剛產生的短效 token
- `THREADS_APP_SECRET`：App settings → Basic → App secret

然後**立刻**執行（短效 token 一過期就換不了，得回頭重產）：

```
python exchange_token.py
```

把印出來的長效 token 貼回 `.env` 的 `THREADS_ACCESS_TOKEN`。

### 2. 本機測試

```
pip install -r requirements.txt
copy .env.example .env
notepad .env
```

除了 token，還要填 `POST_CHARS`（貼文用的字元集）。然後：

```
python post_sunrise.py --sample
python post_sunrise.py --show
python post_sunrise.py --force --dry-run
python post_sunrise.py --force
```

`--sample` 抽幾則看看，`--show` 看未來兩週的日出與排班，
`--dry-run` 是演練不真的發，最後一行才會真的發出去。

### 3. 設 Secrets

```
gh secret set THREADS_ACCESS_TOKEN
```

`POST_CHARS`、`POST_SUFFIX`、`REPLY_TEMPLATE` 含中文，**一定要走網頁設定**——
cmd 的 cp950 主控台會把貼上的中文吃掉，變成空值送出去。

`POST_CHARS` 放進 secret 的用意是：公開的原始碼只描述機制，
看不出實際會發什麼內容。

帳號 ID 不用設，程式拿 token 去 `/me` 查——手填容易錯，
錯了會噴一個很難懂的 `Object with ID does not exist`。

### 4. 開觸發用的 PAT

GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token。

- Repository access：只選這個 repo
- Permissions：**Contents: Read and write**（`repository_dispatch` 需要）

### 5. 設定外部 cron

任何會定時發 HTTP 請求的服務都行，例如 cron-job.org。建一個 job：

| 欄位 | 值 |
| --- | --- |
| URL | `https://api.github.com/repos/<帳號>/<repo>/dispatches` |
| Method | `POST` |
| 時間 | 每天 04:40，時區 Asia/Taipei |
| Header | `Authorization: Bearer <PAT>` |
| Header | `Accept: application/vnd.github+json` |
| Header | `Content-Type: application/json` |
| Body | `{"event_type":"sunrise"}` |

04:40 是抓在全年最早日出（05:03）前留 20 分鐘餘裕。
存檔後點 TEST RUN，應該回 **204 No Content**。

### 6. 讓 token 自動續期

長效 token **只有 60 天壽命**，過期後不會有任何通知，就是靜靜停掉。
`refresh-token.yml` 每月 1 號自動續期並寫回 Secret，
但寫 Secret 需要另一顆 fine-grained PAT，權限給 **Secrets: Read and write**：

```
gh secret set SECRET_UPDATER_PAT
```

刻意跟觸發用的 PAT 分開：觸發用的那顆存在第三方服務手上，
洩漏了最多被亂觸發；如果同一顆還能寫 Secrets，代價就大得多。

## 指令參數

| 參數 | 用途 |
| --- | --- |
| `--wait` | 睡到今天日出那一刻再發（workflow 用的就是這個） |
| `--now` | 立刻發，但仍檢查今天是否已發過 |
| `--force` | 立刻發，連休假與重複檢查都跳過 |
| `--dry-run` | 只演練不發文 |
| `--show` | 印出未來 14 天的日出與排班 |
| `--sample` | 隨機抽 10 則看看 |

## 環境變數

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `POST_CHARS` | （必填）| 貼文用的字元集 |
| `POST_LENGTH` | `3` | 每則取幾個字 |
| `POST_SUFFIX` | （空）| 接在後面的固定字尾 |
| `POST_TEXT` | （空）| 填了就固定發這句，忽略上面三個 |
| `MAKEUP_TEXT` | （空）| 補假那天要發的內容；留空就跟其他假日一樣沉默，見下方說明 |
| `REPLY_TEMPLATE` | （空）| 貼文底下那則回覆的樣板；留空就不發，見下方可用欄位 |
| `LAT` / `LON` | 台北 | 觀測地點 |
| `SETTLE_SECONDS` | `10` | 建 container 到 publish 的間隔，也是提早起跑的秒數 |
| `GRACE_MINUTES` | `60` | job 起太晚時，比日出晚超過這麼久就不補發 |
| `MAX_WAIT_MINUTES` | `330` | 睡眠時間上限，防呆用 |

## REPLY_TEMPLATE 可用欄位

| 欄位 | 說明 |
| --- | --- |
| `{time}` | 實際發文時刻（HH:MM:SS） |
| `{holiday_name}` | 下一個連假的代表假日名稱（查 `calendar_tw.next_long_weekend`） |
| `{holiday_days}` | 距離**連假第一天**還有幾天 |

`holiday_days` 算的是連假的起點，不是那個假日本身的日期——很多連假
其實從前面的週末就開始了（教師節是週一，但連假從週六就開始）。
單獨一天、沒接到週末的假日不算連假，會被跳過繼續往後找下一個。
含 `holiday_*` 的樣板在查不到下一個連假時會直接跳過（記警告、不發回覆）。

## 補假的特殊處理

補假（國定假日遇週末往後補的那天）不算真正的節日，比較像行政上湊出來的。
設定 `MAKEUP_TEXT` 之後，補假那天會照常發文（用這個固定內容，通常比平常
簡短），其他假日則維持完全沉默。不設就跟以前一樣，補假也是沉默。

`--show` 會把這種日子標成「發文（補假，簡短版）」，方便跟真正的休息日區分。

## 維運注意

- **Actions 的 log 是公開的**，所以發文成功那行只印字數與 post id，不印內文。
- 回覆是 best-effort：主貼文發出去之後才發，失敗只記警告、不讓 workflow 標紅，
  否則早上看到紅叉會誤以為當天沒發成功。
- 去重是比對「今天發過、且形狀符合設定」的貼文，
  你自己另外發文不會影響它；但手動發了一則剛好符合形狀的貼文，那天就不會再發。
- 去重查詢失敗時程式直接失敗、當天不發——寧可漏一天，也不要重複洗版。
- 行事曆資料每年更新一次，通常前一年年中就會有下一年。
  真的缺了會退回「只休週六日」，不會停擺。
- Token scope 需要 `threads_basic` + `threads_content_publish`。
