# 錦標賽環境與 PKO（T1–T3）

本文件是錦標賽環境的設計規格，在開始實作前定案。目前進度見 [../status.md](../status.md)。

## 1. 目標與範圍

### 1.1 為什麼需要錦標賽環境

最終目標是訓練偏 Natural8 賞金獵人／PKO 錦標賽的策略。

目前的訓練與評估環境是：兩人（Heads-Up）、每手獨立、每手開始時雙方籌碼重置為 100BB、獎勵是這一手贏或輸多少 BB。這是現金桌（cash game）的設定。即使模型在這個環境穩定贏過所有 Rule Bot，也不代表它學到錦標賽需要的策略，因為它完全不需要考慮：

- 存活：輸光就出局，不能再買入。
- 短籌碼：籌碼只剩 10BB 上下時，策略幾乎只剩全下或蓋牌。
- 盲注上升：籌碼相對盲注會隨時間縮小。
- 名次獎金：獎金依名次分配，籌碼價值不是線性的（ICM）。
- Bounty：淘汰別人可以直接拿到獎金（PKO）。

### 1.2 原則

- **保留現有 HU cash 環境**，當作「基礎牌技」的測試與回歸基準：HU bb/100 benchmark 不刪除，之後任何輸入或演算法修改都要確認它沒有變差。
- **錦標賽環境疊在既有的 6 人撲克引擎上**：`poker/table.py` 的 `TableHand` 已支援每個座位不同籌碼、任意按鈕位置與 2–6 人，side pot 與多人攤牌都已驗證。錦標賽只負責「手與手之間」的規則，不重寫單手牌局。
- **一場完整的錦標賽是一個 episode**，最終結果以整場的金額為主，而不是每手的 BB 盈虧。
- **所有規則可設定，不寫死在引擎中**：起始籌碼、盲注表、ante、payout、名次判定、bounty 比例都是設定的一部分；常見組合以 preset 提供。
- **第一版只做單桌 6 人**，不做多桌、併桌、late registration。

## 2. 階段與依賴

```text
P1-a（v2 輸入）→ T1（錦標賽環境）─┬→ P1-b（HU cash 長時間訓練）
                                 └→ T2（PKO）→ T3（錦標賽上的學習）
```

| 階段 | 內容 | 依賴 |
|---|---|---|
| P1-a | `poker-hunlhe-vector-v2` 輸入：牌力特徵、2–6 人通用、預留錦標賽欄位 | — |
| T1 | 6 人錦標賽環境、可設定 payout、名次判定、Rule Bot 與 `HeuristicPushFoldBot`、tournament benchmark、平台整合 | P1-a（輸入欄位） |
| P1-b | HU cash 長時間訓練與調參，直到穩定贏 4 種 Rule Bot | P1-a |
| T2 | PKO bounty 規則與 bounty 指標 | T1 |
| T3 | 錦標賽上的學習（6 人輸入、獎勵設計、演算法選擇） | T2；可參考 P1-b 的結果，但不以它為前提 |
| 之後 | 多桌、併桌（table balancing）、late registration 等完整 MTT 規則 | T3 |

- T1 完成後，P1-b 與 T2/T3 **平行推進**，P1-b 不是錦標賽工作的硬性前提。
- P1-a 先於 T1，是為了讓 v2 一開始就包含錦標賽需要的欄位，避免之後再改輸入格式。

## 3. v2 輸入（P1-a）

`poker-hunlhe-vector-v2`，同時接受兩人與 2–6 人的 Observation；v1 保留，checkpoint 已記錄輸入格式版本，舊模型照常載入。

### 3.1 牌力特徵

- 翻牌前：兩張手牌的大小、是否同花、是否對子、手牌類別。
- 翻牌後：目前成牌類型與最大牌、同花聽牌、順子聽牌、公牌結構（公牌成對、三張以上同花）。
- 只使用自己看得到的資訊，延續既有的資訊隔離測試。

### 3.2 自己與對手

- 對手使用**固定 5 個槽位**，依相對於自己的座位順序排列。每個槽位：
  - 是否有人、籌碼、本輪下注、是否蓋牌、是否全下、距離按鈕幾個位置、bounty（T2 之前為 0）。
- 兩人牌局只使用一個對手槽位，其餘補 0。

### 3.3 數值單位

- 籌碼、彩池、下注、跟注金額**一律除以目前的 BB**。
- 因為除以 BB 後 BB 永遠是 1，另外提供盲注本身的大小：
  - 實際 SB、BB、ante 各自除以起始籌碼（看得出盲注相對起始籌碼的成長）。
  - ante 除以 BB。
  - 盲注等級編號、距離下一級還有幾手。

### 3.4 錦標賽欄位（cash 時補 0）

- 剩餘人數。
- **Payout 向量**：固定長度 6，每一項是該名次獎金占獎池的比例，例如 `[0.65, 0.35, 0, 0, 0, 0]`、`[0.5, 0.3, 0.2, 0, 0, 0]`。不壓縮成單一「形狀」數值。
- 自己的 bounty（T2 之前為 0）。

### 3.5 驗收

