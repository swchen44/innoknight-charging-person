# 開發者文件

維護本專案需要知道的一切：模組結構、開發環境（uv）、測試、workflow 行為與常見維護情境。
設計理由見 [design.md](design.md)，階段計劃見 [plan.md](plan.md)。

## 專案結構

```
innoknight_scheduler/
├── automation.py        # 每日流程的純邏輯：清理舊預約 → 檢查 target_date → 建立預約
├── scheduler.py         # 純函式：payload 組裝、過期清理判斷、重複預約判斷
├── client.py            # InnoKnight HTTP API client（read_balance / schedule_set / …）
├── crypto.py            # InnoKnight 通訊協定的 AES 加密層（協定常數來自網站前端）
├── browser_session.py   # headful Chrome + CDP 登入流程（含重試）；正式無人值守入口
└── main.py              # direct API 登入入口（開發測試用，可能被 reCAPTCHA 擋）
.github/workflows/
├── daily-schedule.yml   # 每日充電預約（schedule cron 已啟用 + workflow_dispatch）
├── ci.yml               # push/PR 時跑 ruff + mypy + 單元測試
└── keepalive.yml        # 每月 2 次自動 push 時間戳，防公開 repo 60 天排程停用
tests/
├── unittest/            # 純單元測試，不需要憑證，CI 自動跑
└── integration/         # 需要真實 InnoKnight 帳號；預設 skip，見下方說明
```

### 模組之間的關係

`browser_session.main()` 是正式入口：

1. `login_with_browser()` 以 **headful** Chrome（`build_chrome_command` 自動帶
   `--disable-gpu` + xvfb-run）啟動、`_fill_login_form()` 用 CDP `Input.insertText`
   填入帳密（絕不進 JS 字串）並點擊登入。
2. **直接從登入 API 回應取得 session**：`_session_from_login_response()` 解析
   `get_end_user_token` 的回應 body 拿 `uuid`/`token`（不依賴前端寫 cookie 的時機；
   保留讀 `user` cookie 作後備）。reCAPTCHA 對 GitHub datacenter IP 是**間歇性放行**，
   被拒時重載頁面重試最多 `max_login_attempts`（預設 3）次。
3. session 掛到 `InnoKnightClient`，`run_daily_workflow()`（`automation.py`）執行純邏輯，
   透過 client 呼叫遠端 API。

> 為何是 headful 而不是 headless、為何要 `--disable-gpu`、reCAPTCHA 的間歇性——
> 完整的驗證與除錯證據鏈見 [PDCA.md](PDCA.md)（含兩個中途被實驗推翻的錯誤判斷）。

`crypto.py` **不能刪**：`client.py` 的 `login()` / `set_schedule()` / `remove_schedule()`
都依賴它把 payload 加密後才送出，不加密伺服器不收。它是 InnoKnight 網站自身協定的
一部分，跟帳密保管完全是兩回事。

## 開發環境（uv）

