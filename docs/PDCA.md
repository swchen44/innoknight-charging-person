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

### 累積穩定性數據（08-30 → 09-02，共 4 個排程夜，尚未滿一週）

| 排程夜 | 觸發（UTC，設定 14:05） | 延遲 | 登入結果 | job 耗時 | 判斷結果 |
|---|---|---|---|---|---|
| 08-30（調校前） | 17:58:07 | 3h53m | ❌ 3/3 全拒 → 失敗 | 29s | （失敗，未建立） |
| 08-31（調校後） | 19:52:19 | 5h47m | ✅ 第 1 次即過 | 13s | 建立 09-02 |
| 09-01（調校後） | 17:41:19 | 3h36m | ✅ 第 1 次即過 | 10s | 建立 09-03 |
| 09-02（調校後） | 17:41:59 | 3h36m | ✅ 第 1 次即過 | 14s | 跳過（充電中） |

**觀察**：

1. **調校後登入 3/3 全部第 1 次即成功**（0 次重試）——與調校前那次的「3/3 全拒」
   形成強烈對比，樣本雖小但方向明確。真正的可靠度需要更多夜晚的樣本，特別是要
   再撞見一次 reCAPTCHA 判定嚴格的時段，才能驗證新的 5 次重試上限是否真的夠用，
   還是恰好至今運氣好。
2. **觸發延遲全部落在 3.5–5.8 小時**，比研究文件原引用的「15 分鐘至 2 小時」
   （GitHub #191400）更久，也比首夜的「近 4 小時」更長（最長到 5h47m）。四次全部
   被「前一晚觸發、target_date=明天」吸收，沒有一次逼近 07:00 窗口。這進一步印證
   §4-1 的設計餘裕是足夠的，但也代表這個延遲量級可能是常態而非特例。
3. **09-02 那次是「充電樁狀態非就緒」正常跳過**（非失敗）——车當下在充電中，
   依既有邏輯不建立隔日預約，故 09-04 未建立雲端版辨識預約。此為原專案沿用邏輯，
   非本次雲端化引入的問題；辨識期自管主機的離峰時段仍是後備。

**尚不足以下結論**：4 個樣本無法排除「調校後恰好幸運」的可能，需持續觀察到滿一週
（7 個排程夜）以上，才能算出有統計意義的重試分佈與殘餘失敗率。

### 補洞參數驗證（workflow_dispatch 新增 target_offset_days/start_time/end_time）

背景：09-01 那次調校前的失敗漏建了當天的辨識預約，事後想補時發現 workflow 只能
建「明天」、時段鎖 Variables，沒有臨時指定日期與時段的手動入口，只能等下一輪
自動排程覆蓋，補不回那一天。為此在 workflow_dispatch 加了三個可留空的參數。

驗證（Run 33688299687，dry-run，`target_offset_days=0 start_time=09:00 end_time=11:00`）：

```
Browser login OK: user_id=zWs8***
目標充電樁狀態: 充電樁已就緒
新增重點預約: *** 2026-09-03 09:00~11:00
Dry-run only; no schedule was changed.
```

三個覆寫全部精準命中：`target_offset_days=0` 正確算出「今天」（09-03，而非預設的
明天）、`start_time`/`end_time` 正確覆寫 Variables 的 07:00–10:00。且確認 `schedule`
觸發不受影響（`inputs.*` 在 schedule 事件下為 `null`，`||` 安全落回 Variables/預設，
GitHub 官方行為，非本專案臆測）。

### 辨識期結束：改回離峰時段，並把觸發緩衝拉到 4 小時（2026-09-03）

使用者確認離峰時段為 **00:30–06:00**（訊息裡曾誤打成「3點到6點」，經
[AskUserQuestion] 確認為打字誤植），並指出既有 4 天延遲數據顯示緩衝不足，
要求「workflow 必須在預設時間的 4 小時以前」。兩項變更：

1. **Variables 改回 00:30/06:00**，結束辨識期。驗證（Run 33690131272，dry-run）：
   ```
   新增重點預約: *** 2026-09-04 00:30~06:00
   ```
   正確算出真正的離峰時段，辨識期正式結束。
