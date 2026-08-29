# 實作計劃：場景一 Use Case 1 — 個人每日自動充電預約

> 設計依據見 [design.md](design.md)。每個 Phase 都有明確的完成條件（Definition of Done），
> 依序執行，**Phase 2 的 go/no-go 驗證不通過就停下重新評估，不進入後續階段**。

## 總覽

> **2026-08-29 調整**：使用者決定 repo **公開**。公開 repo 的 Actions log 全世界可讀，
> 因此原 Phase 3 的安全強化（密碼不內嵌 JS、exit code、Action 釘 SHA）**提前到
> Phase 1 一併完成**，必須先於任何真實執行。開發工具鏈定為 uv/uvx + ruff + mypy。

```
Phase 1 移植程式碼＋安全強化（✅ 完成）→ Phase 2 手動觸發驗證（go/no-go）
→ Phase 4 啟用每日排程 + 量測 → Phase 5（未來）模板化分享
```

| Phase | 內容 | 狀態 |
|---|---|---|
| 1 | 移植核心程式碼、workflow、測試（tests/unittest + tests/integration） | ✅ |
| 3（提前併入 1） | 安全強化：憑證不進 JS、exit code、Action 釘完整 SHA | ✅ |
| 2 | `workflow_dispatch` 手動 dry-run，驗證 IP 風控（go/no-go） | ⬜ 待 Secrets |
| 4 | 啟用 `schedule` cron、`--apply` 上線、量測延遲與耗時 | ⬜ |
| 5 | （通過驗證後才考慮）Template repository 分享給朋友 | ⬜ |

---

## Phase 1：移植程式碼與 workflow 骨架 ✅（已完成，含提前的安全強化）

**實際完成內容**：核心模組移植完畢（`crypto.py` 保留、硬編碼裝置名稱清除、
`target_date=明天` 支援）；`daily-schedule.yml`（workflow_dispatch，schedule 註解待
Phase 4）與 `ci.yml`（uv + ruff + mypy + 單元測試）就緒；憑證改由 CDP
`Input.insertText` 傳遞、不進任何 JS 字串；設定錯誤回非 0；第三方 Action 全數釘
完整 commit SHA。測試拆為 `tests/unittest/`（25 個，CI 自動跑）與
`tests/integration/`（需真實帳號，即 Phase 2 的驗證載體）。細節見
[developer.md](developer.md)。以下為原始規劃內容，保留供追溯：

**目標**：本 repo 具備可在 GitHub Actions 上執行 dry-run 的完整程式碼。

1. 從 `innoknight-charging-scheduler` 複製核心模組（沿用、不重寫）：
   - `innoknight_scheduler/`：`automation.py`、`browser_session.py`、`client.py`、
     `crypto.py`（**必須保留**，是 InnoKnight 協定層）、`scheduler.py`、`main.py`、`__init__.py`
   - `pyproject.toml`、`tests/`
   - **不複製** `.env` / `scripts/innoknight-cron.sh`（被 Secrets 與 workflow 取代）
2. 清理硬編碼的個人資訊：`automation.py`、`browser_session.py`、`.env.example`、`README.md`
   中的真實裝置名稱（`晴空匯EVBA-1_R_B3-448`）改為必填環境變數、不給預設值。
3. 建立 `.github/workflows/daily-schedule.yml`：
   - 觸發：先只放 `workflow_dispatch`（帶 `apply` boolean input，預設 false = dry-run）。
     **`schedule` cron 這個階段先不加**，等 Phase 4。
   - 步驟：checkout → setup-python → `pip install -e .` →
     `sudo apt-get update && sudo apt-get install -y xvfb` →
     `browser-actions/setup-chrome`（或改用 runner 預裝 Chrome，Phase 2 實測後擇一固定）→
     執行 `python -m innoknight_scheduler.main`（dry-run／`--apply` 依 input）。
   - `permissions: contents: read`。
   - 帳密來源：`secrets.INNOKNIGHT_USERNAME` / `secrets.INNOKNIGHT_PASSWORD`；
     設定值來源：`vars.INNOKNIGHT_DEVICE_NAME`、`vars.INNOKNIGHT_CHROME_PATH` 等。
4. 用 `gh secret set` / `gh variable set` 設定 Secrets 與 Variables（**不經任何檔案**）。
5. 本機跑 `pytest` 確認移植後測試全綠。

**完成條件**：repo 內程式碼齊全、測試通過、workflow YAML 存在且語法有效（`gh workflow list` 可見）。

## Phase 2：手動觸發驗證（go/no-go）

**目標**：回答設計文件 §6 第一項——GitHub Actions 共用 IP 會不會被 InnoKnight 擋下。

