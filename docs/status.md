# TableForge 開發進度

最後更新：第一版收尾（設定、營運、GPU）階段。每完成一個階段，本文件與程式碼在同一個 commit 更新。

狀態定義：

- **已完成**：功能實作完成，並有自動測試或實際執行驗證。
- **部分完成**：主要流程可運作，但缺少規格要求的部分，缺漏列於備註。
- **延後**：程式已就緒，但驗收需要目前沒有的環境。
- **未開始**：尚未實作。

## 總覽

| 里程碑 | 內容 | 狀態 | 主要 commit |
|---|---|---|---|
| M0 | 契約骨架、ID、Job/Artifact、SQLite、本機 artifact store | 已完成 | `e6b9178` |
| M1 | Heads-Up Poker Engine、Rule Bots、資金守恆 | 已完成 | `f3b1679`–`df4b40d` |
| M2 | 單機工作流：lease、heartbeat、失敗/取消、experiment、worker loop | 已完成 | `5b27e50`–`de6318d` |
| C0/C1 | 能力模型、CPU/RAM/磁碟偵測、`doctor` | 已完成 | `ab79ff0`、`0e95ed3` |
| C2 | NVIDIA GPU 偵測、GPU 測試分級 | 已完成 | 本階段 |
| C3 | Scheduler 資源過濾、reservation、capability revision | 已完成 | `179933e` |
| C4 | 降級執行計畫（fallback）、OOM 分類 | 已完成 | 本階段 |
| C5 | UI 呈現能力資訊 | 未開始 | 平台化階段 |
| M3a | gRPC 傳輸、enrollment/session、TLS、artifact 傳輸 | 已完成 | `61cf545`、`b5c4c51` |
| M3a 第 2 階段 | Mac 實機驗證、跨平台結果一致性 | 延後 | 需在 Mac 上執行 |
| M3a 實機 | 兩台電腦實際連線驗收 | 延後 | 需要 Tailscale |
| 營運 | 設定分層、outbox、metrics、artifact GC、session 輪換/撤銷 | 已完成 | `5e76237`、`e845d0c` |
| M3b | PostgreSQL、MinIO、Docker | 未開始 | 下一步 |
| M4 | NFSP 多輪訓練、評估、promotion（CPU） | 已完成 | `5abd094`、`c897b37`、`bf77238` 及本階段 |
| M5 | League、歷史模型、matchup matrix、1,000 萬手 soak | 已完成 | 本階段 |
| O0–O7 | 對手建模研究線 | 未開始 | M5 之後 |

目前測試：219 個測試全部通過（含 RTX 2070 實機 GPU 測試），Ruff、mypy strict 通過。

## 已完成項目與驗收方式

### M0 契約骨架
- UUIDv7 ID、Job/Artifact 領域模型、repository ports。
- SQLite Job repository：冪等建立、衝突偵測。
- 本機 content-addressed artifact store：hash 驗證、去重、原子提交。
- 驗收：contract tests。

### M1 Poker Engine
- 牌、洗牌（固定 seed 可重現）、5–7 張牌型評估。
- Heads-Up 下注狀態機：盲注、行動順序、min-raise、短碼 all-in 不重開加注權。
- 結算：showdown、平分、未跟注籌碼退還。
- 每個座位的 Observation，看不到對手手牌與未發公牌。
- Rule Bots：Random、Call、Tight、Aggro。
- 驗收：Hypothesis property tests；**100 萬手資金守恆 soak，零錯誤，約 5,000 手/秒**。

### M2 單機工作流
- Job lease、heartbeat 續租、過期重排、fencing、冪等完成。
- 失敗立即重試或終止、合作式取消、結構化錯誤紀錄。
- Node registry、capability revision、TTL、attempt provenance、資源 reservation。
- Experiment revision、preset、`start/status/stop`、持續 worker loop、JSON log。
- 驗收：整合測試；CLI 端到端實跑。

### C0/C1/C3 能力判斷
- 偵測 CPU、RAM（Windows、Linux、macOS）、磁碟。
- 每個資料目錄有固定的 NodeId。
- Workload 相容判斷；不相容或能力未知時不派工；高優先 GPU Job 不會擋住 CPU Job。
- 驗收：單元測試。

