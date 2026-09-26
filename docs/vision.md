# TableForge 長期願景

本文件記錄 TableForge 最終希望成為的樣子，**不是目前的工作計畫**。目前進度與下一步見 [status.md](status.md)；各主題的工程規格見 [architecture/](architecture/README.md)；初期構想見 [concepts/](concepts/README.md)。

願景分兩部分：

- **平台的最終能力**：一套 TableForge 能做什麼。
- **最終的部署形態**：多套 TableForge 之間怎麼連接。

兩者互相獨立。平台能力不需要等部署形態完成，部署形態也不限制平台能力。

## 1. 平台的最終能力

### 1.1 多演算法同桌對戰

不同訓練方式的 Agent 在同一張牌桌上對戰、產生訓練資料並持續演化：

- NFSP（已有）、Deep CFR、PPO、Rule Bots（已有）、固定策略 baseline、歷史 checkpoint（已有），以及未來的 search + CFR + 神經網路 Agent。
- 所有 Agent 只需遵守統一的 Observation、Action、遊戲規則、Hand History 與評估協定；各自保有自己的 buffer、網路、optimizer 與訓練頻率。
- League 同時包含多種演算法，matchup matrix 可比較演算法之間的相對強弱。

**目前狀態**：League 已讓 NFSP 的新舊版本與 4 種 Rule Bots 互相對戰並產生 matchup matrix。Deep CFR、PPO 尚未實作。擴充方式已在 `architecture/01-system.md` 第 7 節定義：新增 executor、AgentManifest 擴充與 artifact 類型，不修改 Job 生命週期。

### 1.2 6-Max

支援最多 6 人的牌桌，使多種演算法真正「同坐一桌」，而不只是兩兩對戰。

- 擴充 GameConfig 與撲克引擎（座位、行動順序、side pot、多人 showdown），Worker 協定不變。
- 資金守恆、資訊隔離等既有不變量必須以同樣嚴格的 property test 與大規模 soak test 驗證。
- 評估方式需要擴充：多人牌局的 bb/100 與信賴區間需要考慮座位輪替與多方互動。

**目前狀態**：2–6 人撲克引擎（side pot、多人 showdown）、多人 Rule Bot simulation 與座位輪替評估已完成，並通過 property test 與 2–6 人各 100 萬手 soak。NFSP 與 League 仍只支援 Heads-Up。

### 1.3 網頁介面

在 CLI 之外提供網頁介面，呈現與 CLI 完全相同的判定結果：

- 節點與硬體能力、workload 相容性（`architecture/06` 的 C5）。
- 實驗、Job、checkpoint 升級紀錄、matchup matrix 與訓練曲線。
- 多人共用時的使用者、專案與權限管理（`architecture/07`）。

原則：核心判定只在 Scheduler/Application 層，UI 只負責呈現；UI 不阻塞 CLI 版本的任何功能。

**目前狀態**：只有 CLI 與 Prometheus metrics。

### 1.4 對手建模

Agent 不只根據當前這一手牌決策，還能根據同一對手過去多手牌的行為建立模型，並依對手的長期或近期特徵調整策略。完整規格與 O0–O7 研究線見 `architecture/05-opponent-modeling.md`。

**目前狀態**：未開始。

## 2. 最終的部署形態：聯邦式對等網路（D1）

### 2.1 形態

- 每位參與者各自執行一套完整的 TableForge，**所有節點地位平等，沒有全網的中心**。
- 每個實驗由建立它的節點擁有與管理。該節點的 Coordinator 只是「這個實驗的擁有者」，不是全網的中心；lease、fencing、experiment revision、checkpoint 升級等既有正確性保證在節點內部完全不變。
- 節點之間交換：
  - **模型**：附簽名與來源資訊的 checkpoint，以 sha256 定址，收到的一方自行驗證。
  - **對戰結果**：附完整重算資訊（seed、雙方 checkpoint 的 sha256、程式版本），任何節點都能重算驗證。
  - **算力（選擇性）**：借用其他節點的 Worker 執行自己實驗的 Job；Job 的所有權仍屬實驗擁有者。
- 典型用途：
  - 跨節點 League：把別人的模型加入自己的對手池，或輪流主辦對戰。
  - 跨節點排名：比較不同團體訓練出的模型。
  - 借用朋友的 GPU 跑自己的實驗。

### 2.2 實作方式