2. **cron 觸發時間 22:05 → 20:30**（UTC `5 14 * * *` → `30 12 * * *`），緩衝從
   2h25m 拉高到 **4 小時**。依據：累積穩定性數據顯示實測延遲落在 3.5–5.8 小時，
   已超過研究文件原引用的「15 分鐘至 2 小時」（GitHub #191400），22:05 的緩衝
   （距 00:30 僅 2h25m）不再算充分安全邊際。

   **權衡揭露（未詢問使用者，直接記錄取捨）**：觸發時間越早，車輛在觸發當下
   還沒返家插上充電的機率越高，可能誤觸 `device_not_ready` 而跳過建立。20:30
   仍是晚間、多數使用情境車輛應已到家，但比原本的 22:05 承擔略高的「太早」風險。
   若之後常在 20:30 撞到 `device_not_ready`，需要重新評估這個時間點。

   **`target_date` 自我修正機制**（消除對「絕對不能延遲過午夜」的疑慮）：
   `target_date` 不是靜態綁在 cron 上算好的值，而是**每次執行當下**用 Taipei
   實際時間重新算「明天」。因此即使延遲把執行時間推過午夜，也不會建立錯誤日期
   的預約——只是會建立「更晚一天」的預約，由連續每晚的排程鏈自然接續補上，
   不會產生永久缺口（除非同一天恰好也發生登入失敗或裝置未就緒等獨立原因）。
   這也是為何即便觀察到 5h47m 延遲，先前的 dry-run/真實執行從未建立錯日期的原因。

### 舊系統退場：原自管主機已停用（2026-09-03，同日）

使用者告知原自管主機的 crontab **已停止**。這比 plan.md Phase 4 原訂的完成條件
（連續 7 天穩定性數據 + 離峰時段運行足夠天數）更早——是使用者的決定，本文件如實
記錄，不代表原計劃的建議順序被推翻。

**現況評估（誠實記錄，不過度樂觀）**：

- 雲端版目前累積的健康樣本：4 個排程夜（08-30 失敗、08-31/09-01/09-02 成功），
  調校後 3/3 第一次登入即過。這個樣本是在**辨識期（07:00–10:00）、22:05 觸發**
  的組合下取得的。
- **主機停用的同時，觸發時間（→20:30）與時段（→00:30–06:00）也剛好同一天變更**——
  三個變數一起換，代表舊有的 4 夜健康樣本**不能直接證明新組合同樣可靠**，新組合
  的第一次真正自動排程執行（今晚台北 20:30 附近，實際視延遲而定）才是它的首次實測。
  這與 08-30 首夜（見上方「上線首夜的實測發現與調校」）是類似情境：手動測試順利
  不保證自動排程首次執行也順利，先前就在類似情境下踩過一次 reCAPTCHA 連續拒絕。
- **零後備**：與辨識期或首夜失敗當時不同，現在若失敗，沒有自管主機能墊底。

**待辦**：密切關注今晚起的自動排程結果，若失敗立即用手動補洞救援（見 README），
並把結果記錄回本文件與 design.md §6。

### 加第二輪排程備援，並查證/實測 concurrency 的隱藏陷阱（2026-09-03，同日）

使用者提議：既然沒有後備系統了，加一個「第二輪」排程，間隔半小時，第二輪本質上是
檢查是否已有排程——這正是既有 `has_equivalent_schedule` 冪等判斷已經在做的事，
不需要新寫檢查邏輯，只需要讓 workflow 再被觸發一次。

**查證發現一個原本會讓兩輪備援失效的坑**：workflow 既有的
`concurrency: { group: daily-schedule, cancel-in-progress: false }` 只保護
「**已經在跑**」的 job；官方文件明確指出，預設 `queue: single` 下，若一個
run 還在**排隊、尚未開始跑**時有新的同 group run 觸發，GitHub **會直接取消
排隊中的那個**、讓新的取代它。以本專案實測過的 3.5–5.8 小時排程延遲，
第一輪很可能長時間停留在「已觸發但還沒真正開始跑」的排隊狀態，這時第二輪
一觸發，第一輪就會被取消——兩輪備援可能因此互相取消，某些晚上反而只跑成一次，
完全違背加第二輪的初衷。解法是 2026-05 才發布的 `queue: max`（讓同 group 的
run 改成 FIFO 真正排隊，不互相取消）。

**實測驗證**（而非只信任文件說法——本專案已有教訓，見「障礙二」）：手動連續
dispatch 兩次（間隔 3 秒），確認第二次觸發時第一次仍在跑（`in_progress`）：

| Run | 觸發 | 開始 | 結束 | 結果 |
|---|---|---|---|---|
| A（33691451928） | 22:39:38 | 22:39:38 | 22:40:17 | success |
| B（33691455730） | 22:39:41（A 仍在跑時觸發） | 22:40:17（等 A 結束才開始） | 22:41:20 | success |

B 沒有被取消，而是排隊等到 A 結束才開始跑，兩者都是 success。`queue: max` 的
FIFO 排隊行為確認有效。

**實作**：`daily-schedule.yml` 加第二個 `schedule` cron（UTC `0 13 * * *` = 台北
21:00，第一輪 30 分鐘後，緩衝縮到 3.5 小時——比第一輪的 4 小時政策窄，是「多一次
機會」與「維持緩衝」之間的取捨，之後若常在 21:00 撞見裝置未就緒可再調整）；
`concurrency` 加 `queue: max`。不需要任何應用程式碼變更。