### M3a 遠端傳輸
- gRPC Coordinator server 與 Worker client，Worker 只依賴 `CoordinatorClient` 介面。
- 一次性 enrollment token、session token；節點不能冒用其他節點身分。
- 預設 TLS（自簽憑證）；只有明確 `--insecure` 才不加密。
- Artifact 分段上傳/下載、hash 驗證、Worker 本機 cache。
- 網路錯誤指數退避重試；lease 失效等錯誤轉成明確例外，不盲目重試。
- 驗收：TLS 與非 TLS 兩種模式的整合測試；**真實多程序測試**：Coordinator 與 Worker 分開執行、強制關閉 Worker 後由另一個 Worker 接手完成；README 兩節點流程在乾淨目錄實跑通過。

### M4 NFSP（CPU）
- 固定長度狀態編碼 `poker-hunlhe-vector-v1`，測試確認不含對手手牌與未發公牌。
- 抽象動作 `nlhe-discrete-v1`（fold、check/call、半池、滿池、all-in），測試確認一律轉成合法下注。
- NFSP：Q 網路、平均策略網路、circular replay、reservoir buffer。
- **Checkpoint bundle v2**：分成 `actor`（Q＋平均策略）、`inference`（只有平均策略）、`training`（優化器、target 網路、reservoir buffer、RNG）。可完整接續訓練，接續後結果與不中斷訓練位元相同。
- **Actor/learner 分離**：Actor Job 讀取 checkpoint 自我對打，輸出 experience shard；Coordinator 產生固定的 dataset snapshot；Learner Job 只讀 snapshot 訓練。Shard 或 snapshot 與 checkpoint 不一致、或 artifact 被竄改都會被拒絕。
- **Checkpoint registry**：記錄 parent、輪次、hash、狀態（candidate、active、rejected、retired），每個 agent 同時只有一個 active。CLI `checkpoint list/show`。
- **評估與 promotion**：Candidate 與 active 直接對打，分批評估直到信賴區間寬度達標或到上限；另對 Rule Bots 評估作為參考。規則：只有 candidate 的 95% 信賴區間上限 < 0（確定比較差）才拒絕；被拒絕時下一輪從原 active 重新訓練。
- **背景推進**：`coordinator serve` 內建 reconciler；單機模式由閒置的 Worker 推進。不再需要查詢 status。
- Preset：`hu-nfsp-cpu-smoke`（單一 Job，快速檢查）、`hu-nfsp-cpu-iterative`（3 輪，每輪 2 個 Actor Job）。
- 驗收：
  - 同一設定跑兩次，每一輪的 checkpoint hash 完全相同。
  - Reconciler 重複執行、Coordinator 重啟後不會重複建立 Job。
  - 強制拒絕 candidate 時，下一輪正確以原 active 為 parent。
  - Job 失敗時實驗標記為 failed。
  - **真實多程序測試**：`coordinator serve` 加上兩個遠端 Worker 程序，不需人工介入完成 3 輪、13 個 Job。
  - 單機 CLI 一次 `worker start` 完成 3 輪，約 19 秒。

### M5 League
- **對手池（population）**：每一輪建立一個固定的 population artifact，包含 current（自我對打）、最近幾個歷史 checkpoint、4 個 Rule Bot。權重預設 40% / 30% / 30%；還沒有歷史 checkpoint 時，歷史的比重併入 current。
- **Matchmaking**：Actor 每一手依權重抽對手，用 actor seed 決定，結果可重現。對上固定對手時只記錄 learner 那一方的 experience。Population 與 checkpoint 不一致時會被拒絕。
- **Matchup matrix**：最後一輪結束後，所有已升級的 checkpoint 和 Rule Bots 兩兩對打（Rule Bot 之間不對打），輸出 bb/100 與 95% 信賴區間。CLI `league matrix <experiment-id>`，`*` 表示信賴區間不含 0。
- Preset：`hu-nfsp-cpu-league`（4 輪、每輪 2 個 Actor、歷史上限 3）。
- **並行安全**：Coordinator 背景 reconciler 和閒置 Worker 可能同時推進；population 與 snapshot artifact 改用內容 hash 當 ID，本機 artifact store 改成可同時提交同一個 artifact。
- 驗收：
  - Matchmaking 固定 seed 可重現，2,000 手抽樣比例符合權重。
  - 歷史對手數從第 1 輪的 0 增加到上限 2；matrix 完整且正負對稱。
  - 8 個 reconciler 同時執行不會重複建立 Job；32 個執行緒同時提交同一個 artifact 都成功。
  - 單機 CLI 一次 `worker start` 完成 4 輪加上 matrix，共 47 個 Job，約 31 秒。