- 很可能以**另一個專案或 branch** 實作，建構在 TableForge 之上，而不是修改 TableForge 的核心。
- 每個對等節點內部就是一套完整的 TableForge（單機版或小團體版）。
- D1 層負責節點之間的事：節點身分、簽名、節點發現、artifact 交換、跨節點 League 與排名。
- D1 層透過 TableForge 的**控制面 API** 操作實驗與取得 checkpoint。控制面 API 也是多人共用路線 S1 的內容（`architecture/07` 第 7 節），因此該階段同時是 D1 的前置條件。

### 2.3 需要的能力

| 能力 | 說明 |
|---|---|
| 節點身分 | 每個節點自己產生金鑰（例如 Ed25519），不由中央發放 |
| 簽名與來源 | checkpoint 與對戰結果附產生者簽名；可追溯到哪個節點、哪個實驗、哪個 parent |
| 節點發現與連線 | 私有網路（Tailscale、Headscale）、點對點函式庫（例如 libp2p）或手動加入 |
| Artifact 交換 | 以 sha256 向其他節點取得檔案；內容自行驗證，不需信任來源 |
| 結果驗證 | 對戰與評估可用 seed 重算比對；GPU 訓練沒有位元級決定性，別人的訓練結果只能依信任或信譽判斷 |
| 信任與信譽 | 可信節點名單、封鎖機制、防止一人冒充多個身分（Sybil） |
| 跨節點 League 與排名 | 在不共享資料庫的情況下組成對手池、安排對戰、累積排名 |

### 2.4 從現在到 D1 的演進

```text
單機
 → A：自己的多台電腦（單一 Coordinator，私有網路）
 → B：信任的小團體（使用者、專案權限、配額、稽核）
 → D1：聯邦式對等網路（多套 TableForge 互相交換模型、結果與算力）
```

每一步都沿用前一步的內容：

- A 與 B 使用同一個架構：單一 Coordinator、Worker 主動拉取 Job（`architecture/07` 第 3 節）。
- B 的使用者身分、稽核與 artifact 存取範圍，在 D1 中變成「每個節點內部」的功能。
- B 的控制面 API 是 D1 層與 TableForge 的接點。

## 3. 途中必須守住的原則

以下原則讓 D1 在未來仍然可行。目前的實作都已符合，之後的修改不能破壞：

1. **Artifact 以 sha256 定址且不可變**：跨節點交換時可直接驗證內容，不需要改格式。
2. **對戰與評估結果可以重算**：保留 seed 與雙方 checkpoint 的 sha256；日後修改結果格式時補上程式版本。
3. **ID 不依賴中央分配**：使用 UUIDv7 或內容 hash，在全網都不會衝突。
4. **對外契約使用有版本的 Protobuf，欄位只新增不修改**：D1 層可以穩定地透過 API 操作 TableForge。
5. **模型載入只使用安全格式**：safetensors、`allow_pickle=False` 的 NumPy；不引入 pickle 或 `torch.load`，因為未來會載入別人提供的模型。
6. **核心邏輯不依賴全網唯一的中心**：撲克引擎、executor、NFSP、League 與部署方式無關。

平台能力的擴充另外必須遵守既有規格：新演算法與 6-Max 透過既有 Job、Artifact、Agent、Checkpoint 邊界加入，不修改核心生命週期（`architecture/01-system.md` 第 7 節）；網頁介面只呈現 Application 層的判定結果。

## 4. 明確不走的方向

- **D2：全網共同訓練同一個模型、沒有任何節點擁有它**。這需要全網共識（在不互信的節點之間還需要拜占庭容錯），會削弱可重現性與「每個 Job 只完成一次」的保證，而且屬於分散式系統研究，會讓專案重心離開撲克 AI。
- **Multi-master，或以最終一致性處理 Job 所有權**（`architecture/04` 第 10 節）。
- **讓節點執行別人提供的程式碼**，除非有容器沙箱、唯讀檔案系統、資源限制與禁止對外連線（`architecture/03` 第 9 節）。

## 5. 尚未決定的問題

- D1 要另開專案，還是在同一個 repository 的 branch。
- 節點發現方式：私有網路、點對點函式庫或中央目錄服務（只提供目錄，不擁有任何實驗）。
- 信譽如何計算；是否允許陌生節點加入，或只限邀請。
- 如何信任別人的訓練結果：只接受附重算資訊的評估結果，或引入信譽權重。
- 跨節點排名的方法：Elo、TrueSkill，或以 matchup matrix 為基礎的方法。
- 6-Max 與多演算法同桌時的評估方法與所需手數。

## 6. 文件維護

- 願景改變時更新本文件，並寫明改變的理由。
- 被排入工作計畫的項目列在 `status.md` 的「下一步候選」；完成後記錄於 `status.md`，本文件只更新「目前狀態」。
