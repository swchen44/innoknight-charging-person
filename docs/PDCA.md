# PDCA：Use Case 1 雲端化的驗證與除錯紀錄

> 本文件用 PDCA（Plan–Do–Check–Act）循環，完整記錄「把每日充電預約從自管主機
> 搬到 GitHub Actions 免費環境」的 go/no-go 驗證過程、遇到的每一個障礙、實測證據、
> 以及兩個**中途被實驗推翻的錯誤判斷**。設計見 [design.md](design.md)，計劃見 [plan.md](plan.md)。
>
> **結論（2026-08-30）：GO。** headful Chrome + `--disable-gpu` + 直接從登入 API 回應
> 取得 session + reCAPTCHA 間歇性拒絕時重載重試，已在 GitHub Actions 免費環境**端到端跑通**
> 一次完整的 dry-run（登入 → 查裝置狀態 → 算出明日預約 → 未實際寫入）。

---

## Plan

**目標**：功能與原專案 `innoknight-charging-scheduler` 完全一致，但改由 GitHub Actions
免費 runner 觸發與執行，每月成本 NT$ 0。

**待驗證的头号未知數**（design.md §6，同時是未來多租戶版的可行性守門員）：

> GitHub Actions runner 的共用 IP range 是否會被 InnoKnight 的登入風控或 reCAPTCHA
> 判定為異常。若被擋，`INNOKNIGHT_RECAPTCHA_TOKEN` 不是有效退路（人工取得、幾分鐘過期，
> 無法無人值守），代表這個免費架構不成立，要改走付費固定出口 IP／代理。

**驗證方式**：以 `workflow_dispatch` 手動觸發 dry-run（只讀不寫），觀察能否登入成功。

---

## Do

Phase 1 移植：沿用核心模組（`automation` / `client` / `crypto` / `scheduler` /
`browser_session`），清除硬編碼個人資訊，新增 `target_date=明天` 支援、`daily-schedule.yml`、
單元＋整合測試。設定三個 Secrets（帳號、密碼、裝置名稱）後開始 dry-run 驗證。

---

## Check（實測證據）

驗證過程遠比預期曲折，經歷 **8 輪** dry-run。每一輪都用「只印安全資訊」的頁面／API
診斷（欄位值只印長度不印內容、登入回應只印 `success`/`message`），逐步逼近根因。

### 證據總表

| # | Run | 變更 | 結果 | 關鍵證據 |
|---|---|---|---|---|
| 1 | 33244710903 | 初版（headless:false + Xvfb，沿用原設計） | ❌ | `CDP port 9224 did not become ready: Connection refused` |
| 2 | 33244867386 | 附上 Chrome 死因診斷 | ❌ | `chrome exit_code=-15`，只有一堆 `dbus/bus.cc ERROR`（誤導） |
| 3 | 33245169332 | 冒煙測試（單一 headful 變體） | ❌ | headful `no DevTools line`；**誤判**成 headful 不可行 |
| 4 | 33245342649 | 四瀏覽器變體冒煙測試 | ⚠️ | 唯 `--headless=new` 起 CDP；**得出錯誤結論**「Chrome 移除 headful CDP」 |
| 5 | 33256696831 | 改用 `--headless=new` | ❌ | 登入逾時等不到 cookie |
| 6 | 33257486256 | 摘除 headless 指紋（改 UA、AutomationControlled） | ❌ | 仍逾時；`hasCaptcha:true` |
| 7 | 33257642819 | 抓登入 API 回應 | ❌→定位 | `get_end_user_token → {"success": false}`＝**reCAPTCHA 擋 headless** |
| — | 33276796501 | **四變體 headful 完整 log 診斷** | 🔑 | `headful + --disable-gpu` **CDP OK 1s**——推翻 #4 的錯誤結論 |
| 8a | 33276968008 | 改回 headful + `--disable-gpu` | ⚠️ | `get_end_user_token → {"success": true, "OK"}`＝**reCAPTCHA 放行 headful！** |
| 8b | 33277105205 | 加儲存診斷 | ❌ | 同址再測卻 `success:false`＝**reCAPTCHA 間歇性**；token 走 cookie 非 localStorage |
| ✅ | 33277306239 | response-based session + 重載重試 | **✅ 成功** | 見下方「最終成功」 |

### 障礙一：CDP port 起不來（誤入 dbus 歧途）

前幾輪 Chrome 啟動後 `Connection refused`，log 全是 `dbus/bus.cc:405 Failed to connect
to the bus`。這些 dbus 錯誤極具誤導性，看起來像是缺少 D-Bus session。**但它們只是 noise**。

### 障礙二：中途誤判「Chrome 151+ 移除了 headful 的 CDP port」（已被推翻）

Run #3/#4 的冒煙測試中，headful 變體全數 `no DevTools line`，只有 `--headless=new` 成功。
當時據此下了結論：現代 Chrome 有頭模式不再支援 `--remote-debugging-port`，headless 是唯一路。
**這個結論是錯的**，而且它逼著我們走進「必須用 headless」的死路。

推翻它的是 **Run 33276796501 的四變體對照實驗**（完整 log）：

| 變體 | 旗標 | 結果 |
|---|---|---|
| A `headful-xvfb` | 純 headful | ❌ CDP FAILED |
| **B `headful-xvfb-lean`** | headful + `--disable-gpu --no-first-run --no-default-browser-check` | ✅ **CDP OK 1s** |
| C `headful-dbus` | headful + `dbus-run-session`（無 `--disable-gpu`） | ❌ FAILED（證明 dbus 不是主因） |
| D `headless-new` | headless | ✅ OK 1s |