- **1,000 萬手 soak**：`hu-rule-soak-10m` preset，16 個 Worker 程序約 4 分鐘完成 1,000 個 Job、7,042 萬個動作。所有 shard 的資金淨值為 0，seed 範圍 0–10,000,000 連續且不重疊。

限制：
- CPU 小型設定只能證明流程正確，不能證明模型變強。League 實測中，第 4 輪對 round-0 為 +553.9 bb/100（顯著），但對 Tight bot 仍顯著落後（-634.0 bb/100）；多數格子信賴區間含 0。實測第 3 輪 candidate 對 active 為 -65.2 bb/100，信賴區間上限 45.6，依規則仍升級。要評估真正的學習效果需要更長的訓練和更多評估手數。

### 設定與營運
- **設定分層**：預設值 < `<data-dir>/tableforge.toml`（或 `--config`） < 環境變數 `TABLEFORGE_<SECTION>__<KEY>` < CLI 參數。未知設定或錯誤值直接報錯。啟動 log 會輸出設定，密碼與金鑰遮蔽。
- **組件集中建立**：`composition.py` 依設定建立所有 repository 與 artifact store；CLI 不再直接建立 SQLite 類別。Checkpoint 與 auth 也改用 port 介面。
- **背壓**：Worker artifact cache 有容量上限（`worker.cache_max_bytes`，預設 10 GiB），超過時刪除最久未用的檔案。
- **Log context**：JSON log 帶時間、component、node_id、job_id、attempt_id、experiment_id。
- **Outbox**：Job、experiment、checkpoint 的狀態變更與事件在同一個 transaction 寫入 `events` 表；被拒絕的操作不產生事件。CLI `events tail`。
- **Metrics**：`coordinator serve --metrics-listen 127.0.0.1:9464` 提供 Prometheus `/metrics`：Job／實驗／checkpoint 狀態、active 與過期 lease、節點數、失敗原因、RPC 次數與延遲、artifact 傳輸量、reconciler 結果。
- **Artifact GC**：`artifact gc --dry-run`。Job 輸入輸出、payload 內的 sha256、checkpoint 引用的 artifact 都保留；未被引用且超過寬限時間（預設 24 小時）才刪除。
- **安全**：`node revoke <node-id>` 立即撤銷節點所有 session；`node rotate-session` 換新 session，舊 token 立即失效。Enrollment token 固定以 `tfe_` 開頭，避免被 CLI 誤當成參數（先前約 1/64 機率失敗）。
- 驗收：單元與 contract tests；實跑 coordinator + 遠端 worker：rotate 後完成 Job、metrics 與 events 正確、revoke 後 worker 被拒（UNAUTHENTICATED）。