1. `gh workflow run daily-schedule.yml`（dry-run）手動觸發。
2. 檢查 log：登入是否成功、是否出現 reCAPTCHA／風控攔截、cookie 是否正常取得、
   `read_balance` 是否回傳既有排程。
3. 記錄 Chrome 實際執行檔路徑與單次 job 總耗時（寫回 design.md §6 清單）。
4. 連續數天、不同時段各觸發一次，確認不是單次僥倖。

**結果（2026-08-30）：GO ✅**——dry-run 端到端跑通，完整證據與除錯過程見 [PDCA.md](PDCA.md)。
關鍵發現：必須用 **headful**（headless 被 reCAPTCHA 擋、headful 間歇性放行），
且 headful 在 runner 需 `--disable-gpu`；登入改為直接讀登入 API 回應、被拒時重載重試最多 3 次。
剩餘待辦轉為「穩定性量測」（見下方風險備忘與 design.md §6）。

## Phase 3：安全強化 ✅（已提前至 Phase 1 完成——repo 公開，log 全世界可讀，必須先於任何真實執行）

**目標**：修掉研究文件評審點名的安全問題，未來情境二直接沿用。

1. **密碼不內嵌 JS**：把 `build_login_script()` 的 `json.dumps()` 內嵌方式，
   改為 CDP `Runtime.callFunctionOn` + `arguments` 傳值（或 `Input.insertText` 逐欄輸入）；
   錯誤處理路徑裁掉可能包含表達式內容的欄位。以既有測試＋一次真實 dry-run 驗證登入成功率不變。
2. **exit code 調整**：`device_not_found` / `device_missing_schedule_target` 回非 0
   （設定錯誤要讓 GitHub 失敗通知信看得到）；`device_not_ready` 維持 0（正常跳過）。
3. **釘死供應鏈**：workflow 中所有第三方 Action 改用完整 commit SHA；
   確認 `permissions:` 已是最小。
4. GitHub 帳號開 2FA（若尚未）。

**完成條件**：三項皆完成、dry-run 仍成功、密碼字面不出現在任何 log（含刻意觸發錯誤的測試 run）。

## Phase 4：啟用每日排程與量測

**目標**：正式上線，取代現有雲端主機上的 crontab。

1. workflow 加上 `schedule: - cron: "5 14 * * *"`（UTC，＝台北前一晚 22:05），
   `target_date` 邏輯確認為「明天」。保留 `workflow_dispatch` 供手動補跑。
2. 先以 dry-run 模式跑 2–3 天，比對觸發時間戳量測**排程延遲分佈**。
3. 確認無誤後改為 `--apply` 正式模式。
4. **舊系統退場**：確認本 repo 連續成功建立預約數天後，停用原雲端主機的 crontab
   （先註解保留，一週後再刪），避免兩邊同時操作同一帳號。
5. 觀察 1–2 週：每隔幾天人工看一次 log（不能只看綠燈，見 design.md §8）；
   記錄 Actions 分鐘數用量。

**完成條件**：連續 7 天以上由 GitHub Actions 成功維護隔日預約、舊 crontab 已停用、
延遲與耗時數據已記錄回 design.md §6 清單。

## Phase 5（未來）：模板化分享給朋友

**前提**：Phase 2 go/no-go 通過、Phase 4 穩定運行一段時間。此階段另行細部規劃，要點先記錄：

- 轉為 **Template repository**，說明文件明確寫「請用 Use this template，**不要用 Fork**」
  （公開 repo 的 fork 無法事後改私有）。
- 先設**私有**模板、邀請最初幾個朋友以協作者身分使用，驗證過風控與觸發分散設計後再考慮公開。
- 每個副本的觸發分鐘數要求各自挑不同數字，避免集中流量尖峰。
- 設計版本通知機制（如 workflow 每次執行順帶檢查來源 repo 的版本標記檔），
  否則資安修正傳不到已分發的副本。
- 發布前再次確認無任何硬編碼個人資訊。

## 風險備忘

- **排程延遲**：已用「前一晚觸發＋target_date=明天」結構性避開，Phase 4 量測確認。
- **無聲失效**：私有 repo 不受 60 天停用規則影響；設定錯誤類失敗會寄信；
  仍需定期人工看 log。
- **供應鏈**：Action 釘 SHA（Phase 3）；未來若把引擎抽成共用套件，須釘版本雜湊。
- **InnoKnight 改版登入頁**：幾乎必然發生。發生時會以登入失敗（非 0）寄信告警；
  修復後若已進入 Phase 5，需靠版本通知機制傳給副本。
