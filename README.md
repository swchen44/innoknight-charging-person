# innoknight-charging-person

InnoKnight 個人版每日自動充電排程——把原本跑在自管雲端主機 crontab 上的
[innoknight-charging-scheduler](https://github.com/swchen44/innoknight-charging-scheduler)，
移植到 **GitHub Actions 免費額度**上執行，完全不用自己顧主機，每月成本 NT$ 0。

## 做什麼

每天台北時間前一晚 22:05（UTC cron `5 14 * * *`）由 GitHub Actions 觸發：

1. 用 Xvfb + Chrome（CDP）登入 iot.innoknight.com。
2. 清理過期的一次性舊預約（保留最近一筆）。
3. 若**隔天**尚無 00:30–06:00 離峰充電預約、且充電樁就緒，就自動建立。

帳號密碼只存在本 repo 的 GitHub Actions Secrets，核心 Python 邏輯沿用原專案、不重寫。

## 專案狀態

**設計階段**——目前 repo 只有設計與計劃文件，程式碼移植依計劃分階段進行：

| 階段 | 狀態 |
|---|---|
| 設計與計劃 | ✅ 完成 |
| Phase 1 移植程式碼與 workflow 骨架 | ⬜ 未開始 |
| Phase 2 手動 dry-run 驗證 IP 風控（go/no-go） | ⬜ 未開始 |
| Phase 3 安全強化 | ⬜ 未開始 |
| Phase 4 啟用每日排程、舊系統退場 | ⬜ 未開始 |
| Phase 5 模板化分享（未來） | ⬜ 未開始 |

## 文件

- [docs/design.md](docs/design.md) — 設計文件：架構、執行流程、關鍵設計決策、安全、風險與待驗證清單
- [docs/plan.md](docs/plan.md) — 實作計劃：分階段步驟與各階段完成條件
- 上游研究：[cloud-architecture.md](https://github.com/swchen44/innoknight-charging-scheduler/blob/main/docs/research/cloud-architecture.md)（情境一，已經過兩輪獨立架構評審）

## 關鍵設計決策（摘要）

- **前一晚觸發、target_date = 明天**：結構性避開 GitHub 排程觸發器最高 2 小時以上的延遲，
  不會重演「時段已過才建立預約」的舊 bug。
- **UTC cron 為唯一事實來源**：台北恆為 UTC+8 無日光節約，`5 14 * * *` 恆等於台北 22:05。
- **repo 保持私有**：私有 repo 月 2,000 分鐘免費額度（本用量約 1.5–3%），
  且不受公開 repo「60 天無活動停用排程」的無聲失效影響。
- **密碼不以字串內嵌進 JS**：GitHub secret masking 是字面比對，跳脫後會失效；
  改用 CDP `Runtime.callFunctionOn` 傳值。
- **設定錯誤回非 0**：讓 GitHub 內建的失敗通知信成為免架設的告警管道。

詳細理由見 [docs/design.md](docs/design.md) §4。

## 安全注意事項

- 帳密只透過 GitHub Settings 或 `gh secret set` 設定，**絕不 commit 進任何檔案**。
- GitHub 帳號開啟 2FA。
- 刪除本 repo 不會讓 InnoKnight 密碼失效；真正的退出方式是去 InnoKnight 改密碼。
