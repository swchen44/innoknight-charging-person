# innoknight-charging-person

InnoKnight 個人版每日自動充電排程——每天由 **GitHub Actions**（免費）自動登入
iot.innoknight.com，替你建立隔天的充電預約。不用自己顧主機，每月成本 NT$ 0。

本專案是 [innoknight-charging-scheduler](https://github.com/swchen44/innoknight-charging-scheduler)
的雲端化版本（該 repo 跑在自管主機的 crontab 上）。

> **目前狀態：辨識期並行運行（2026-08-30 起）。** 原自管主機不停、繼續用
> 00:30–06:00 離峰時段；雲端版（本 repo）刻意改用**早上 07:00–10:00** 這個明顯不同的
> 時段，方便人工在 InnoKnight 上一眼分辨哪筆預約是雲端版建立的、確認雲端版運作正常。
> 核對穩定後，再把雲端版改回離峰 00:30–06:00 並讓自管主機退場。時段由 GitHub Variables
> 控制，改時段不需要動程式碼。

## 運作方式

每天台北時間**前一晚 22:05**（UTC cron `5 14 * * *`）觸發：

1. 用 Chrome（**headful** + `--disable-gpu` + Xvfb + CDP）登入 InnoKnight——headful
   真實指紋是通過網站 reCAPTCHA 的關鍵；被 reCAPTCHA 間歇性拒絕時會重載頁面重試最多 3 次
   （完整驗證見 [docs/PDCA.md](docs/PDCA.md)）。
2. 清理過期的一次性舊預約（保留最近一筆）。
3. 若**明天**尚無相同時段預約、且充電樁狀態為「充電樁已就緒」，就自動建立
   （辨識期為 07:00–10:00；時段由 Variables 設定）。

「前一晚觸發、目標日是明天」是刻意設計：GitHub 排程觸發器尖峰時段可能延遲
15 分鐘到 2 小時以上，前移觸發時間讓延遲再大也不會做出「時段已過」的預約。

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

充電時段由 Variables（非機密）控制。**目前為辨識期，設為 07:00–10:00**（與自管主機的
00:30–06:00 區分，見文首「目前狀態」）；核對雲端版穩定後改回 `00:30` / `06:00` 即可：

```bash
gh variable set INNOKNIGHT_START_TIME --body "07:00"   # 辨識期；離峰改回 00:30
gh variable set INNOKNIGHT_END_TIME --body "10:00"     # 辨識期；離峰改回 06:00
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

### 3. 每日排程（已啟用）

`daily-schedule.yml` 的 `schedule` cron **已於 Phase 4 啟用**，每天台北時間前一晚
22:05 自動執行、建立隔日預約，排程觸發一律是正式寫入。要暫停就把 `schedule:` 兩行
重新註解掉。

## 看執行結果

- **Actions 頁**：每天一筆 run。綠燈＝流程正常結束（包含「充電中、今天不用做事」
  這類正常跳過）；**紅燈會寄信通知你**，通常是登入失敗或設定錯誤（例如充電樁
  名稱打錯——`device_not_found` 刻意設計成紅燈，不會默默假裝成功）。
- 綠燈不等於「有建立預約」，點進 log 看實際內容（會印「新增重點預約」或跳過原因）。
- **辨識期人工核對**：雲端版建立的是 **07:00–10:00**、自管主機是 00:30–06:00，
  在 InnoKnight 上看到 07:00–10:00 的預約就表示雲端版設定成功。

## 注意事項

- **60 天規則**：公開 repo 的排程 workflow 在 60 天沒有任何 push/PR 活動後會被
  GitHub 自動停用（排程執行本身不算活動）。GitHub 會先寄警告信，收到就到
  Actions → daily-schedule 按「Enable」；平常任何一次 push 都會重置計時。
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
| **Phase 4 啟用每晚自動排程** | ✅ 已啟用（辨識期 07:00–10:00、兩邊並行） |
| Phase 4 後續：穩定性量測、改回離峰、舊系統退場 | ⬜ 進行中／待人工核對 |
| Phase 5 模板化分享（未來） | ⬜ |