- 監督式 equity 測試的 R² 從 v1 的 0.46 明顯提升。
- 資訊隔離測試：任何欄位都不含別人的手牌或未發的牌。
- 兩人 NFSP 能改用 v2 訓練。

## 4. 錦標賽環境（T1）

### 4.1 設定（TournamentConfig）

一個可存成 JSON 的不可變物件，引擎內沒有寫死的數值：

| 欄位 | 說明 |
|---|---|
| `players` | 起始人數，2–6 |
| `starting_stack_bb` | 起始籌碼，以第一級的 BB 為單位；**必須可設定** |
| `blind_levels` | 盲注表：每一級的 SB、BB、ante，以及維持幾手 |
| `ante_type` | 無、或 BB ante（由大盲位代付） |
| `payout` | 各名次的獎金比例，總和為 1 |
| `buy_in` | 每人報名費（以金額計），用來計算 ROI |
| `placement_resolver` | 同一手多人淘汰時的名次判定方式（4.4） |
| `bounty` | PKO 設定；T1 為關閉（T2） |

內建 preset：

| 起始籌碼 | payout | 名稱範例 |
|---|---|---|
| 10BB | 65/35 | `6max-sng-10bb` |
| 10BB | 50/30/20 | `6max-sng-10bb-top3` |
| 25BB | 兩組各一 | `6max-sng-25bb`、`6max-sng-25bb-top3` |
| 50BB | 兩組各一 | `6max-sng-50bb`、`6max-sng-50bb-top3` |

10BB 只是其中一個 preset，引擎本身不假設任何籌碼深度。

### 4.2 流程

- 每一手用現有的 `TableHand` 打，只傳入還在場的玩家。
- **籌碼跨手保留**；每手結束後籌碼為 0 的玩家淘汰。
- 淘汰後剩下的座位重新編號，人數依序從 6 降到 1。
- **按鈕**移到下一個還在場的玩家（第一版為簡化規則，不做 dead button）。
- **盲注依手數升級**：每一級維持設定的手數，結果完全可以重現，與決策速度無關。
- 剩一人時錦標賽結束。
- `TableHand` 新增選填的 ante 參數，不傳時行為與現在完全相同。
- 同一個錦標賽 seed 產生每一手的牌，重播結果必須完全相同。

### 4.3 每場結果

- **最終結果（monetary result）** = 名次獎金 + 收到的 bounty − buy-in。
- T1 沒有 bounty，最終結果只有名次獎金 − buy-in。
- 每場的完整結果包含：每個玩家的名次、名次獎金、bounty（T2 起）、最終結果、每手的籌碼變化與淘汰紀錄。

### 4.4 同一手多人淘汰的名次

做成**可替換的名次判定器**（`placement_resolver`），T1 就實作預設版本：

- 依**這一手開始前**的籌碼排名，籌碼較多的人名次較好。
- 開始前籌碼相同時，這幾個人**平分**他們所佔名次的獎金總和。例如兩人並列第 3、4 名，各得 (p3 + p4) / 2。

### 4.5 ICM telemetry

- 每手開始與結束時，以 Malmuth-Harville 方法計算每個玩家的 ICM 權益，記錄在結果中。
- **只作為 telemetry**（分析與畫圖用）：
  - 不定義成訓練獎勵。
  - 不保證逐手加總等於最終結果。
- 是否把它用於 potential-based reward shaping，在 T3 決定。
- T2 的 bounty 權益**另外記錄一欄**，不併入 ICM 權益。

### 4.6 對手

- 現有 4 種 Rule Bot（random、call、tight、aggro）。
- **`HeuristicPushFoldBot`**：依自己籌碼的 BB 數、位置、手牌強度，決定全下或蓋牌。
  - 這是規則近似，**不是 Nash 均衡**，因此不使用 Nash 命名。
  - 它在 25BB、50BB 時仍然只會全下或蓋牌，因此在深籌碼只作參考對手，不代表合理策略。

### 4.7 Tournament benchmark

| 指標 | 定義 |
|---|---|
| Prize EV | 平均名次獎金 |
| Bounty EV | 平均收到的 bounty（T2 起才有值） |
| Total EV | Prize EV + Bounty EV − buy-in |
| ROI | Total EV ÷ buy-in |
| 平均名次 | — |
| ITM% | 拿到獎金的比例 |
| 各名次比例 | 每個名次各出現幾次 |

- Prize EV 與 Bounty EV 分開記錄，才能判斷模型是靠名次還是靠 bounty 獲利。
- 全部附 95% 信賴區間。
- **減少運氣**：同一組 seed 讓受測者輪流坐每個座位，與現有 `evaluate_table` 的做法相同。
- **至少測兩組 payout**（65/35 與 50/30/20），避免模型過度擬合單一 payout 結構。10BB 的兩組為必測；25BB、50BB 可以跑，但不是必要。
- 驗收所需的場數（T1 實測，`HeuristicPushFoldBot` 對混合 Rule Bot、10 BB、65/35、輪流座位）：ROI 95% 信賴區間半寬 ±10%、±5%、±2% 約需 1,000、3,900、24,000 場。內建 preset 為 3,000 場（約 ±5%）。模型與對手不同時變異也會不同，正式驗收前以同樣方法重新量測。