### C2/C4 GPU（RTX 2070 8 GB 實機）
- **CUDA 版 PyTorch**：Windows／Linux 自動從 PyTorch cu130 index 安裝（macOS 仍用一般版本）。安裝：`uv sync --extra learning --extra gpu`。
- **GPU 偵測**：優先用 NVML，失敗時改用 `nvidia-smi`；讀型號、VRAM 總量與可用量、driver、compute capability。偵測失敗記為警告，不當成 0。`doctor` 顯示每張 GPU。
- **Windows 記憶體限制**：Windows 上 GPU 配置也會佔用系統 commit（實測每配置 512 MiB，commit 就少約 512 MiB）。本機 page file 較小時，實際能用的 VRAM 比顯示的少，因此可用 VRAM 取「GPU 可用量」與「系統 commit 餘量減 1 GiB」的較小者，並給出 `GPU_MEMORY_LIMITED_BY_SYSTEM_COMMIT` 警告。
- **降級執行計畫**：Workload profile 定義 VRAM 模型（固定用量＋每個 batch 項目用量，依實測峰值設定）與可接受的 batch 清單。Scheduler 選放得下的最大 batch；不是第一選擇時狀態為 DEGRADED，並記錄在 attempt 的 execution plan。DEGRADED 的 Job 可以派工。
- **執行**：Execution plan 隨 lease 傳給 Worker（proto 新增欄位）；Learner 依 plan 選 device 與 batch 上限。Checkpoint 一律以 CPU tensor 儲存，GPU 訓練的 checkpoint 可在 CPU 載入。
- **OOM**：CUDA OOM 記為 `GPU_OUT_OF_MEMORY`，錯誤摘要包含 execution plan 與峰值 VRAM。
- **CPU Worker 不碰 GPU**：PyTorch optimizer 每一步都會查詢 accelerator，即使全部在 CPU 也會建立 CUDA context（每個約 250 MB commit）。多個 CPU Worker 同時執行時會耗盡記憶體。Coordinator 與 `--no-gpu`（或 `worker.use_gpu=false`）的 Worker 會在啟動時隱藏 GPU。
- Preset：`hu-nfsp-gpu-iterative`（3 輪，hidden 512，batch 8192，learner 用 CUDA）。
- **GPU 測試分級**：`requires_cuda`、`vram_8gb`、`vram_16gb`、`vram_24gb` marker，依偵測結果自動略過。GPU 測試在子程序中執行，避免與其他測試共用 CUDA 狀態。
- 驗收：
  - `doctor` 正確顯示 RTX 2070（8 GiB，compute 7.5）；`hu-nfsp-small-gpu` 依當下餘量降級為較小 batch。
  - 依計畫的 batch 實際在 GPU 上訓練，峰值不超過預估。
  - GPU 與 CPU 訓練同一份資料，結果在誤差 1e-3 內一致；GPU checkpoint 可在 CPU 載入。
  - `hu-nfsp-gpu-iterative` 實跑：13 個 Job 全部成功，3 個 learner 都以 `cuda:0`、batch 8192 執行，約 6 分鐘。
  - 完整測試連續 3 次全部通過。

## 延後項目

### Mac 實機驗證（M3a 第 2 階段）
在 Mac 上執行：

```
uv sync
uv run pytest
uv run tableforge doctor
```

確認記憶體偵測正確、沒有 NVIDIA GPU 時 GPU workload 顯示 UNSUPPORTED。另外用相同 seed 在兩台電腦各跑同一個 simulation Job，比對輸出 hash。

### 兩節點實機驗收
兩台電腦目前不在同一個網路，Windows 沒有 Wi-Fi，需要先安裝 Tailscale。驗收項目：Mac 領取並完成 Job；中途中斷後由另一個 Worker 接手；結果不重複計入；GPU Job 不派給 Mac。

## 與規格的差異

以下是刻意的簡化，之後需要補上或更新規格：

| 項目 | 規格 | 目前實作 |
|---|---|---|
| Artifact 傳輸 | Coordinator 不轉送大型 artifact，Worker 直接連 storage | 經由 Coordinator 的 gRPC 串流，單檔上限 1 GiB |
| 兩節點 metadata | PostgreSQL | 仍為 SQLite（單一 Coordinator 可運作） |
| Experience 格式 | Parquet shard | NPZ 形式的 zip（規格允許的替代格式，避免新增 pyarrow） |
| RL buffer 跨輪保留 | 保留最近幾輪資料 | 每輪只使用當輪 shard；reservoir buffer 跨輪保留 |

已修正的差異：NFSP 訓練改讀 dataset snapshot；experiment 改由背景推進；outbox 事件已加入。

## 下一步候選

依建議優先順序：

1. **M3b**：PostgreSQL adapter、S3/MinIO 與 Worker 直連 storage、Docker 部署（進行中）。
2. **學習效果驗證**：較長的訓練與評估設定（現在可用 GPU），確認模型相對 Rule Bots 有顯著進步。目前第 4 輪仍輸給 Tight bot。
3. **PFSP**：依 matchup 勝率調整對手權重（目前為固定權重）。
4. **O0–O7 對手建模**。