工具鏈：[uv](https://docs.astral.sh/uv/) 管理環境與執行、ruff lint、mypy（strict）型別檢查。

```bash
# 建立環境並安裝（含 dev 依賴）
uv venv --python 3.12
uv pip install -e '.[dev]'

# 開發迴圈
uv run ruff check .            # lint
uv run mypy                    # 型別檢查（strict，範圍 = innoknight_scheduler）
uv run pytest tests/unittest   # 單元測試

# 一次性工具可用 uvx（不進專案環境）
uvx ruff check .
```

CI（`ci.yml`）跑的就是上面三個指令，本機全綠 = CI 全綠。

## 測試

### 單元測試 `tests/unittest/`

不需要任何憑證與網路，CI 每次 push/PR 都跑：

- `test_scheduler.py` — 純函式：payload、過期清理、重複預約（含 target_date 區分）。
- `test_daily_workflow.py` — `run_daily_workflow` 全流程（fake client），
  含「前一晚觸發、target_date=明天」的核心行為。
- `test_browser_session.py` — cookie 解析、登入腳本。**重點測試**：
  `test_fill_login_form_sends_credentials_only_via_insert_text` 保證憑證只經由
  CDP `Input.insertText` 傳遞、不出現在任何被 evaluate 的 JS 字串——這是公開 repo
  的硬性安全規則，改動登入流程時此測試必須維持通過。
- `test_browser_workflow.py` — client 的加密 device_id 行為與排程 fallback。

### 整合測試 `tests/integration/`

`test_live_browser_workflow.py` 需要真實帳號（環境變數 `INNOKNIGHT_USERNAME` /
`INNOKNIGHT_PASSWORD` / `INNOKNIGHT_DEVICE_NAME`）與 Chrome
（`INNOKNIGHT_CHROME_PATH` 或預設路徑），缺任一項自動 skip；
只做 dry-run，不改遠端資料。

它同時是 [plan.md](plan.md) Phase 2 的 go/no-go 驗證載體：在 GitHub Actions 上跑通
= Actions 共用 IP 沒有被 InnoKnight 風控擋下。

本機（Linux）執行：

```bash
INNOKNIGHT_USERNAME=... INNOKNIGHT_PASSWORD=... INNOKNIGHT_DEVICE_NAME=... \
  uv run pytest tests/integration -v
```

## Workflow 行為

### `daily-schedule.yml`

| 觸發 | 模式 |
|---|---|
| `workflow_dispatch`，不勾 apply | dry-run（只讀不寫） |
| `workflow_dispatch`，勾 apply | 正式執行（`--execute`） |
| `schedule`（**已啟用**） | 一律正式執行 |

排程時間 `5 14 * * *`（UTC）= 台北 22:05（前一晚），程式內 `--target-offset-days`
預設 1，所以建立的是**明天**的預約。時段由 Variables 決定——**辨識期為 07:00–10:00**
（與自管主機的 00:30–06:00 區分，見 [PDCA.md](PDCA.md)）。不要把 cron 改回午夜附近——
設計理由（排程延遲）見 design.md §4 第 1 條。

`workflow_dispatch` 額外提供 `target_offset_days` / `start_time` / `end_time` 三個補洞
參數（留空 = 落回預設/Variables）。實作用 `inputs.x || vars.X || 預設` 的 fallback 鏈；
`inputs.*` 在 `schedule` 觸發下是 `null`（GitHub 官方行為，非本專案臆測），`||` 安全
落空不影響每晚自動排程。用途見 README「補洞」段落，驗證見 PDCA.md。

Exit code 約定（`browser_session.main()`）：

| 情況 | exit code | Actions 顯示 |
|---|---|---|
| 成功建立 / 已存在 / 充電中跳過 | 0 | 綠燈 |
| `schedule_set_failed`（遠端拒絕） | 1 | 紅燈 → 通知信 |
| `device_not_found` / `device_missing_schedule_target`（設定錯誤） | 2 | 紅燈 → 通知信 |

### `ci.yml`

push 到 main 與 PR 時跑 ruff + mypy + `tests/unittest`。整合測試刻意不在 CI 跑
（需要憑證，且公開 repo 的 PR 不該碰 Secrets）。

### `keepalive.yml`

每月 1、15 號自動 push 一個時間戳 commit（`.github/last-activity.txt`），
根治「公開 repo 60 天無活動 → 排程被無聲停用」。`permissions: contents: write`
（本 repo 唯一需要寫入權限的 workflow）。commit 帶 `[skip ci]`。詳見下方「60 天規則」。

## Secrets 與 Variables

| 名稱 | 類型 | 說明 |
|---|---|---|
| `INNOKNIGHT_USERNAME` | Secret | InnoKnight 帳號 |
| `INNOKNIGHT_PASSWORD` | Secret | InnoKnight 密碼 |
| `INNOKNIGHT_DEVICE_NAME` | **Secret**（不是 Variable） | 充電樁名稱是建案＋車位號碼（住處線索）；公開 repo 的 log 全世界可讀，放 Secret 才會自動遮罩 |
| `INNOKNIGHT_START_TIME` | Variable（選填，code 預設 00:30） | 充電開始時間；**辨識期設 07:00** |
| `INNOKNIGHT_END_TIME` | Variable（選填，code 預設 06:00） | 充電結束時間；**辨識期設 10:00** |

設定方式（絕不寫進任何檔案）：

```bash
gh secret set INNOKNIGHT_USERNAME
gh secret set INNOKNIGHT_PASSWORD
gh secret set INNOKNIGHT_DEVICE_NAME
```

## 安全規則（改程式前必讀）

1. **憑證絕不進 JS 字串**。登入流程的 focus/點擊腳本連參數都不收帳密；帳密只經由
   CDP `Input.insertText` 傳遞。GitHub secret masking 是字面比對，任何跳脫（JSON、
   Unicode）後的內嵌字串都不會被遮罩，而公開 repo 的 log 全世界可讀。
   對應測試在 `tests/unittest/test_browser_session.py`。
2. **錯誤訊息不回印請求參數**。`CdpClient.call()` 只帶 CDP 回傳的 error 物件，
   不把我們送出的 params（可能含密碼）放進例外訊息。
3. **第三方 Action 一律釘完整 commit SHA**，不用 `@v1` 這種可變 tag；
   `permissions:` 維持最小——`daily-schedule`/`ci` 用 `contents: read`，只有
   `keepalive` 需要 `contents: write`（自動 push 時間戳）。升級 Action 時查新版 SHA 換上。
4. **不新增任何硬編碼個人資訊**（裝置名稱、住址線索、序號）。單元測試用
   `測試充電樁A-1` 這類通用值。

## 常見維護情境

### InnoKnight 改版登入頁（幾乎必然發生）

症狀：workflow 紅燈，log 出現 `login_input_not_found` 或
`Login failed after N attempts`。逾時的錯誤訊息會附上頁面診斷（URL、標題、
欄位值長度、captcha 偵測、登入 API 的 `success`/`message`——都不含憑證），據此判斷是
選擇器失效還是 reCAPTCHA 連續被拒。

修法：開真實網站看新的 DOM，更新 `browser_session.py` 的 `build_focus_script()` /
`build_click_login_script()` 選擇器，跑單元測試確認憑證隔離規則沒破，
再手動 dispatch 一次 dry-run 驗證。

### 排程停止執行（公開 repo 的 60 天規則）

公開 repo 60 天沒有 push/PR 活動，GitHub 會自動停用 `schedule` workflow
（排程執行本身**不算**活動）。**本 repo 已用 `keepalive.yml` 根治**：每月 1、15 號
自動 push 時間戳 commit 重置計時，正常情況不需人工介入。若 keepalive 本身故障
（例如 `contents: write` 權限被 repo 設定收回），GitHub 停用前仍會寄警告信；
收到就到 Actions → 對應 workflow → Enable，並修好 keepalive。

### 「綠燈但沒排到程」

exit code 0 不代表建立了預約（`device_not_ready` 是正常跳過）。每隔一段時間
人工看一次 run log 的實際內容；log 會印「新增重點預約」或明確的跳過原因。

### 升級依賴

```bash
uv pip install -e '.[dev]' --upgrade
uv run pytest tests/unittest && uv run ruff check . && uv run mypy
```

升級後手動 dispatch 一次 dry-run，確認登入流程在真實環境仍成功。
