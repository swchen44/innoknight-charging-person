# innoknight-charging-person

InnoKnight 個人版每日自動充電排程——每天由 **GitHub Actions**（免費）自動登入
iot.innoknight.com，替你建立隔天的充電預約。不用自己顧主機，每月成本 NT$ 0。

本專案是 [innoknight-charging-scheduler](https://github.com/swchen44/innoknight-charging-scheduler)
的雲端化版本（該 repo 跑在自管主機的 crontab 上）。

> **目前狀態（2026-09-03 起）：原自管主機已停用，雲端版是唯一運作中的系統。**
> 辨識期（雲端版暫用 07:00–10:00 供人工核對）已結束，時段改回真正的離峰
> **00:30–06:00**。因為沒有後備了，同日加了**每晚兩輪排程**（20:30 + 21:00）——
> 第二輪若第一輪已成功會直接跳過，若失敗則完整重跑一次。仍建議近期每天早上
> 都檢查一次 [Actions 頁](../../actions/workflows/daily-schedule.yml)。

## 運作方式

每天台北時間**前一晚 20:30 與 21:00 各觸發一輪**（UTC cron `30 12 * * *` /
`0 13 * * *`）：

1. 用 Chrome（**headful** + `--disable-gpu` + Xvfb + CDP）登入 InnoKnight——headful
   真實指紋是通過網站 reCAPTCHA 的關鍵；被 reCAPTCHA 間歇性拒絕時會重載頁面重試最多 5 次
   （完整驗證見 [docs/PDCA.md](docs/PDCA.md)）。
2. 清理過期的一次性舊預約（保留最近一筆）。
3. 若**明天**尚無相同時段預約、且充電樁狀態為「充電樁已就緒」，就自動建立
   （目前為離峰 00:30–06:00；時段由 Variables 設定）。

「前一晚觸發、目標日是明天」是刻意設計：即使排程延遲把實際執行時間推過午夜，
`target_date` 是每次執行當下重新算出的「明天」，不會做出「時段已過」的預約。
第一輪 20:30 觸發保留 4 小時緩衝（2026-09-03 從 22:05 調整，因實測排程延遲達
3.5–5.8 小時，超過原先估計，見 [docs/PDCA.md](docs/PDCA.md)）；**第二輪 21:00
是同日加的備援**——原自管主機停用後雲端版沒有後備了，第二輪給 reCAPTCHA／
裝置就緒狀態多一次機會。第二輪不會重複建立：如果第一輪已成功，它會直接判定
「已存在」並跳過（同一套冪等判斷）；如果第一輪失敗，就是完整重跑一次登入流程。

## 快速開始

### 1. 設定 Secrets（帳號、密碼、充電樁名稱）

三個都用 Secrets——本 repo 是公開的，執行 log 全世界可讀，充電樁名稱含建案與
車位號碼，放 Secrets 才會在 log 中自動遮罩。用 `gh` CLI（或 GitHub 網頁
Settings → Secrets and variables → Actions）：

```bash
gh secret set INNOKNIGHT_USERNAME     # InnoKnight 帳號（email 或手機）
gh secret set INNOKNIGHT_PASSWORD     # InnoKnight 密碼
gh secret set INNOKNIGHT_DEVICE_NAME  # 充電樁完整名稱（照網站上顯示的打）
```

充電時段由 Variables（非機密）控制，**目前為真正離峰的 00:30–06:00**（辨識期已結束，
見文首「目前狀態」）：

```bash
gh variable set INNOKNIGHT_START_TIME --body "00:30"
gh variable set INNOKNIGHT_END_TIME --body "06:00"
```

**絕對不要**把帳密寫進任何檔案再 commit（`.env` 已被 `.gitignore` 排除，但紀律優先）。
建議 GitHub 帳號開啟兩步驟驗證（2FA）。

### 2. 手動跑一次 dry-run 驗證

```bash
gh workflow run daily-schedule.yml
```

到 Actions 頁看 log：登入成功、印出裝置狀態與「會做什麼」，但不改任何遠端資料。
確認沒問題後，勾 `apply` 再跑一次就是真的建立預約：

```bash
gh workflow run daily-schedule.yml -f apply=true
```

### 3. 每日排程（已啟用，兩輪）

`daily-schedule.yml` 的 `schedule` cron **已於 Phase 4 啟用**，每天台北時間前一晚
20:30 與 21:00 各自動執行一次、建立隔日預約，排程觸發一律是正式寫入。要暫停
就把 `schedule:` 底下兩個 `cron:` 都註解掉。

### 手動指定日期或時段

`workflow_dispatch` 的 `target_offset_days`/`start_time`/`end_time` 是一般用途的
覆寫參數（不限於補洞），例如某天自動排程失敗漏建（如 reCAPTCHA 連續被拒），
或想臨時測試不同時段，不需等下一輪自動排程：

```bash
# 補「今天」的預約（預設是明天），並可臨時指定不同時段
gh workflow run daily-schedule.yml -f apply=true -f target_offset_days=0 \
  -f start_time=09:00 -f end_time=11:00
```

三個參數都留空時分別落回預設（明天／Variables 設定的時段），不影響每晚的自動排程。

## 看執行結果

- **Actions 頁**：每天一筆 run。綠燈＝流程正常結束（包含「充電中、今天不用做事」
  這類正常跳過）；**紅燈會寄信通知你**，通常是登入失敗或設定錯誤（例如充電樁
  名稱打錯——`device_not_found` 刻意設計成紅燈，不會默默假裝成功）。
- 綠燈不等於「有建立預約」，點進 log 看實際內容（會印「新增重點預約」或跳過原因）。
- 雲端版與自管主機現在建立的是同一個時段（00:30–06:00），無法再靠時段肉眼區分；
  要確認雲端版有沒有正常運作，直接看 Actions 頁的 run log。

## 注意事項

- **無後備系統**：原自管主機已於 2026-09-03 停用，雲端版是唯一負責充電排程的系統。
  若某晚失敗（reCAPTCHA 5 次重試全被拒、InnoKnight 改版、GitHub Actions 本身異常等），
  當晚沒有其他系統會補上，車可能沒充到電。**紅燈會寄失敗通知信，收到要當天處理**——
  用「[手動指定日期或時段](#手動指定日期或時段)」立即補建，不要等下一輪自動排程。
  建議近期（累積更多穩定性數據前）每天早上都花幾秒看一眼 Actions 頁。
- **60 天規則（已根治）**：公開 repo 的排程 workflow 在 60 天沒有 push/PR 活動後會被
  GitHub 自動停用。本 repo 的 `keepalive.yml` 每月 1、15 號自動 push 一個時間戳
  重置計時，正常情況不需人工介入（若 keepalive 故障，GitHub 停用前仍會寄警告信作為後備）。
- **退出**：刪掉這個 repo 不會讓 InnoKnight 密碼失效；不再使用時，去 InnoKnight
  改密碼才是真正的退出。
- 預設 dry-run、要旗標才寫入；跨午夜的充電時段（如 23:00–02:00）不支援。

## 本機執行（開發／除錯）

需要 Linux（xvfb-run + Chrome）。複製 `.env.example` 為 `.env` 填入設定後：

```bash
uv venv --python 3.12 && uv pip install -e .
uv run python -m innoknight_scheduler.browser_session            # dry-run
uv run python -m innoknight_scheduler.browser_session --execute  # 正式
```

## 文件

- [docs/design.md](docs/design.md) — 設計：架構、流程、關鍵設計決策、安全、待驗證清單
- [docs/plan.md](docs/plan.md) — 分階段實作計劃與目前進度
- [docs/PDCA.md](docs/PDCA.md) — **雲端化的驗證與除錯全紀錄**：go/no-go 實測證據鏈、
  reCAPTCHA 為何要用 headful、兩個中途被實驗推翻的錯誤判斷、網路查證來源
- [docs/developer.md](docs/developer.md) — 開發者文件：模組結構、uv/ruff/mypy 工具鏈、
  測試、維護情境（InnoKnight 改版怎麼修等）
- 上游研究：[cloud-architecture.md](https://github.com/swchen44/innoknight-charging-scheduler/blob/main/docs/research/cloud-architecture.md)

## 專案進度

| 階段 | 狀態 |
|---|---|
| 設計與計劃 | ✅ |
| Phase 1 移植程式碼、workflow、測試 | ✅ |
| 安全強化（憑證不進 JS、exit code、Action 釘 SHA）— 因 repo 公開而提前 | ✅ |
| **Phase 2 dry-run 驗證 IP 風控（go/no-go）** | ✅ **GO——端到端跑通**（見 [PDCA.md](docs/PDCA.md)） |
| 真實寫入驗證（apply + 冪等回查） | ✅ 完成（見 [PDCA.md](docs/PDCA.md)） |
| **Phase 4 啟用每晚自動排程** | ✅ 已啟用；辨識期已結束、已改回離峰 00:30–06:00 |
| **舊系統退場** | ✅ 原自管主機已停用（2026-09-03）——雲端版是唯一系統，無後備 |
| Phase 4 後續：持續穩定性量測 | ⬜ 進行中，見下方注意事項與 [PDCA.md](docs/PDCA.md) |
| Phase 5 模板化分享（未來） | ⬜ |