---

## Act

### 定案設計（已寫入程式與 design.md §4）

1. **headful（`headless:false`）+ Xvfb**——headful 真實指紋是通過 reCAPTCHA 的前提。
2. **`--disable-gpu`（+ `--no-first-run --no-default-browser-check`）**——runner 無 GPU，
   headful 少了它會卡 CDP 逾時。
3. **直接從登入 API 回應取得 session**，不依賴前端寫 cookie 的時機；保留 cookie 後備。
4. **reCAPTCHA 間歇性 → 重載頁面重試最多 5 次**（首夜連續 3 拒後從 3 調高，見上），
   遞增等待每次取得新的 reCAPTCHA 評分。
5. **觸發時間 20:30、緩衝 4 小時**（2026-09-03 從 22:05 調整，見上）——實測延遲
   常態落在 3.5–5.8 小時，原 2h25m 緩衝不足。`target_date` 每次執行當下重新計算，
   延遲推過午夜也不會建立錯日期預約，只是自然順延一天由後續排程接續（見上）。
6. **第二輪排程備援（21:00）+ `concurrency.queue: max`**——原自管主機停用後
   雲端版無後備，加第二輪給 reCAPTCHA/裝置就緒狀態多一次機會；`queue: max`
   是必要條件，否則預設的 `queue: single` 會讓兩輪在特定延遲情境下互相取消
   （已實測驗證，見上）。第二輪不需要新的檢查邏輯——`has_equivalent_schedule`
   本身就是冪等檢查，第一輪已成功時第二輪直接判定已存在並跳過。

### 待辦（後續量測與上線）

- [x] **真實寫入驗證**：apply 一次 + dry-run 回查確認「已存在」，證明 `schedule_set` 成功且冪等（見上）。
- [x] 清理一次性診斷 workflow `diagnostics.yml`（結論已固化於本文件）。
- [x] 排程觸發器延遲首次實測：首夜延遲近 4 小時，被「前一晚觸發」吸收（見上）。
      續量測 4 夜後發現常態延遲 3.5–5.8 小時，已據此把緩衝從 2h25m 拉高到 4 小時。
- [~] **穩定性量測（進行中，4/7+ 夜）**：首夜連續 3 拒 → 調高重試上限至 5 後，
      連續 3 夜（08-31/09-01/09-02）皆第 1 次即成功（見上表）。樣本仍太小，
      續觀察到滿一週以上，統計平均重試次數與「5 次全被拒」的殘餘失敗率，
      評估是否需要進一步對策（如錯開觸發分鐘、或最終走付費固定出口 IP）。
      **辨識期已於 09-03 結束**，續觀察改用離峰時段 00:30–06:00 後的表現。
- [x] **補洞手動參數**：workflow_dispatch 加 `target_offset_days`/`start_time`/`end_time`，
      dry-run 驗證三者皆精準覆寫且不影響 schedule 觸發（見上）。
- [x] **Phase 4 並行上線**：`schedule` cron 已啟用，雲端版每晚自動執行。
      辨識期（07:00–10:00 區分雲端版與自管主機）已於 09-03 結束，兩邊現在都是
      00:30–06:00——**已無法再靠時段肉眼區分哪邊建立的**，只能靠 Actions run log
      核對雲端版是否正常運作。注意 `cleanup_candidates` 會清理**所有**過期的一次性
      預約（不分哪邊建的，保留最近一筆），且 `has_equivalent_schedule` 的冪等判斷
      現在對兩邊都生效——若某晚自管主機先建立、雲端版稍後執行會判定「已存在」而
      跳過，這是預期行為，不是雲端版失效。
- [x] **舊系統退場**：原自管主機已於 2026-09-03 停用（見上「舊系統退場」小節）。
      早於原訂完成條件，雲端版現在無後備，是目前最大的營運風險。
- [x] **第二輪排程備援**：加 21:00 第二輪 cron + `concurrency.queue: max`；
      手動連續 dispatch 實測確認兩輪確實依序排隊執行、不互相取消（見上）。
- [ ] **首次真正自動排程驗證（新組合：20:30+21:00 雙輪觸發 + 離峰時段 + 零後備）**：
      新的觸發時間、時段、雙輪機制組合尚未被真實 `schedule` 事件驗證過（此前的
      queue:max 驗證用的是 workflow_dispatch，不是真正的 schedule 觸發）。今晚起
      的自動執行是首次真槍實彈測試，需要人工確認結果並記錄回本文件——特別留意
      兩輪是否都準時各自觸發（GitHub 排程器本身也可能漏跳某一輪，需觀察）。

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
