# innoknight-charging-person

InnoKnight 個人版每日自動充電排程——每天由 **GitHub Actions**（免費）自動登入
iot.innoknight.com，替你建立**隔天 00:30–06:00** 的離峰充電預約。
不用自己顧主機，每月成本 NT$ 0。

本專案是 [innoknight-charging-scheduler](https://github.com/swchen44/innoknight-charging-scheduler)
的雲端化版本（該 repo 跑在自管主機的 crontab 上）。

## 運作方式

每天台北時間**前一晚 22:05**（UTC cron `5 14 * * *`）觸發：

1. 用 Xvfb + Chrome（CDP）登入 InnoKnight 網站。
2. 清理過期的一次性舊預約（保留最近一筆）。
3. 若**明天**尚無 00:30–06:00 預約、且充電樁狀態為「充電樁已就緒」，就自動建立。

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

充電時段預設 00:30–06:00；要改的話設 Variables（非機密）：

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

### 3. 啟用每日排程

手動驗證通過後（見 [docs/plan.md](docs/plan.md) Phase 2→4），把
`.github/workflows/daily-schedule.yml` 裡 `schedule:` 那三行註解解開、push 即可。
排程觸發一律是正式執行。

## 看執行結果

- **Actions 頁**：每天一筆 run。綠燈＝流程正常結束（包含「充電中、今天不用做事」
  這類正常跳過）；**紅燈會寄信通知你**，通常是登入失敗或設定錯誤（例如充電樁
  名稱打錯——`device_not_found` 刻意設計成紅燈，不會默默假裝成功）。
- 綠燈不等於「有建立預約」，點進 log 看實際內容（會印「新增重點預約」或跳過原因）。

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
- [docs/developer.md](docs/developer.md) — 開發者文件：模組結構、uv/ruff/mypy 工具鏈、
  測試、維護情境（InnoKnight 改版怎麼修等）
- 上游研究：[cloud-architecture.md](https://github.com/swchen44/innoknight-charging-scheduler/blob/main/docs/research/cloud-architecture.md)

## 專案進度

| 階段 | 狀態 |
|---|---|
| 設計與計劃 | ✅ |
| Phase 1 移植程式碼、workflow、測試 | ✅ |
| 安全強化（憑證不進 JS、exit code、Action 釘 SHA）— 因 repo 公開而提前 | ✅ |
| Phase 2 手動 dry-run 驗證 IP 風控（go/no-go） | ⬜ 待使用者設定 Secrets 後執行 |
| Phase 4 啟用每日排程、舊系統退場 | ⬜ |
| Phase 5 模板化分享（未來） | ⬜ |