**真正的根因**：GitHub runner 沒有 GPU，headful Chrome 預設會嘗試初始化 GPU 而**卡住直到
CDP 逾時**；加上 `--disable-gpu` 就 1 秒起 CDP。變體 C 補了 dbus 卻沒加 `--disable-gpu`，
仍然失敗——一刀證明 dbus 錯誤是 noise、GPU 才是關鍵。先前冒煙測試的 headful 變體正好都
沒帶 `--disable-gpu`，才造成誤判。

> **教訓**：不要用「一個變體失敗」推論「整類做法不可行」。控制單一變因的對照實驗，
> 才能把「純 headful 失敗」與「headful 這條路不通」區分開。

### 障礙三：reCAPTCHA 擋 headless，但**間歇性放行 headful**

同一個登入流程，唯一差別是 headless vs headful：

- **headless**（Run #7）：`get_end_user_token → http=200 {"success": false, "message": ""}`
- **headful**（Run 8a）：`get_end_user_token → http=200 {"success": true, "message": "OK"}`

這是决定性證據：InnoKnight 的 reCAPTCHA 對 headless 指紋 + datacenter IP 直接拒絕，
但對 **headful 真實指紋放行**。這也回頭驗證了原專案刻意用 `headless:false` 的用意。

但 headful 並非每次都過——Run 8b 同址再測又是 `success:false`。**reCAPTCHA 對 GitHub
datacenter IP 是間歇性放行**（v3 行為評分落在邊界，datacenter IP 信譽本就偏低）。

### 障礙四：登入成功卻抓不到 session

Run 8a `success:true` 卻仍逾時。儲存診斷（Run 8b）顯示：`cookieKeys: ["language",
"email", "ipsd"]`、`localStorage: {"_grecaptcha": 130}`——token 走 cookie（原專案讀的
`user` cookie），但前端在 CDP 環境下寫入 `user` cookie 的時機不可靠。

**解法**：不再等前端寫 cookie，改**直接從 `get_end_user_token` 的回應 body 解析
`uuid`/`token` 組出 session**（與 `client.login()` 同一套欄位），並保留讀 `user` cookie 作後備。

### 最終成功（Run 33277306239，dry-run，52 秒）

```
login attempt 1/3 rejected; reloading and retrying
login succeeded on attempt 2/3
Browser login OK: user_id=zWs8***
目標充電樁狀態: 充電樁已就緒
新增重點預約: *** 2026-08-31 00:30~06:00
Dry-run only; no schedule was changed.
```

第 1 次 reCAPTCHA 拒絕、**重載頁面後第 2 次通過**，取得 session、查得裝置狀態、
算出隔日（08-31）預約、dry-run 未實際寫入。整條鏈端到端打通。單次 job 52 秒
（核心步驟 21 秒，含一次重試）。

---

## Act

### 定案設計（已寫入程式與 design.md §4）

1. **headful（`headless:false`）+ Xvfb**——headful 真實指紋是通過 reCAPTCHA 的前提。
2. **`--disable-gpu`（+ `--no-first-run --no-default-browser-check`）**——runner 無 GPU，
   headful 少了它會卡 CDP 逾時。
3. **直接從登入 API 回應取得 session**，不依賴前端寫 cookie 的時機；保留 cookie 後備。
4. **reCAPTCHA 間歇性 → 重載頁面重試最多 3 次**，每次取得新的 reCAPTCHA 評分。

### 待辦（後續量測與上線）

- [ ] **穩定性量測**：連續多天 dry-run，統計「平均需要幾次重試才成功」「3 次全被拒的機率」。
      這決定 `max_login_attempts` 要不要調高，以及這個免費方案的實際可靠度。
- [ ] 量測不同台北時段的排程觸發器延遲，確認前一晚 22:05 觸發的餘裕足夠（design.md §6）。
- [ ] 穩定後進入 [plan.md](plan.md) Phase 4：解開 `schedule` cron、切 `--apply`、舊主機退場。
- [ ] 清理：`.github/workflows/diagnostics.yml` 是一次性診斷用，結論已固化於本文件，可刪除。

### 對未來多租戶版（情境二）的意義

头号 go/no-go 得到正面答案：**GitHub Actions IP 可以登入 InnoKnight**（前提是 headful）。
但要注意兩點警訊：(1) reCAPTCHA 間歇性放行——多租戶批次登入多個帳號時，失敗率會被放大，
且所有帳號共用同一 IP 是「共同命運失效」；(2) 重試會增加登入次數，需與風控之間拿捏
（情境二已設計「同一天最多重試 2 次」）。

---

## 參考資料（網路查證）

- [Chrome 136+：headful 用預設 profile 無法被 CDP 驅動，需非預設 `--user-data-dir`](https://github.com/browser-use/browser-use/issues/1520)
- [Chrome `--remote-debugging-port` headless 可用、regular 模式失敗的原因與 Chrome 136 變更](https://www.codegenes.net/blog/debugging-a-chrome-instance-with-remote-debugging-port-flag/)
- [Docker/Xvfb 下 headful Chrome + remote debugging 的 dbus 干擾討論](https://forums.docker.com/t/docker-chrome-headfull-with-xvfb-and-remote-debugging-active-dbus/108202)
- [reCAPTCHA 對 datacenter IP 信譽偏低、headless CDP 指紋易被偵測；static ISP IP 優於 rotating datacenter](https://www.browserless.io/blog/bot-detection)
- [Bot / Headless Chrome 偵測測試集](https://bot.incolumitas.com/)