### 4.8 平台整合

- 新增 Job 類型 `TournamentJobPayload`；proto 只新增，舊版本不受影響。
- 新增 4.1 的 preset，可在 `tableforge ui` 建立。
- UI 的實驗明細顯示 4.7 的指標。

### 4.9 驗收

- 性質測試（Hypothesis）：
  - 籌碼守恆（每手與整場）。
  - 獎金總額等於獎池。
  - 名次唯一，或依 4.4 正確並列。
  - 一定剛好結束在一個贏家。
  - 同 seed 重播結果完全相同。
  - 盲注依手數正確升級。
- 10 萬場 Rule Bot 錦標賽 soak，零錯誤。
- UI 截圖檢查。

## 5. PKO（T2）

採常見 PKO 規則作為預設，所有比例都可設定：

- 每人的 buy-in 一半進獎池、一半成為自己的起始 bounty。
- 淘汰別人時，對方 bounty 的一半立刻拿到現金，另一半加到自己的 bounty 上。
- 只有籌碼足以蓋過被淘汰者的玩家才能收 bounty；一手中有多人都蓋過時，依各個 pot 的贏家平分。
- 冠軍另外拿回自己頭上的 bounty。
- Observation 的 bounty 欄位從 T2 開始有值。
- Bounty 權益作為 telemetry 另外記錄一欄，不併入 ICM。

驗收：bounty 守恆（收到的 bounty 總和 + 剩餘 bounty = 所有人投入的 bounty）的性質測試、Bounty EV 指標、PKO preset（例如 `6max-pko-10bb`）。

實作（T2，已完成）：
- 設定：`TournamentConfig.bounty = BountyConfig(bounty_share, cash_share, winner_keeps_own)`，預設 0.5、0.5、是。沒有 `bounty` 的設定 JSON 與 T1 完全相同。
- 誰收 bounty：被淘汰者**有資格爭奪的最高那個 pot** 的贏家。這個 pot 的其他參與者投入的籌碼都不少於被淘汰者，所以贏家一定蓋得過他；平分 pot 時平分 bounty；對手蓋牌時由沒蓋牌的人收。
- 每手可以看到每個座位頭上的 bounty（`TournamentHandInfo.bounties`，以及 `starting_bounty`），給 v2 輸入的 bounty 欄位與規則 bot 使用。
- 每手記錄 `bounties_before`、`bounties_after` 與 `knockouts`（被淘汰者、收取者、現金、加到頭上的金額），與 ICM 分開。
- 參考對手：`HeuristicPushFoldBot(bounty_weight=1.5)`（worker 名稱 `hunter`）：跟注別人的全下時，若自己蓋得過對方，依對方 bounty 相對起始 bounty 的倍數放寬門檻。

## 6. 錦標賽上的學習（T3）

### 6.1 已決定（2026-09 與使用者討論）

| 項目 | 決定 |
|---|---|
| 演算法 | 第一版擴充現有 NFSP（沿用 checkpoint、Actor/Learner、升級判定、訓練曲線）；以錦標賽 benchmark 判斷，卡住再換 PPO 自我對打加 league |
| 輸入 | v2（406 維），錦標賽欄位由每手的 `TournamentHandInfo` 填入：盲注、ante、等級、剩餘人數、payout 向量、各座位 bounty（以起始 bounty 為 1） |
| 獎勵 | 每手結束時給「權益變化」：權益 = 剩餘獎金的 ICM 份額 + 已拿到的 bounty 現金（`Tournament.equity`）。一個座位的獎勵加總 = 最終名次獎金 + bounty 現金 − 起始權益，也就是最終結果加一個常數（potential-based shaping，不改變最佳策略） |
| 跨手 | 預設同一座位的決策跨手連接，只有被淘汰或錦標賽結束才是終點；也可選擇每手結束（`hand_horizon`）。第一輪探測中每手結束明顯較穩定（見 status.md） |
| 第一個賽制 | 10 BB PKO（65/35，50% bounty、50% 現金） |
| 對手 | 學習者固定一個座位（依 seed 輪換），其他 5 個座位各有一半機率是模型自己（自我對打），否則從 random、call、tight、aggro、pushfold、hunter 抽一個 |
| 驗收 | 對混合 Rule Bot 至少 3,000 場（500 seed × 6 座位），ROI 95% 信賴區間下限 > 0，且高於同條件的 `hunter`（−7.3%）與 `HeuristicPushFoldBot`（−10.5%）；換 seed 重跑也成立 |

### 6.2 未納入第一版
- 其他人頭上尚未收到的 bounty 不算進權益（只有收到才算），避免重複計算。
- 25/50 BB 與一般錦標賽在 10 BB PKO 有結果後再做。
- 分散式錦標賽訓練（Actor/Learner Job）與 UI preset 在單機探測有正向結果後再接上。

## 7. 尚未決定

- 多桌、併桌、late registration 的規則與時程。
