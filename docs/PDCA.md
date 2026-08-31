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

### 可重現性初測（連續 3 次 dry-run）

為確認不是一次僥倖，緊接著連跑 3 次 dry-run：

| Run | 登入結果 | 端到端 |
|---|---|---|
| 33277306239 | 第 2 次嘗試成功（第 1 次被 reCAPTCHA 拒） | ✅ |
| 33277505663 | **第 1 次嘗試即成功** | ✅ |
| 33277550439 | 第 2 次嘗試成功（第 1 次被拒） | ✅ |

**3/3 全部成功，且都在 2 次嘗試內完成**（`max_login_attempts=3` 尚有餘裕）。
再次印證 reCAPTCHA 的間歇性（有時第 1 次就過、有時需重載一次），以及重載重試
確實把間歇性成功轉為穩定成功。這是小樣本、同一時段、同一 IP 段的初測，跨時段／跨多天
的正式穩定性量測仍列為待辦（見 Act）。

### 真實寫入驗證（此前唯一未測的路徑）

在此之前所有測試都是 **dry-run**——會登入、會算出「該建立哪筆預約」，但**從不實際送出
`schedule_set`**。因此「雲端能不能真的寫入 InnoKnight」在上線前仍是零驗證的盲點。

為了辨識（原自管主機不停、兩邊並行），把充電時段改成一個明顯不同的窗口——
**早上 07:00–10:00**（透過 GitHub Variables `INNOKNIGHT_START_TIME`/`END_TIME` 設定，
非離峰的 00:30–06:00），這樣使用者能一眼分辨哪筆預約是雲端版建立的。

| 步驟 | Run | 動作 | 結果 |
|---|---|---|---|
| 寫入 | 33278452411 | `apply=true`（真實 `--execute`） | `新增重點預約: 2026-08-31 07:00~10:00`，job 綠燈、無失敗訊息 |
| 確認 | 33278513179 | dry-run 回查（不寫入） | **`2026-08-31 已存在相同預約，結束流程。`** |

第二步是關鍵鐵證：一次獨立的 dry-run 從遠端 `read_balance` 查到了剛建立的預約，
`has_equivalent_schedule` 判為已存在而跳過。這同時證明了兩件事：(1) `schedule_set`
真的寫入成功；(2) **冪等性正確**——重複執行不會建立第二筆。

### 上線首夜的實測發現與調校（2026-08-30 → 08-31）

Phase 4 啟用後第一晚的自動排程執行（Run 33326712281），暴露了兩件手動測試看不到的事：

1. **`schedule` 自動觸發機制有效，但延遲近 4 小時**：cron 設 UTC 14:05，實際 **17:58**
   才觸發（延遲 3 小時 53 分）。這正是研究文件引用 GitHub #191400 警告的尖峰延遲，
   而且比預期更久。**但「前一晚觸發 + target_date=明天」的設計成功吸收了它**——
   UTC 17:58 = 台北 08-31 01:58，仍遠早於 07:00 窗口。若當初照原始「00:05 觸發」設計，
   這 4 小時延遲會直接造成「時段已過才建立」的失敗。設計決策 §4-1 得到實戰驗證。
2. **reCAPTCHA 連續 3 次全拒 → 當次失敗**：`Login failed after 3 attempts (last verdict:
   rejected)`，`success:false`。先前手動測試都在 ≤2 次內成功（可重現性 3/3），但自動執行
   首夜就撞上評分特別低的時段，`max_login_attempts=3` 不夠。這是 reCAPTCHA 間歇性的
   第一次「連續失敗」實證。當晚充電未受影響（08-31 07:00–10:00 已於 08-30 手動建立），
   且 job 紅燈正確觸發了失敗通知信。

**調校**：`max_login_attempts` 3 → **5**，並在重試間加**遞增等待**（5/7/9/11 秒，給
reCAPTCHA 評分恢復時間、也淡化快速連登的機器人特徵）。機率上（單次成功率 p≈0.4 估）
把「至少一次成功」從 3 次的 ~78% 提到 5 次的 ~95%+。不再往上加，是為了避免過多連續
失敗登入加重 InnoKnight 對此帳號/IP 的風控。

調校後的驗證 dry-run（Run 33402017656）第 1 次登入即通過、正確算出要建立
09-01 07:00–10:00。今晚（08-31 22:05）的自動排程會以更高的成功率實際建立 09-01 預約。

---

## Act

### 定案設計（已寫入程式與 design.md §4）

1. **headful（`headless:false`）+ Xvfb**——headful 真實指紋是通過 reCAPTCHA 的前提。
2. **`--disable-gpu`（+ `--no-first-run --no-default-browser-check`）**——runner 無 GPU，
   headful 少了它會卡 CDP 逾時。
3. **直接從登入 API 回應取得 session**，不依賴前端寫 cookie 的時機；保留 cookie 後備。
4. **reCAPTCHA 間歇性 → 重載頁面重試最多 5 次**（首夜連續 3 拒後從 3 調高，見上），
   遞增等待每次取得新的 reCAPTCHA 評分。

### 待辦（後續量測與上線）

- [x] **真實寫入驗證**：apply 一次 + dry-run 回查確認「已存在」，證明 `schedule_set` 成功且冪等（見上）。
- [x] 清理一次性診斷 workflow `diagnostics.yml`（結論已固化於本文件）。
- [x] 排程觸發器延遲首次實測：首夜延遲近 4 小時，被「前一晚觸發」吸收（見上）。持續觀察分佈。
- [~] **穩定性量測（進行中）**：首夜自動執行即遇 reCAPTCHA 連續 3 拒 → 已調高重試上限至 5。
      續觀察多天，統計平均重試次數與「5 次全被拒」的殘餘失敗率，評估是否需要進一步對策
      （如錯開觸發分鐘、或最終走付費固定出口 IP）。
- [ ] **Phase 4 並行上線（進行中）**：解開 `schedule` cron 讓雲端版每晚自動執行。
      **原自管主機不停、兩邊並行**：雲端版用 07:00–10:00、自管主機維持 00:30–06:00，
      以時段區分、供人工核對。注意 `cleanup_candidates` 會清理**所有**過期的一次性預約
      （不分哪邊建的，保留最近一筆）——這是同一 InnoKnight 帳號下的既有行為，兩邊並行時要知道。

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
