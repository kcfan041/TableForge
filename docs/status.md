# TableForge 開發進度

最後更新：T1 6 人錦標賽環境與 tournament benchmark 完成。錦標賽路線見 [architecture/08-tournament.md](architecture/08-tournament.md)。每完成一個階段，本文件與程式碼在同一個 commit 更新。

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
| C2 | NVIDIA GPU 偵測、GPU 測試分級 | 已完成 | `ca5e60e` |
| C3 | Scheduler 資源過濾、reservation、capability revision | 已完成 | `179933e` |
| C4 | 降級執行計畫（fallback）、OOM 分類 | 已完成 | `ca5e60e` |
| C5 | UI 呈現能力資訊 | 部分完成 | 網頁「節點」頁顯示 CPU、記憶體、GPU、可執行角色與警告；尚未顯示 workload 相容性與原因碼 |
| M3a | gRPC 傳輸、enrollment/session、TLS、artifact 傳輸 | 已完成 | `61cf545`、`b5c4c51` |
| M3a 第 2 階段 | Mac 實機驗證、跨平台結果一致性 | 延後 | 需在 Mac 上執行 |
| M3a 實機 | 兩台電腦實際連線驗收 | 延後 | 需要測試者與 Tailscale；程式與流程已以本機區域網路 IP 模擬驗證（見 S0） |
| 營運 | 設定分層、outbox、metrics、artifact GC、session 輪換/撤銷 | 已完成 | `5e76237`、`e845d0c` |
| M3b-1 | PostgreSQL metadata、schema migration、SQLite 資料搬移 | 已完成 | `817e9f7` |
| M3b-2 | S3 相容 artifact store、Worker 直連 storage | 已完成 | `5f7e613` |
| M3b-3 | Docker 映像與 Compose 部署、主機 GPU Worker | 已完成 | `ce713b0` |
| S0 | 測試者加入：邀請碼、`join`、叢集 ID、Worker 編號、遠端操作（viewer／operator）、連線診斷、測試者指南 | 已完成 | `7df7cb7` 及本階段；實體兩台電腦驗收延後 |
| M3b-3 CUDA 容器 | GPU Worker 在容器內執行 | 延後 | 需要 Docker Desktop 的 WSL2 後端 |
| M4 | NFSP 多輪訓練、評估、promotion（CPU） | 已完成 | `5abd094`、`c897b37`、`bf77238` 及本階段 |
| M5 | League、歷史模型、matchup matrix、1,000 萬手 soak | 已完成 | 本階段 |
| 6-Max | 2–6 人撲克引擎、side pot、多人 Rule Bot simulation、座位輪替評估 | 已完成 | `d64d844`；NFSP 與 League 維持兩人 |
| W1 | Coordinator 網頁介面：HTTP API、admin 角色、網頁登入、即時更新、React 前端 | 已完成 | `0890e7e` |
| W2 | 測試者端本機控制台 `tableforge ui`（加入、連線檢查、啟停 Worker、log） | 已完成 | `30cfc2a` |
| U1 | 單一入口：第一次開啟選「建立／加入」，建立叢集全自動、背景叢集服務、建立者自動成為 Worker | 已完成 | `bedb6c2` |
| U2 | `tableforge ui` 內的叢集管理頁面（依權限顯示）、`ControlService` 補齊、admin 邀請碼 | 已完成 | `cf470a7` |
| U3 | 瀏覽器網頁（給只用瀏覽器的人）預設關閉、由建立者在 UI 開啟；文件改成只介紹 `tableforge ui` | 已完成 | 本階段 |
| O0–O7 | 對手建模研究線 | 未開始 | M5 之後 |
| P1-a | v2 輸入：牌力特徵、2–6 人通用、錦標賽欄位 | 已完成 | 本階段；R² 0.48 → 0.87–0.89 |
| P1-b0 | 訓練用的 UI：訓練曲線（每輪 Rule Bot benchmark）、自訂訓練參數、多 seed 並排 | 已完成 | `23874ff` |
| P1-b | HU cash 長時間訓練，穩定贏 4 種 Rule Bot | 已完成 | 獎勵縮放 + 大 buffer：3 個 seed 都顯著贏 4 種 Rule Bot |
| T1 | 6 人錦標賽環境、可設定 payout 與名次判定、`HeuristicPushFoldBot`、tournament benchmark | 已完成 | `1e603f9`；設計見 [architecture/08-tournament.md](architecture/08-tournament.md) |
| T2 | PKO bounty 規則與 bounty 指標 | 已完成 | 本階段 |
| T3 | 錦標賽上的學習 | 第一版已達驗收 | 10 BB PKO：5 個 seed 全部顯著獲利（平均策略 +19%、Q 網路 +51%） |

目前測試：726 個測試全部通過（另有 1 個只在非 Windows 執行）（含 RTX 5070 Ti 實機 GPU 測試、Docker PostgreSQL 17 與 Docker RustFS 上的測試），Ruff、mypy strict 通過。沒有 GPU、PostgreSQL 或 S3 服務時，相關測試會自動略過。

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

### C2/C4 GPU（RTX 2070 8 GB 開發，RTX 5070 Ti 16 GB 重新驗證）
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
- **換成 RTX 5070 Ti（16 GB，compute 12.0，Blackwell）後重新驗證**：
  - PyTorch 2.14 cu130 原生支援 sm_120，不需要改程式；GPU 測試全部通過，16 GB 分級測試不再略過。
  - `doctor` 正確顯示 5070 Ti。GPU 空閒時 `hu-nfsp-small-gpu` 以完整 batch（262,144）執行；同時有其他程式（例如遊戲）佔用 GPU 時，依剩餘 VRAM 自動降為 131,072，實測峰值 2.7 GiB，未超過預估。
  - 在 Compose 驗收中跑完 `hu-nfsp-gpu-iterative`（見 M3b-3）。

### M3b-1 PostgreSQL
- **共用 SQL 實作**：Job、Node、Experiment、Checkpoint、Auth、Event 六個 repository 只寫一份（`infrastructure/sql/`），SQLite 與 PostgreSQL 只有連線、鎖定語法和時間型別不同。原本的 `SQLite*Repository` 類別保留，改為包一層。
- **並行安全**：PostgreSQL 領取 Job 時先掃描候選，再對單筆用 `FOR UPDATE SKIP LOCKED` 鎖定，多個 Coordinator 程序或執行緒同時領取不會拿到同一個 Job，也不會互相等待。續租、完成、失敗、取消、checkpoint 升級、experiment revision、session 輪換都會先鎖定該列。一次掃描 64 筆，第一批都不相容時會繼續往下找。
- **Outbox**：狀態變更與事件在同一個 transaction 寫入；PostgreSQL 的事件序號使用 identity 欄位。
- **Schema 版本管理**：`schema_migrations` 表記錄版本；PostgreSQL 用 advisory lock 避免多個程序同時升級。資料庫版本比程式新時直接拒絕啟動。舊版 SQLite 資料庫會自動補上欄位。
- **時間**：PostgreSQL 使用 `TIMESTAMPTZ`；SQLite 一律先轉成 UTC 再存。修正了一個既有問題：以不同時區的時間寫入時，SQLite 以文字比較 lease 到期時間會出錯。
- **設定**：`metadata.backend = "postgres"` 與 `metadata.url`；log 中的密碼會遮蔽。安裝：`uv sync --extra postgres`（psycopg 3 + 連線池）。
- **搬移**：`metadata copy-from-sqlite <舊 tableforge.db>`，整個搬移在一個 transaction 內完成；目標不是空的會拒絕，失敗時不留下任何資料。保留所有 ID、revision、attempt 紀錄與事件序號，搬移後新事件接續原序號。`metadata migrate-schema` 顯示目前 schema 版本。
- **測試**：contract tests 改為同一份測試同時跑 SQLite 與 PostgreSQL（每個測試使用獨立 schema）。新增並行測試：20 個執行緒搶 12 個 Job、16 個執行緒同時用相同冪等鍵建立 Job、16 個執行緒同時推進同一個實驗、6 個 checkpoint 同時升級。
- 驗收（Docker PostgreSQL 17，限制 1 GiB 記憶體）：
  - SQLite 上跑完的實驗搬到 PostgreSQL 後狀態一致；再次搬移被拒絕。
  - PostgreSQL 上跑完 `hu-nfsp-cpu-iterative`（13 個 Job，約 19 秒）。
  - `coordinator serve` 使用 PostgreSQL，4 個遠端 Worker 程序跑 `hu-rule-soak`（100 個 Job），中途強制關閉其中一個：100 個 Job 全部成功、沒有任何 Job 被完成兩次、被中斷的 Job 由其他 Worker 接手，metrics 與事件數正確。

### M3b-2 S3 artifact 與 Worker 直連
- **S3 相容 artifact store**：以 sha256 為 key 存放（`sha256/aa/bb/<hash>`），每個 artifact 的描述存在 `metadata/<id>.json`。上傳時附帶 `x-amz-checksum-sha256`，storage 本身會拒絕內容不符的上傳；以 `If-None-Match: *` 避免覆寫既有物件，多個 Worker 同時上傳同一份內容也安全。
- **Worker 直連**：新增三個 RPC。Worker 先向 Coordinator 要一個有時效的簽名網址（預設 15 分鐘，上限 1 小時），直接對 storage 上傳或下載，再通知 Coordinator 登記。Coordinator 登記前會檢查物件確實存在、大小與 checksum 相符。已經存在的內容不會重複上傳。
- **簽名網址的保護**：上傳網址綁定單一 key、sha256 與長度；改內容、拿掉 checksum header、改簽名、改 key、去掉簽名、過期都會被拒絕（測試逐一驗證）。
- **自動退回**：本機 artifact store 不支援直連時，Worker 自動改用原本經過 Coordinator 的 gRPC 串流，不需要設定。也可用 `artifacts.direct_transfer = false` 強制關閉直連。
- **介面整理**：artifact store 改成 `put`／`get`／`read`／`list_objects`／`delete_object`，本機與 S3 共用同一套 contract tests。`artifact gc` 改為透過 store 列舉物件，S3 也能用。Coordinator 在 Docker 內、Worker 在外面時，可用 `artifacts.public_endpoint_url` 指定 Worker 看到的 storage 位址。
- **Storage 選擇**：MinIO 已不再公開提供社群版 Docker 映像（Docker Hub 與 quay.io 都無法下載），改用 Apache-2.0 授權的 RustFS 驗證。SeaweedFS 也測過，但預設設定不驗證簽名，不適合。程式只使用標準 S3 API，AWS S3、MinIO、Cloudflare R2 等都能用。
- **並行問題修正**：6 個 checkpoint 同時升級時，PostgreSQL 會違反「每個 agent 只有一個 active」的唯一限制；改為先鎖定該 agent 的所有 checkpoint。Windows 上多執行緒同時寫入同一個 artifact，偶爾會在讀取驗證時遇到檔案被鎖，改為短暫重試。
- 驗收（PostgreSQL + RustFS，3 個遠端 Worker 跑 `hu-nfsp-cpu-league`，中途強制關閉 1 個）：
  - 47 個 Job 全部成功，matchup matrix 產生，沒有任何 Job 被完成兩次。
  - Coordinator metrics 中經由 Coordinator 傳送的 artifact 流量為 0；直連上傳 4.5 MB、直連下載 45 MB。Coordinator 沒有建立本機 artifact 目錄。
  - Bucket 中 55 個物件全部被 metadata 引用，`artifact gc` 沒有可刪除的物件。

### M3b-3 Docker 部署與主機 GPU Worker
- **映像**（單一 `Dockerfile`，base image 以 digest 固定）：
  - `coordinator`：212 MB，只含 PostgreSQL 與 S3 用戶端，不含 PyTorch。
  - `worker-cpu`：1.13 GB，使用 lock 檔的版本，但把 PyTorch 換成同版本的 CPU 版，並移除 CUDA 函式庫。
  - `worker-cuda`：Dockerfile 已寫好，這台電腦沒有建置（見延後項目）。
  - 以非 root 使用者（uid 10001）執行。
- **Compose**：
  - `compose.yaml`：PostgreSQL + RustFS + Coordinator + CPU Workers（`CPU_WORKERS` 或 `--scale worker=N`），每個服務都有記憶體與 CPU 上限，對外 port 只開在 `127.0.0.1`。密碼放在 `.env`（範本 `docker/.env.example`，`.env` 不進 git），沒設定時 Compose 直接報錯。
  - `compose.minimal.yaml`：只有 Coordinator（SQLite、本機 storage）與 CPU Workers。
- **自動加入**：Coordinator 啟動時建立 TLS 憑證和一個可多次使用的註冊碼（預設 16 次、24 小時），放在共用 volume；Worker 容器啟動時自動註冊，憑證存在自己的 volume，重啟不需重新註冊。
- **可重複使用的註冊碼**：`coordinator enrollment-token --max-uses N --output <檔案>`；同一個節點重複註冊不會多算次數，超過上限就拒絕，並行註冊也不會超過（SQLite 與 PostgreSQL 都測過）。`node enroll --token-file`、`--bundle <資料夾>`（資料夾內含註冊碼與憑證）。
- **多個 storage 位址**：容器內的 Worker 用 `storage:9000`，Windows 上的 Worker 用 `127.0.0.1:59000`。Coordinator 設定 `artifacts.endpoints = "host=http://127.0.0.1:59000"`，Worker 設定 `artifacts.endpoint = "host"`，Coordinator 依名稱用對應位址簽名。只能選 Coordinator 設定檔列出的名稱；把網址改到其他主機會因簽名不符被拒絕。
- **主機 GPU Worker**：從 Compose 取出註冊碼與憑證，在 Windows 上用 `uv run` 執行 Worker，直接使用 5070 Ti。
- **小修正**：
  - 設定只讀取 `TABLEFORGE_<區段>__<名稱>` 格式的環境變數，`TABLEFORGE_ROLE` 等其他變數不再被誤判為錯誤。
  - Worker 閒置時只記錄一次 log，不再每秒一行。
  - log 立即輸出，不被緩衝。
- 驗收（全新 volume，Compose 的 3 個 CPU Worker + Windows 上 5070 Ti 的 GPU Worker，同時跑 `hu-nfsp-cpu-league` 與 `hu-nfsp-gpu-iterative`，第 26 秒 `docker kill` 一個 CPU Worker 容器）：
  - 60 個 Job 全部成功（league 47 個、GPU 13 個），沒有 Job 被完成兩次；被中斷的 Job 由其他 Worker 接手。
  - 3 個 CUDA learner 全部由主機 GPU Worker 執行（`cuda:0`、batch 8192），沒有派給 CPU Worker。
  - Artifact 全部直連 storage：上傳 50 MB、下載 198 MB，經過 Coordinator 的流量為 0。
  - Bucket 中 71 個物件全部被引用；結束時沒有殘留的 lease。
  - `compose.minimal.yaml` 跑完 `hu-rule-smoke`。

### S0 測試者加入
讓不同網路的測試者（Windows + NVIDIA GPU）加入叢集，提供 GPU 並遠端操作實驗。對應 `architecture/07-multi-party-and-trust.md` 的 S0，加上 S1 中「節點與操作權限分開」的部分；使用者、專案與隔離留到完整版。操作說明見 [tester-guide.md](tester-guide.md)。

- **叢集 ID**：每個叢集第一次啟動時產生固定的 `cluster_id`（schema v4 的 `cluster_identity` 表），不因換憑證而改變。
- **邀請碼**（`tfinv1_...`，約 290 字元）：包含 cluster_id、Coordinator 位址、CA 憑證的 SHA-256 指紋、一次性交換密碼，以及權限範圍（`node`、`viewer`、`operator` 的組合）。不包含任何長期憑證，也不包含 storage 位址名稱。預設 24 小時內有效，只能使用一次，一次代表一台電腦（節點）。資料庫只存 hash；可列出與作廢。
- **`join`**：
  1. 先取得 Coordinator 出示的憑證，比對指紋，不符就停止，不會送出密碼。
  2. 以 TLS 連線確認 cluster_id。
  3. 交換取得長期憑證：節點 session（預設 7 天）和／或操作憑證（預設 30 天）。
  4. 逐一測試 Coordinator 提供的 storage 候選位址，選用第一個連得到的。
- **Storage 候選清單**：Coordinator 回傳所有位址與優先順序；如果某個位址的主機和節點連進來的來源位址相同，就排在前面。節點實際測試後自行選擇。Worker 每次啟動都重新取得清單，Coordinator 改了位址也不必重新加入。
- **節點與操作憑證完全分開**：
  - 節點 session 只能呼叫 Worker 相關的 RPC。
  - 操作憑證（前綴 `tfo_`）只能呼叫新的 `ControlService`。
  - 兩者互相代用都會被拒絕。
  - 存放位置不同：節點憑證在 `--data-dir`，操作憑證在 `%APPDATA%\TableForge\clusters\<cluster_id>\`。
- **權限**：
  - `viewer`：查看實驗、checkpoint、matchup matrix、節點。
  - `operator`：另外可以建立、啟動、停止實驗。
  - 管理動作（產生與作廢邀請碼、撤銷節點與操作憑證、GC、資料搬移）只能在 Coordinator 電腦上執行，不開放遠端。
- **Worker 編號**：Worker 啟動時用節點憑證取得 `worker_id`，並記錄在 lease 與 attempt 上；Coordinator 會檢查 worker_id 屬於呼叫的節點。一台電腦可以用同一份節點憑證開多個 Worker。尚未做「每個 Worker 綁定一張 GPU」。
- **連線診斷** `doctor --coordinator`：依序檢查 dns、tcp、tls（憑證是否改變）、cluster、session、storage（實際上傳下載一個小檔案），停在第一個失敗的項目並提示原因。
- **Tailscale 設定**：
  - `coordinator init --host <名稱> [--regenerate]`：憑證已存在但不包含要求的名稱時會拒絕，必須明確加上 `--regenerate`，因為換憑證會讓已加入的節點失效。
  - Compose 以 `.env` 的 `BIND_ADDRESS`、`EXTRA_TLS_HOSTS`、`EXTRA_STORAGE_ENDPOINTS` 設定；預設仍只開放本機。
  - `invite create` 會檢查 `--address` 是否包含在憑證內。
- **測試**：新增 contract tests（SQLite 與 PostgreSQL）：邀請碼只能兌換一次（含 12 個執行緒同時兌換）、過期與作廢、兩種憑證不能互相代用、操作憑證過期與撤銷、cluster_id 在並行下唯一。整合測試涵蓋完整的 `join` 流程、指紋錯誤時不送出密碼、叢集 ID 不符、重複使用邀請碼、viewer 不能修改、Worker 編號不能跨節點使用，以及 doctor 在各種失敗時停在正確的項目。
- 驗收（以本機區域網路 IP `192.168.18.1` 模擬另一台電腦；Compose 只開放在這個 IP）：
  - 管理者為這個 IP 產生邀請碼；為憑證沒有包含的位址產生邀請碼時被拒絕。
  - 測試者在新的資料目錄 `join`：候選位址 `storage:9000`、`127.0.0.1:59000` 都連不到，自動選用 `tailnet` 位址；同一個邀請碼第二次使用被拒絕。
  - `doctor --coordinator` 6 項全部 OK，storage 直連上傳下載。
  - 測試者用 `remote` 指令遠端建立並啟動 `hu-nfsp-gpu-iterative` 與 `hu-nfsp-cpu-league`，由自己電腦的 5070 Ti Worker 和 Compose 內的 3 個 CPU Worker 一起執行：13 個與 47 個 Job 全部成功。3 個 CUDA learner 都在測試者的節點上執行，並記錄了 worker_id。檔案全部直連 storage（上傳 50 MB、下載 197 MB）。
  - viewer 可以列出實驗，停止實驗被拒絕（`ROLE_REQUIRED`），也沒有取得節點憑證。
  - 撤銷測試者的節點與操作憑證後，`doctor` 在 session 項目失敗，`remote` 回傳 `UNAUTHENTICATED`。
- **尚未完成**：與測試者透過 Tailscale 的兩台實體電腦驗收（需要測試者配合）；每個 Worker 綁定一張 GPU；使用者、專案與隔離（完整版）。

### 測試者使用前的穩定性
- **版本相容檢查**：`src/tableforge/version.py` 定義協定版本（目前 1）與可接受的最低版本。`GetClusterInfo` 回傳 Coordinator 的程式版本與協定版本；`join`、Worker 啟動與每次重新連線、`doctor --coordinator`（新增 `version` 項目）都會檢查。不相容時停止並提示更新哪一邊；只是程式版本不同則顯示警告。
- **斷線自動恢復**：
  - 錯誤分成三類：
    - 暫時性（`UNAVAILABLE`、`DEADLINE_EXCEEDED` 等）：Worker 不結束，以 1、2、4……最多 60 秒的間隔重試，只記錄斷線與恢復兩次 log。
    - lease 已失效：斷線期間工作被交給別人，記錄 `lease_lost` 後繼續。
    - 必須由人處理（憑證被撤銷、版本不相容）：停止並說明怎麼做，不顯示 traceback。
  - Worker 在 Coordinator 還沒啟動前就可以先開，會等到連得上為止。
- **節點憑證自動續期**：`coordinator.json` 記錄憑證的核發與到期時間。剩不到 1/4 有效期時，Worker 自動換新並寫回。同一台電腦多個 Worker 共用同一個檔案時，以跨程序檔案鎖確保只換一次，其他 Worker 讀到新憑證後直接沿用。舊格式（沒有到期時間）的憑證會在第一次檢查時換新。
- **CI**：`.github/workflows/ci.yml`，四個工作：
  - Lint 與型別檢查，並確認產生的 gRPC 程式碼與 proto 一致。
  - Linux 完整測試：PostgreSQL 17 與 RustFS 服務容器。
  - Windows CPU 測試。
  - 建置 Coordinator 與 CPU Worker 映像並做簡單檢查。

  CI 裝 CPU 版 PyTorch，並跳過 lock 檔中的 CUDA 套件，節省時間。README 加上 CI 狀態徽章。
  - CI 第一次執行就抓到兩個本機測試沒發現的問題，已修正：
    - Windows 專用的記憶體查詢在 Linux 上無法通過型別檢查：合併為單一函式，並以平台判斷隔開。
    - 多個程序同時建立 node-id 時，其中一個可能讀到寫到一半的檔案：改成先寫入暫存檔，再以原子性的 hard link 發布，並新增 8 個程序同時建立的測試。
- **文件整理**：根目錄的 `test.md`、`test2.md`、`test3.md` 搬到 `docs/concepts/`，改為有意義的檔名並加上說明。
- 驗收：
  - 單元測試：退避間隔與上限、只記錄狀態轉換、lease 失效後繼續、憑證撤銷時停止、未預期錯誤照常拋出、版本相容規則、只在最後 1/4 有效期續期、第二個 Worker 沿用第一個換好的憑證。
  - 多程序測試：Worker 執行 12 個 simulation Job 期間，強制關閉並重啟 Coordinator，再關閉 30 秒後重啟。Worker 全程沒有結束，12 個 Job 全部成功，log 有斷線與恢復的紀錄，沒有 traceback。
  - 被撤銷的節點啟動 Worker 時，提示「請要新的邀請碼」並結束。

### 6-Max
- **新引擎**：`src/tableforge/poker/table.py` 的 `TableHand`，支援 2–6 人。原本的 `HeadsUpHand` 沒有修改，NFSP、League 與既有評估繼續使用它。
  - 位置：小盲在按鈕左邊、大盲在小盲左邊；翻牌前由大盲左邊先行動，翻牌後由按鈕左邊先行動。兩人時沿用 Heads-Up 慣例（按鈕下小盲、翻牌前先行動）。
  - 下注：min-raise、大盲在全員跟注後可以再加注；短碼 all-in 不足一個完整加注時，不重開已行動玩家的加注權。
  - 結算：依投入金額分層建立主池與 side pot，每一層只有投入到該層、且未蓋牌的玩家可以爭取；只有一人投入的部分視為未被跟注，退還給該玩家。平分時零頭依座位順序（按鈕左邊開始）一枚一枚發給贏家。
  - 發牌：從小盲開始一次一張，每人兩輪。
  - `TableObservation` 列出所有座位的籌碼、投入與狀態，但只有自己的手牌。
- **兩人時與 Heads-Up 引擎一致**：同樣的 seed 與行動下，手牌、行動紀錄、最終籌碼逐手相同（400 組隨機案例）。唯一刻意的差異：盲注玩家 all-in 且金額小於另一位盲注時，Heads-Up 引擎直接攤牌，新引擎讓另一位玩家選擇跟注或蓋牌。
- **Rule Bots**：四種 Bot 同時接受兩種 Observation，不需要另外寫多人版本。
- **Simulation**：`SimulationJobPayload` 新增 `players`（預設 2，只有不是 2 時才寫入 JSON，舊的 Job 內容不變）。多人時由 Worker 改用新引擎，結果多了 `players` 與 `side_pots`。新增 preset `6max-rule-smoke`（1,200 手）與 `6max-rule-soak`（100 萬手）；NFSP 類實驗設定多於 2 人會被拒絕。
- **評估**：`src/tableforge/poker/table_play.py` 的 `evaluate_table`。每一手牌以相同的牌與按鈕位置重播 N 次，讓受測者輪流坐每個座位，以減少運氣造成的變異；回報整體 bb/100 與 95% 信賴區間，也分別回報每個座位的結果。`evaluate_against_rule_bots` 讓受測者分別對上整桌的每一種 Rule Bot。
- 驗收：
  - 單元測試：位置與行動順序、大盲的加注選擇、全部蓋牌、短碼 all-in 不重開加注權、非法行動、三人 all-in 產生主池與 side pot、未被跟注的籌碼退還、平分零頭、資訊隔離、17 張牌不重複。
  - Property tests（Hypothesis）：隨機 2–6 人、隨機籌碼與按鈕，資金守恆、每一池的贏家都有資格且沒蓋牌、蓋牌玩家拿不到籌碼、重播結果相同。
  - **2–6 人各 100 萬手 soak**，每手每個座位的籌碼隨機，確保經常出現 side pot，零錯誤：

    | 人數 | 攤牌 | 蓋牌結束 | side pot | 手/秒 |
    |---|---|---|---|---|
    | 2 | 621,461 | 378,539 | 0 | 4,830 |
    | 3 | 869,623 | 130,377 | 252,844 | 3,017 |
    | 4 | 980,535 | 19,465 | 667,414 | 2,280 |
    | 5 | 1,000,000 | 0 | 1,182,248 | 1,841 |
    | 6 | 1,000,000 | 0 | 1,783,828 | 1,534 |

    5–6 人時桌上幾乎一定有 CallBot，所以每手都到攤牌；蓋牌結束的情況由 2–4 人與 property tests 涵蓋。執行方式：`uv run python -m tableforge.poker.soak_cli --players 6 --hands 1000000`。
- **尚未做**：NFSP 與 League 多人化（等模型強度問題解決後）、多人的 Rule Bot 策略（目前四種 Bot 的判斷都沒有考慮人數與位置）。

### W1 Coordinator 網頁介面
- **HTTP API**：`src/tableforge/web/app.py`（FastAPI），由 `coordinator serve` 在同一個程序內以背景執行緒啟動（uvicorn），預設 `127.0.0.1:8080`，可用 `--http-listen` 或 `coordinator.http_listen` 更改，設成空字串則關閉。直接呼叫與 gRPC `ControlService` 相同的 Application 服務，權限規則也相同。
  - 資源：總覽、節點（含 Worker 編號）、preset、實驗（列表、明細、建立、開始、停止、模擬結果彙總、評估結果）、League 矩陣、checkpoint、事件、診斷。
  - 管理者專用：邀請碼列表、產生、撤銷；操作憑證列表、撤銷；撤銷節點。
  - 即時更新：`/api/events/stream`（Server-Sent Events）。伺服器每秒讀一次 outbox 事件表，每 15 秒送一次 keep-alive；每條連線最多 5 分鐘，之後瀏覽器從上次的事件編號自動重連，不會漏掉事件。頁面收到事件後會重新載入資料（多個事件合併成一次）。
  - 前端的建置結果由同一個 HTTP 伺服器提供；網址找不到檔案時回傳 `index.html`（讓網頁內的網址可以直接開啟），並防止用 `../` 讀到 static 以外的檔案。
- **權限與登入**：
  - 新增 `admin` 角色，高於 `operator`。只能在 Coordinator 電腦上用 `tableforge coordinator admin` 產生，邀請碼不能授予（`InviteScope` 與網頁 API 都會拒絕），因此外流的邀請碼不會變成管理者權限。schema migration、GC、憑證變更等仍然只能在 Coordinator 電腦上用指令執行。
  - 登入時貼上邀請碼或操作憑證。邀請碼只接受「只含 viewer／operator 權限」的；含 Worker 節點的邀請碼會被拒絕且不會用掉，讓它仍可以拿去 `join`。
  - 登入後發給瀏覽器 HttpOnly、SameSite=Strict 的 cookie（HTTPS 時加上 Secure）。session 存在資料庫的新表 `web_sessions`（schema v5，SQLite 與 PostgreSQL 都有，`metadata copy-from-sqlite` 也會複製），最多 30 天，而且不會比原本的操作憑證更晚到期。撤銷操作憑證時會一併刪除它的網頁 session。
  - 會修改資料的請求都要帶上 CSRF token（每個 session 一個）。腳本可以改用 `Authorization: Bearer tfo_...`，不需要 CSRF。
  - 登入失敗每個來源 5 分鐘最多 10 次，之後回 429。
  - 所有回應都加上安全標頭：CSP（只允許同網域）、禁止被嵌入其他網頁、nosniff、不送 Referrer；API 回應不快取。
  - HTTPS：建議用 `tailscale serve` 把 `127.0.0.1:8080` 以 HTTPS 分享到 tailnet；也可以設定 `coordinator.http_certificate` 與 `coordinator.http_private_key` 讓網頁直接使用 TLS。
- **邀請碼核發共用**：`transport/invite_issue.py`，CLI 的 `coordinator invite create` 與網頁共用同一段程式，檢查憑證是否涵蓋位址、storage 位址警告都一致。
- **前端**：`web/`，React 19 + TypeScript 7 + Vite 8，嚴格型別檢查。建置結果放在 `src/tableforge/web/static/`（約 94 KB gzip）並 commit 進 repo，Python 套件會包含這些檔案，使用者不需要安裝 Node.js。
  - 頁面：登入、總覽、節點、實驗（建立並開始、停止）、實驗明細（設定、Job、模擬結果的各座位輸贏長條圖、評估結果）、League 對戰矩陣熱圖（綠色列方贏、紅色列方輸，信賴區間跨過 0 的格子加上斜線，滑鼠移上去顯示信賴區間與手數）、Checkpoint、事件（可篩選）、診斷、邀請與憑證（管理者）。
  - 繁體中文為主，另有英文（未翻譯的字自動用中文），選擇會記在瀏覽器。支援深色模式與窄螢幕。
  - 依角色顯示：檢視者看不到建立、停止按鈕，只有管理者看得到邀請頁與撤銷按鈕；伺服器端仍會檢查權限。
- **Docker**：Coordinator 容器開放 8080，compose 對應到主機的 `127.0.0.1:8080`（`WEB_HOST_PORT`）。即使設定了 `BIND_ADDRESS`，網頁仍只開在本機，要分享請用 `tailscale serve`。
- **CI**：新增「Web UI build is committed」：重新 `npm ci && npm run build`，確認結果與 commit 的檔案完全相同。本機連續建置兩次，檔案 hash 相同。
- 驗收：
  - API 測試 19 個：未登入一律 401、cookie 屬性、邀請碼登入只能一次、含節點的邀請碼被拒且不會用掉、登入失敗次數限制、沒有 CSRF token 被拒、Bearer 不需要 CSRF、檢視者不能修改、實驗完整流程、錯誤回應格式、各唯讀頁面、管理者的邀請與憑證管理（含不能用邀請碼授予 admin、不能撤銷自己）、撤銷憑證後網頁 session 立即失效、session 隨憑證到期、登出、事件串流、static 檔案與 `../` 防護、真實伺服器啟動與關閉。
  - `scripts/web_e2e.py`：以真實 HTTP 伺服器與 Worker 走完 13 個步驟（提供前端、拒絕未登入、管理者產生邀請碼、操作者用邀請碼登入且只能一次、CSRF、事件串流、建立並開始 6 人實驗、收到即時事件、Worker 跑完且各座位輸贏加總為 0、各頁資料、撤銷後立即登出、登出），全部通過，約 4 秒。每一步印一行，並有整體時間上限。
  - 以 Edge 無介面模式實際開啟 10 個頁面並截圖檢查：登入、總覽、節點、實驗列表、實驗明細（6 人結果長條圖）、League 熱圖、事件、邀請與憑證、診斷，以及檢視者看到的實驗頁（沒有建立按鈕，顯示權限說明）。
- **尚未做**：workload 相容性說明（C5 剩餘部分）、訓練曲線。

### W2 測試者端本機控制台
- `tableforge [--data-dir DIR] ui [--port 8765] [--no-browser] [--no-gpu]`：在 `127.0.0.1` 開啟只管理這台電腦的控制台，並自動開啟瀏覽器。測試者不需要記指令就能加入叢集、檢查連線、啟動與停止 Worker、看紀錄。
- **加入**：與 `tableforge join` 共用同一段程式（`transport/join.py` 的 `join_with_code`），檢查項目與錯誤訊息相同。
- **連線檢查**：與 `doctor --coordinator` 相同的 7 項（dns、tcp、tls、cluster、version、session、storage），每項顯示結果、耗時與處理提示。
- **Worker**：每個 Worker 是獨立的 `tableforge worker start --remote` 子程序（`web/local.py` 的 `WorkerManager`），因此 Worker 當掉不會影響控制台，也沿用 W0 的斷線重試與憑證續期。
  - 控制台讀取 Worker 輸出的 JSON log，整理成狀態（啟動中、執行中、等待工作、重新連線中、停止中、已停止、異常結束）、完成的工作數量與最後的問題；紀錄以中文顯示，每個 Worker 保留最近 500 行。
  - 停止時送出與 `Ctrl+C` 相同的訊號（Windows 為 CTRL_BREAK，Worker 新增了對應處理），Worker 做完目前的步驟後正常結束；15 秒內沒有結束才強制終止。
  - 關閉控制台（`Ctrl+C`）時，由它啟動的 Worker 會先全部停止，控制台才結束。最多同時 8 個 Worker。
- **安全**：只開在 127.0.0.1，而且：
  - 每次啟動產生一個隨機 token，只寫在控制台提供的網頁裡；所有 API 都要帶這個 token，因此其他網站即使知道網址也無法操作。
  - 只接受 Host 為 `127.0.0.1` 或 `localhost` 的請求，防止 DNS rebinding。
  - CSP 只允許同網域，並禁止被嵌入其他網頁。
- **前端**：與 Coordinator 網頁共用同一個 Vite 專案、樣式與中英文字串，建置出第二個頁面 `local.html`。Coordinator 網頁不提供這個頁面。
- 驗收：
  - 單元／整合測試 8 個：token 與 Host 檢查、加入前後的狀態、加入錯誤訊息、未加入時不能啟動 Worker、啟動 Worker 後讀到 JSON 與純文字 log 並統計工作數、停止時 Worker 正常結束（exit 0）、關閉時停止全部 Worker、不存在的 Worker。
  - `scripts/local_ui_e2e.py`：啟動真實 Coordinator 與真實的 `tableforge ui` 程序，依序完成加入（重複加入被拒）、7 項連線檢查全部通過、啟動 Worker 並跑完 6 人實驗的 4 個 Job、停止 Worker 並確認 exit 0、再開一個 Worker 後對控制台按 `Ctrl+C`，確認控制台 exit 0、沒有 traceback、Worker 程序也已結束。約 9 秒。
  - 過程中發現並修正：Windows 上對控制台按 `Ctrl+C` 時，uvicorn 會用中斷訊號直接結束程序，由控制台啟動的 Worker 會留在背景繼續執行。改成 uvicorn 在背景執行緒執行、由控制台自己處理 `Ctrl+C`，先停止所有 Worker 再結束。
  - 以 Edge 截圖確認加入前與 Worker 執行中的畫面。

### U1 單一入口：建立或加入叢集
目標：一般使用者只需要知道 `tableforge ui`。建立者和加入者都從這裡開始，內部的 Coordinator、Worker 程序與 port 不需要理解。
- **資料位置**：預設放在每個使用者的應用程式資料夾（Windows `%LOCALAPPDATA%\TableForge`，macOS `~/Library/Application Support/TableForge`，Linux `~/.local/share/tableforge`），可用 `--home` 或 `TABLEFORGE_HOME` 更改。裡面分成 `coordinator\`（只有建立者有）、`node\`（這台電腦的 Worker 節點）、`operator\`（叢集管理憑證）。
- **第一次開啟**：只顯示「建立新的叢集」與「加入現有叢集」兩個選項。
- **建立叢集**（`web/cluster.py` 的 `create_cluster`）：
  - 偵測 Tailscale 位址（`tailscale ip -4`），讓使用者選「Tailscale 上的其他人（建議）」或「只有這台電腦」。沒有 Tailscale 時第一個選項不能選，並說明怎麼安裝。
  - 自動產生涵蓋該位址的憑證、建立叢集 ID、建立第一個管理者憑證（直接存在 `operator\`，使用者不必複製）、啟動背景叢集服務，再在內部產生一次性邀請碼讓這台電腦以 Worker 身分加入。完成後直接進入管理畫面。
  - 已經建立過、已經加入別的叢集、或 7443 port 被佔用時會拒絕並說明原因。
- **背景叢集服務**（`CoordinatorSupervisor`）：
  - 以獨立的背景程序執行 `coordinator serve`（Windows 上不開視窗），log 寫到 `coordinator\coordinator.log`，啟動資訊記在 `coordinator\service.json`。
  - 關掉 TableForge 頁面或按 `Ctrl+C` 後服務繼續執行，其他人的 Worker 不受影響；再開 `tableforge ui` 時會找到原本的服務直接接上。服務已停止（例如重開機）時會自動重新啟動。
  - 頁面上顯示服務狀態與其他人的連線位址，可以停止或啟動。停止時在資料目錄放一個停止檔，`coordinator serve` 看到後正常結束（Windows 背景程序收不到 `Ctrl+C` 訊號，所以改用這個方式），20 秒內沒有結束才強制終止。
  - 啟動失敗時，錯誤訊息會附上 log 最後幾行。
- **加入叢集**：沿用 W2（貼邀請碼、連線檢查、啟動 Worker）。
- 頁面上不再顯示資料目錄、叢集 ID、連線位址等細節，改放在「詳細資訊」裡，需要時再展開。文字也把「Coordinator」「控制台」改成「叢集」「TableForge」。
- `tableforge ui` 不再使用 `--data-dir`，改用 `--home`。
- 驗收：`scripts/create_cluster_e2e.py`，以真實的 `tableforge ui` 程序從空目錄開始，9 個步驟全部通過，約 16 秒：
  1. 第一次開啟時未建立也未加入。
  2. 建立叢集後服務執行中、這台電腦已加入、取得管理者權限；再建立一次會被拒絕。
  3. 這台電腦的 Worker 跑完 6 人實驗。
  4. 關掉 UI 後服務程序仍在，而且接受連線。
  5. 重開 UI 時接上原本的服務，沒有重新啟動。
  6. 從 UI 停止服務，log 顯示正常結束。
  7. 重開 UI 會自動重新啟動已停止的服務。
  8. 重新啟動後資料都在，連線檢查全部通過。
  9. 停止服務並關閉 UI，沒有殘留程序。
  - 以 Edge 截圖確認第一次開啟與建立後的畫面。

### U2 `tableforge ui` 內的叢集管理
- **同一套頁面、同一份資料**：新增 `web/views.py` 的 `ClusterViews`，把總覽、節點、preset、實驗（列表與明細、模擬結果彙總）、League 矩陣、checkpoint、事件、診斷、邀請碼、操作憑證，以及建立／開始／停止實驗、產生／撤銷邀請碼、撤銷憑證與節點，都集中在一處。
  - Coordinator 的 HTTP API 改為呼叫它（網址與回應格式不變，W1 的測試全部照舊通過）。
  - gRPC `ControlService` 新增 `Query` 與 `Command` 兩個 RPC（proto 只新增，舊版照常運作），回傳與 HTTP API 相同的 JSON。每個資源與動作需要的角色集中定義在 `QUERY_ROLES`、`COMMAND_ROLES`，兩條路徑共用。
- **`tableforge ui` 顯示叢集管理**：本機伺服器用這台電腦存的操作憑證，透過 gRPC 呼叫 Coordinator（`/api/cluster/...`）。建立者連自己的叢集，加入者連別人的，程式相同，只差權限。
  - 前端在本機模式會把所有頁面的請求改走 `/api/cluster/...`，即時更新改成每 2 秒輪詢一次新事件（Coordinator 可能在另一台電腦）。
  - 側邊欄第一項是「這台電腦」（叢集服務、Worker、連線檢查），接著依權限顯示總覽、節點、實驗、Checkpoint、事件、診斷；管理者另有「邀請與憑證」。沒有管理權限的加入者只看到「這台電腦」。
- **邀請碼**：
  - 一個邀請碼可以同時給「提供運算」與管理權限；加入時兩者一起生效（`tableforge ui` 的加入流程本來就同時存下兩種憑證）。
  - 連線位址自動使用建立叢集時選的位址（憑證中第一個非本機位址），收在「進階」裡，一般不需要改。
  - **admin 邀請碼**：畫面上必須勾選確認，有效時間最多 1 小時（網頁、gRPC、CLI `--confirm-admin` 都強制檢查）；使用時記錄 `ADMIN_INVITE_REDEEMED` 事件（邀請碼、備註、新的操作憑證 ID），事件頁看得到。
- 驗收：
  - 新增測試：admin 邀請碼需要確認且不超過 1 小時、使用時留下事件；gRPC `Query`/`Command` 依角色放行或拒絕（viewer 不能建立實驗、不能看邀請碼，未確認的 admin 邀請碼被拒，找不到的實驗回 NOT_FOUND）。
  - `scripts/cluster_ui_e2e.py`：以四個真實的 `tableforge ui` 程序（建立者、成員、檢視者、代理管理者）走完 11 個步驟，約 20 秒：建立叢集並取得管理者身分 → 建立者看得到管理資料與自動帶入的位址 → 產生 Worker＋操作者、檢視者、admin 邀請碼（未確認的 admin 被拒）→ 成員加入後取得操作者身分 → 成員建立並開始 6 人實驗、由自己的 Worker 跑完 4 個 Job → 建立者看得到成員的節點、實驗與事件 → 操作者不能用管理者功能 → 檢視者只能讀 → admin 邀請碼使用時留下事件 → 建立者撤銷成員的權限後成員立即失去管理權限 → 停止服務並關閉所有 UI。
  - 四個端對端腳本（`cluster_ui_e2e`、`create_cluster_e2e`、`local_ui_e2e`、`web_e2e`）發生失敗或逾時時，都會先停止它們啟動的程序與叢集服務再結束（先前一次失敗曾留下佔用 7443 的背景服務，已修正並以強制逾時驗證不再殘留）。
  - 以 Edge 截圖確認建立者的「這台電腦」、總覽、邀請與憑證，成員的實驗明細，以及檢視者的實驗列表。

### U3 單一入口完成
- **一套 UI、兩種開啟方式**：前端只有一套頁面。平常從 `tableforge ui` 開啟（本機模式）；給不想安裝 TableForge 的人，才由叢集服務提供同一套頁面（瀏覽器模式，以邀請碼登入）。
- **瀏覽器網頁預設關閉**：`tableforge ui` 建立的叢集服務不開 8080。建立者在「這台電腦」→「叢集服務」→「進階：給只用瀏覽器的人」按一下即可開啟或關閉（`CoordinatorSupervisor.set_web`），設定存在 `service.json`，重新開機後照舊。開關時叢集服務會重新啟動幾秒，Worker 自動重新連上。網頁只開在 127.0.0.1，畫面上附 `tailscale serve` 指令與複製按鈕，分享時一律走 HTTPS。直接執行 `coordinator serve` 的進階使用者行為不變。
- **文件**：
  - `docs/tester-guide.md` 改寫為「使用指南」，建立者與加入者都只介紹 `tableforge ui`：安裝、建立叢集、邀請別人（含權限說明與 admin 邀請碼的限制）、加入、建立實驗、給只用瀏覽器的人、更新、常見問題、資料位置。
  - 新增 `docs/advanced.md`，收錄指令版的建立與加入、`remote` 指令、設定、PostgreSQL、S3、Docker 與指令一覽。
  - README 精簡為四行指令開始使用，其他內容連到使用指南與進階文件；開發段落列出四個端對端腳本。
- 驗收：`scripts/create_cluster_e2e.py` 新增兩個步驟（共 11 步，約 26 秒）：確認網頁預設關閉 → 從 UI 開啟後，以瀏覽器方式用檢視者邀請碼登入並看到實驗、這台電腦仍是 Worker → 關閉後 8080 不再接受連線。

### T1 6 人錦標賽環境
規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 4 節。
- **BB ante**：`TableHand` 新增選填的 `big_blind_ante`。由大盲位代付，盲注優先；是主池裡的 dead money，不算下注、不影響跟注金額，這手留在桌上的人都能贏。不傳時行為與之前完全相同（測試確認同 seed 結果一致）。
- **設定**（`poker/tournament.py` 的 `TournamentConfig`，可存成 JSON）：人數、`starting_stack_bb`、盲注表（每級 SB、BB、ante、維持手數，最後一級一直重複）、payout 比例、buy-in、名次判定器。金額以 buy-in 為單位。
  - `standard_levels()`：從 10/20 開始，每級約增加 25%，BB ante 等於一個 BB，預設每 10 手升一級。
  - `preset_config(stack_bb, payout)`：payout 有 `65-35`、`50-30-20`、`winner-take-all`。
- **引擎**（`Tournament`）：每手用 `TableHand` 打，籌碼跨手保留，每手結束後籌碼為 0 的人淘汰；按鈕移到下一個還有籌碼的人（第一版不做 dead button）；起始按鈕由 seed 決定；每手牌由 `錦標賽 seed + 手數` 決定，重播完全相同。每手結束都檢查整場籌碼守恆。
- **同手多人淘汰**：名次判定器可替換，預設 `stack_at_hand_start`：那手開始前籌碼多的名次較好；籌碼相同時並列，平分他們所佔名次的獎金總和。
- **結果**：每個人的名次、名次獎金、bounty（T1 為 0）、最終結果（= 名次獎金 + bounty − buy-in），以及淘汰順序。選擇記錄每手時，另外有盲注、按鈕、每手前後籌碼、淘汰者與 ICM 權益。
- **ICM telemetry**：Malmuth-Harville 算法，每手開始與結束各算一次，**只記錄，不作訓練獎勵**，也不保證逐手加總等於最終結果。
- **錦標賽用的對手**：`HeuristicPushFoldBot`（`agents/rule/tournament.py`）。翻牌前只全下或蓋牌，門檻依籌碼 BB 數、後面還有幾人、Chen 分數決定；被加注時用較嚴的門檻跟注；翻牌後只過牌或跟注。這是規則近似，不是 Nash。預設參數是在混合 Rule Bot 對手中測試幾組後選的中間值，避免過度貼合單一對手。25 BB、50 BB 時它仍只會全下或蓋牌，只當參考對手。
- **Tournament benchmark**（`poker/tournament_benchmark.py`）：同一組 seed 讓受測者輪流坐 6 個座位，以每個 seed 的平均當一筆觀測值計算 95% 信賴區間。指標：Prize EV、Bounty EV、Total EV、ROI、平均名次、ITM%、各名次比例、每場平均手數。每個 Job 輸出原始樣本（`TournamentSamples`），多個 Job 的樣本可以合併後再算指標。
- **驗收場數（已量測）**：受測者 `HeuristicPushFoldBot`、對手為混合 Rule Bot、10 BB、65/35 時，一次觀測值的 ROI 標準差約 0.65 buy-in（輪流座位）或 1.42（不輪流）。ROI 95% 信賴區間半寬 ±10%、±5%、±2% 分別約需 1,000、3,900、24,000 場。內建 preset 用 500 組 seed × 6 座位 = 3,000 場，約 ±5%。
- **平台**：
  - 新增 Job 類型 `tableforge.v1.TournamentJobPayload`（JSON payload，不需要改 proto）與 workload `tournament-rule-benchmark`（需要 simulation 角色，不需要 PyTorch，所有 Worker 都能跑）。
  - 新增實驗類型 `tournament_benchmark` 與 6 個 preset：`6max-sng-10bb`、`6max-sng-25bb`、`6max-sng-50bb`（65/35），以及各自的 `-top3`（50/30/20）版本；每個 preset 分成 10 個 Job，每個 Job 50 組 seed。
  - 實驗明細新增「錦標賽結果」卡片：各指標的平均與 95% 信賴區間（不含 0 時以顏色標示）、各名次比例長條圖，以及受測者、對手、起始籌碼、獎金結構與場數。
- 驗收：
  - 單元與性質測試 19 個：ante 規則、設定驗證與 JSON 來回、起始籌碼可設定（10、25、50、7.5 BB）、盲注依手數升級、同手淘汰的排序與並列、並列時平分獎金、ICM 已知值、150 組隨機錦標賽（2–6 人、3–25 BB、三種 payout、有無 ante）的不變量（名次、獎金總額、結果加總為 0、淘汰順序完整、每手籌碼守恆、盲注等級、ICM 總和等於剩餘獎金）、同 seed 重播相同、按鈕跳過已淘汰的人、`HeuristicPushFoldBot` 翻牌前只全下或蓋牌、benchmark 指標（相同對手時 Total EV 為 0）。
  - 整合測試 3 個：payload 驗證、分開執行的 Job 合併後與一次執行結果完全相同、實驗從建立到 Worker 執行再到合併結果。
  - **10 萬場錦標賽 soak**（`uv run python -m tableforge.poker.tournament_soak --tournaments 100000 --workers 16`，10/25/50 BB × 兩種 payout 輪流、混合 Rule Bot），零錯誤：共 457 萬手，每場平均 45.7 手、最長 189 手，95,583 次同手多人淘汰，16 個程序 66 秒（約 1,500 場/秒）。
  - 實際在 `tableforge ui` 建立 `6max-sng-10bb`：10 個 Job、3,000 場約 26 秒完成，`HeuristicPushFoldBot` 對混合 Rule Bot 的 ROI 為 +14.6%（95% 信賴區間 +9.5% 至 +19.8%），ITM 44.3%；並截圖確認錦標賽結果卡片。
- **尚未做**：PKO bounty（T2）；dead button；多桌。

### 背景程序不再跳出視窗
- **問題**：從沒有主控台的程序啟動 `.venv\Scripts\python.exe` 時會跳出黑色視窗。uv 建立的 `.venv\Scripts\python.exe` 是轉接程式，會再啟動真正的 Python；父程序用 `DETACHED_PROCESS`（或同時設 `DETACHED_PROCESS` 與 `CREATE_NO_WINDOW`，此時 Windows 會忽略後者）時，真正的 Python 沒有主控台，Windows 就替它開新視窗。
- **修正**：`tableforge/processes.py` 統一提供旗標：背景程序用 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`（隱藏的主控台，轉接程式的子程序也共用），短指令（`taskkill`、`nvidia-smi`、`tailscale`）用 `CREATE_NO_WINDOW`。叢集服務、`tableforge ui` 的 Worker 與這些短指令都改用它。
- 隱藏主控台的 Worker 收不到 `tableforge ui` 送的 Ctrl+Break，因此改用停止檔：`tableforge ui` 啟動 Worker 時以環境變數 `TABLEFORGE_STOP_FILE` 指定一個檔案，停止時建立它，Worker 看到後完成目前步驟並正常結束（結束碼 0）。Linux／macOS 仍送 SIGINT。
- 驗收：
  - 實測比較：用舊旗標啟動 `.venv\Scripts\python.exe` 出現 1 個新視窗，新旗標 0 個。
  - 在 `tableforge ui` 建立叢集、開 2 個 Worker、停止 Worker、停止叢集服務，全程監看新出現的視窗：0 個；Worker 都以結束碼 0 停止。
  - 新增測試 2 個（旗標不含 `DETACHED_PROCESS`、停止檔會讓 Worker 停止）；三個端對端腳本（`local_ui_e2e`、`create_cluster_e2e`、`cluster_ui_e2e`）通過。

### R1 研究工具整理與本機模型庫
- **`capability/cpu_topology.py`**：依 L3 快取群組找出各 CCD（Windows `GetLogicalProcessorInformationEx`），提供 `spread_masks()` 與設定／讀取處理器相依性；其他系統回傳單一群組、不做任何事。
- **`scripts/sweep.py`**：研究用參數掃描，取代先前放在暫存資料夾的啟動器與彙整腳本。`start` 在背景啟動多組設定 × seed（不開視窗、低於一般優先權、輪流綁到各 CCD，連同 `.venv` 轉接程式啟動的真正 Python 一起設定），`status`、`summary`（HU 與錦標賽兩種格式，標示每個 seed 是否都顯著）、`stop`。實測兩個程序分別在 CCD0 與 CCD1、沒有視窗，彙整結果與先前的報告一致。
- **本機模型庫**（`tableforge/models.py`、`tableforge models list/add/remove`）：放在資料位置的 `models\`，每個模型含 `checkpoint.zip` 與 `model.json`（演算法、遊戲、訓練設定、評估結果、sha256），讀取時檢查 sha256。已匯入 T3 驗收的 5 個模型 `pko-10bb-nfsp-s1`、`-s11` 到 `-s14`（各約 21 MB，附完整訓練設定、6,000 場評估結果與訓練曲線）。
- `scripts/nfsp_probe.py` 新增 `--rl-capacity`、`--sl-capacity`；`scripts/tournament_probe.py` 新增 `--eval-seed-start`、`--greedy`、`--save`。
- 研究期間的暫存檔由約 320 MB 清到 0.2 MB，只保留各批次的結果記錄。
- 測試 2 個（模型庫的新增、列出、讀取、損壞偵測、ID 驗證、取代與刪除）。

### R2 Worker 平均分配到各 CCD
- **問題**：在多 CCD 的 Windows 電腦（例如 Ryzen 9 9950X3D）上，Windows 會把少數幾個忙碌的單執行緒程序集中排在同一個 CCD，造成局部高溫（使用者以 HWiNFO 觀察到單一 CCD 約 98°C、另一個 70–80°C；平均分散時總功耗較高但最高溫較低）。
- **做法**：`tableforge ui` 啟動 Worker 時，若 CPU 有多個 L3 快取群組，就把 Worker 綁到目前執行中 Worker 最少的那個 CCD（`cpu_topology.spread_masks`）。`.venv\Scripts\python.exe` 是轉接程式，真正的 Python 是它的子程序，因此兩者都會設定（以 Toolhelp 快照找子程序，不開視窗）。
- 「這台電腦」頁的 Worker 卡片有開關（預設開啟，設定存在資料位置），Worker 列表顯示每個 Worker 在哪個 CCD；只有一個 L3 群組的 CPU 與非 Windows 系統不顯示、不做任何事。
- 驗收：
  - 測試 2 個（以假的 CPU 群組執行，Linux CI 也會跑）：依序分配 0、1、0；停止一個後新 Worker 補到較空的群組；關閉後不再綁定且設定會保存；單一群組時開關不可用。
  - 實機：在 `tableforge ui` 建立叢集並開 4 個 Worker，每個 Worker 的轉接程式與 Python 程序都綁在指定的 CCD（CCD0、CCD1、CCD0、CCD1）。

### D1a 權限改以 scope 表示（完成）
- 規格見 [architecture/07-multi-party-and-trust.md](architecture/07-multi-party-and-trust.md) 4.2.1。八個獨立的 scope：`experiment:read`、`experiment:control`、`model:read`、`model:download`、`model:manage`、`analysis:run`、`play:remote`、`admin:manage`；角色（viewer、operator、model_recipient、admin）只是常用組合。
- 支援使用者要求的兩種情況：「可以看、可以下載模型，但不能操作實驗、不能遠端對打」（model_recipient 或自訂），以及「可以操作實驗、可以遠端對打，但不能下載模型」（operator）。
- 「產生重播、策略表」需要 `analysis:run`，查看已產生的結果只需要 `model:read`；「存成模型」需要 `model:manage`，刪除模型需要 `admin:manage`。
- 資料庫 schema 6：`operators.scopes` 欄位，空值代表角色的 scope，升級前的憑證權限不變（測試確認）。
- 「邀請與憑證」頁可以選角色或逐項勾選 scope，列表顯示每個邀請碼與憑證的 scope，管理者可以直接修改；CLI `coordinator invite create --scopes model:read,model:download`。
- 驗收：
  - 每一個查詢與指令都測試「少了它需要的 scope 就被拒絕、只有那個 scope 就通過權限檢查」（HTTP 30 個組合）；gRPC 另測「只能下載」與「只能操作實驗」兩種憑證。
  - 升級前資料、自訂 scope 邀請碼、修改 scope 與事件、最後一個管理者不能被移除或撤銷。
  - 三個 `tableforge ui` 端對端測試通過；實際建立自訂 scope 與 model_recipient 邀請碼並截圖確認。

### D1b–D1d 和模型對打（完成）
- 規格見 [architecture/07-multi-party-and-trust.md](architecture/07-multi-party-and-trust.md) 4.2.2。
- **引擎**：`Tournament.play_hand` 拆成 `begin_hand`／`finish_hand`，讓對局可以停在真人的回合；原本的行為不變（撲克與錦標賽測試全數通過）。
- **對局**（`learning/play.py`）：`GameSession` 一個座位是真人，其他座位是模型或 Rule Bot；動作有蓋牌、過牌／跟注、加注到指定金額（附最小、半池、一池）、全下，並檢查是否合法。
  - 進行中只看得到自己的手牌，攤牌時才看得到對手的牌；模型的決策說明不隨動作送出。
  - 提示預設關閉；開啟後顯示模型在自己位置的機率與 Q 值。
  - 打完可以轉成重播（和重播 Job 同格式，所有手牌與模型說明都看得到），牌譜隨時可以下載（文字）。
- **遠端對打**（`play:remote`）：在 Coordinator 主機上執行，每場屬於建立它的憑證，閒置 2 小時移除。錦標賽規格預設用第一個模型訓練時的規格，也可以指定。
- **模型下載**（`model:download`）：每塊 2 MB，完成後檢查 sha256；`tableforge ui` 存到 `<home>/downloaded-models`，**本機對打**用這裡的模型，不需要連上叢集。
- **對打頁面**：座位設定（真人、模型與打法、Rule Bot）、人數、起始籌碼、seed、提示開關；牌桌、下注、上一手結果；結束後的名次與轉重播（重用重播頁的元件）；`tableforge ui` 內可以切換遠端或本機，並下載、更新、刪除本機模型。
- 驗收：
  - 單元與整合測試：只看得到自己的牌、動作檢查、提示、整場轉重播與牌譜、對局不能被其他憑證看到、沒有 `play:remote` 被拒絕、分塊下載（5 MB 模型分 3 塊）、竄改 sha256 時不存檔、`model:manage` 不能刪除模型。
  - 實機（真實模型 `pko-10bb-nfsp-c1`、`pko-10bb-nfsp-s12`）：遠端開局約 5.5 秒（載入模型），之後每個動作約 4 ms；下載 31.7 MB 模型 2.1 秒且與叢集檔案完全相同；本機打完一整場並轉成重播；截圖確認頁面。

### S1 ICM Push/Fold Nash baseline（完成）
- 規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 7 節。**是簡化遊戲（只能全下或蓋牌）的均衡，不是完整德州撲克的 Nash 均衡**；UI 與文件一律稱「ICM Push/Fold Nash baseline」或「Push/Fold Nash」。
- 內容：
  - 169×169 起手牌全下勝率表（每組 100,000 次模擬，向量化評估器先與引擎評估器逐一比對）。
  - fictitious play 求解器：2–9 人、ICM 獎金、PKO bounty、全下者與跟注者之間的牌互斥；回報收斂誤差 `gap`。
  - 策略表 Job 另外產生 baseline（25 BB 以內的開局全下與面對全下），UI 可切換「模型／baseline／模型 − baseline」，列出平均差距與差最多的牌；模型大量跛入或小加注時另外提示差距不代表選牌錯誤。
  - Rule Bot `pushfold_nash`（每個決策依當下籌碼、盲注、剩餘獎金與 bounty 求解，全程序共用快取），benchmark preset `6max-sng-10bb-pushfold_nash`、`6max-pko-10bb-pushfold_nash`。
- 驗收：
  - heads-up、贏家全拿時與公開的 push/fold 表一致（3–16 BB 共 6 個籌碼深度，距門檻 12% 以外的 338 個決策最多差 2 個，差在公開表省略的策略空洞）；收斂誤差 < 0.001 buy-in。
  - 6 人 65/35 比贏家全拿跟注更緊；PKO 面對頭上 bounty 較高的全下者跟注更寬（策略表 BB 對 BTN 10 BB：1× bounty 跟 50.4%、3× 跟 54.8% 的起手組合）。
  - 效能：6 人一個情境約 0.1 秒；10 BB PKO 策略表的 baseline 部分約 15 秒、一般 SNG 約 2 秒。
- **目前模型和 baseline 的差距（10 BB PKO，平均策略）**：7 個模型的差距都很大，而且方向一致：
  - 開局：模型很少全下（按鈕 10 BB 全下只佔約 8%，baseline 約 74%），不蓋牌的部分大多是跛入（約 58%）與小加注。依起手組合加權，開局全下比例和 baseline 平均差 55–89 個百分點（籌碼越短差越多）。
  - 面對全下：模型跟注比 baseline 緊很多（BB 對 BTN 10 BB 跟 28%，baseline 約 50%；對頭上 3 倍 bounty 的全下者 baseline 跟約 55%）；平均差 36–60 個百分點。
  - 這表示模型學到的是「跛入／小加注」而不是 push/fold，在 10 BB 時這是否較差要看實戰：P2 的結果顯示最好的模型對 baseline bot 仍然贏錢（見下），所以差距本身不代表模型較弱，但面對全下的跟注明顯過緊，是值得追的方向（S4 可剝削程度會量化）。

### P2 模型比較（完成）
- 規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 8 節。每位參賽者一個 Job（一整列），完成的部分先顯示；排名依對所有對手的平均 ROI，**不提供 Elo 分數**（實測中「名次比對方好」的分數把 ROI 明顯為負的 Tight bot 排第一）。
- 模型依引擎設定分組（修正：`10` 與 `10.0` 視為相同規格，`pko-10bb-platform` 和其他 10 BB 模型同組）。
- 驗收：整合測試（兩個模型加 Tight bot，三個 Job，排名與矩陣完整）；`tableforge ui` 實機 4 個 Worker：策略表加 baseline 30 秒、5 位參賽者 × 20 seed 的比較 179 秒；截圖確認。
- **正式比較**（7 個 10 BB PKO 模型 + ICM Push/Fold Nash baseline bot + hunter；每組 200 seed × 6 座位 = 1,200 場；seed 1,700,000,000 起；9 個程序平行 51 分鐘，其中 baseline bot 那一列 51 分鐘、其他列約 8 分鐘）：

  | 排名 | 參賽者 | 平均 ROI | 95% 信賴區間 |
  |---|---|---|---|
  | 1 | pko-10bb-nfsp-c1 | +12.4% | +9.1% ~ +15.8% |
  | 2 | pko-10bb-nfsp-s12 | +11.0% | +7.7% ~ +14.3% |
  | 3 | ICM Push/Fold Nash baseline bot | +6.9% | +3.4% ~ +10.4% |
  | 4 | pko-10bb-nfsp-s14 | +6.1% | +2.6% ~ +9.5% |
  | 5 | pko-10bb-nfsp-s11 | +5.5% | +1.9% ~ +9.1% |
  | 6 | pko-10bb-nfsp-s1 | +4.5% | +1.4% ~ +7.7% |
  | 7 | pko-10bb-platform | +4.1% | +1.0% ~ +7.2% |
  | 8 | pko-10bb-nfsp-s13 | +3.0% | −0.2% ~ +6.3% |
  | 9 | hunter | −22.6% | −25.6% ~ −19.7% |

  - 平均 ROI 都為正是因為 hunter 在每個人的對手中：扣掉對 hunter 的那一格後，只有 c1 與 s12 明顯為正。
  - 對 baseline bot：c1 +8.8%、s12 +13.4%（兩者 95% 信賴區間下限 > 0），其他模型 −1.9% ~ +3.0%（不顯著）；baseline bot 對 c1 +2.0%、對 platform +4.9%（不顯著）。
  - 和 C 階段的結論一致：c1 與 s12 是目前最強的兩個 10 BB 模型；s12 對 s11、s13 顯著為正，platform 對 c1 顯著為負（−11.9%）。
  - 結果檔在研究結果資料夾（`research-results/p2/comparison.json`）。

### 新格式：9 人桌、下注尺度、多結構訓練（T4、S2、S3；正式訓練完成，驗收比較待做）
- 規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 10 節。使用者決定：先完成程式並用小型實驗驗證，正式訓練（與算力預算）之後再談。
- **T4**：引擎與錦標賽支援 2–9 人，6 人以下不變；位置名稱集中一處（2–6 人與以前相同，7–9 人加入 LJ、UTG1、UTG2）；策略表、對打、練習、重播、模型比較、Push/Fold Nash baseline 都可以 9 人。模型坐到超過 encoder 位置數的牌桌時明確報錯（v2 以前會靜默忽略多出來的對手）。
- **S2**：動作格式 `nlhe-discrete-v2`（翻牌前 2x／2.5x／3x，翻牌後 33／66／100／150% 底池，加上蓋牌、過牌／跟注、全下）；說明器、策略表、練習與牌譜檢討都顯示各尺寸。
- **encoder v3**（`poker-nlhe-vector-v3`）：9 個位置、雙向位置、「蓋得過對手」、錦標賽結構特徵（起始 BB、盲注速度、ante、付錢名次、bounty 規則…）。
- **S3**：`TournamentSampler` 每個訓練 seed 抽一個結構；實驗 `settings.sampler`；preset `mixed-pko-v3-train`、`mixed-pko-v3-smoke`；研究腳本 `tournament_probe.py --v3 --sampler`。
- **相容**：v1／v2 的 checkpoint、經驗資料、Job payload 位元一致（NFSP 指紋測試通過）；新欄位只在不是預設值時寫出。
- 驗收（程式）：
  - 單元測試：v2 動作的各尺寸金額（開局 40/50/60、面對加注到 50 時被限制到合法最小再加注）、翻牌後的比例、v1 動作不變；v3 對 2／6／9 人、有無錦標賽資訊的編碼長度與範圍、不同結構產生不同輸入；sampler 可重現、涵蓋範圍、盲注遞增、獎金名次不超過人數、錯誤設定被拒絕；9 人位置與 4 人相容名稱。
  - 引擎性質測試擴到 9 人（隨機牌局與錦標賽的籌碼守恆、名次與獎金）。
  - 整合測試：v3 學習者在 sampler 下打過 2–9 人每一種牌桌並用到各種加注尺寸；v3 checkpoint 讀寫、v1 checkpoint 照常讀取；`mixed-pko-v3-smoke` 在平台上完整跑完（actor payload 帶 sampler、經驗資料與 checkpoint 是 v3／v2 動作）；錯誤設定被拒絕（v2 encoder 配 sampler、v2 encoder 9 人、未知 sampler 欄位）。
  - 實機：v3 模型在 9 人桌的策略表（160 張表、9 個位置）、重播（說明含 7 種尺寸）、練習、Push/Fold Nash baseline（9 人收斂誤差 0.0005）、模型比較都正常。
- **小型實驗**（每組 2 個 seed、各 20,000 場，6 人 25 BB PKO 對混合 Rule Bot benchmark，每點 1,200 場；6 個程序約 15 分鐘，其中 A 組一個 seed 因 OpenMP 在啟動時設定親和性失敗而沒有跑，非程式問題）：

  | 組 | 20,000 場後平均策略 ROI | 最佳回應 ROI |
  |---|---|---|
  | A：v2 encoder + v1 動作（現行） | −2.4%（1 個 seed） | +19.7% |
  | B：v3 + v2 動作，只訓練 25 BB 6 人 | −0.4% / −0.0% | +40.0% / +57.1% |
  | C：v3 + v2 動作，2–9 人、8–60 BB 各種結構混合 | −1.7% / −2.4% | +37.6% / +44.2% |

  - 新格式可以學習，沒有比現行格式差；20,000 場時平均策略都還在 0 附近（T3 的經驗是需要 10 倍以上的訓練量）。
  - 最佳回應（Q 網路）在新格式明顯較強（+38% ~ +57% 對 +20%），更細的下注尺度給了它更多可利用的選項。
  - 混合結構（C）只有約 1/8 的訓練量落在 6 人 25 BB，在這個 benchmark 上仍和專門訓練的 B 差不多，這是 S3 想要的方向；是否真的「一個模型通吃」要等正式訓練後，在多種規格上和各自的專用模型比較。
  - 結果檔：研究結果資料夾 `research-results/v3-smoke`。
- **正式訓練（訓練完成，驗收比較待做）**：三組：M（v3 + 多結構）、S（v3，只訓練 25 BB 6 人）、R（現行 v2，對照組），每組 3 個 seed、各 200,000 場，用 T3 的設定，但 SL 容量從 1,000,000 降為 400,000（記憶體不夠同時跑；三組設定相同，比較仍公平）。每個程序 3.2–4.2 小時。驗收：S 在 25 BB 顯著勝過 R；M 不輸 S；用模型比較，M 另在 10／50 BB 量測。

  訓練中的 benchmark（6 人 25 BB PKO 對混合 Rule Bot，每點 1,200 場；平均策略 ROI）：

  | 組 | 50,000 | 100,000 | 150,000 | 200,000 場 | 200,000 場的信賴區間 | 最佳回應 |
  |---|---|---|---|---|---|---|
  | M（v3 多結構） | +20% ~ +32% | +30% ~ +44% | +33% ~ +50% | **+27% ~ +36%** | 下限 +16% ~ +24% | +25% ~ +75% |
  | S（v3 只 25 BB） | +17% ~ +27% | +27% ~ +32% | +29% ~ +39% | **+30% ~ +43%** | 下限 +19% ~ +32% | −8% ~ +97% |
  | R（v2 現行） | +18% ~ +28% | +24% ~ +40% | +20% ~ +36% | **+26% ~ +29%** | 下限 +16% ~ +19% | +5% ~ +65% |

  - 三組都明顯賺錢，而且比 T3 在 10 BB 的 +15–21% 高（25 BB 對 Rule Bot 的空間較大）。
  - S 平均略高於 R（+36% 對 +27%），但每組 3 個 seed 的區間重疊，這個 benchmark 還看不出顯著差異；M 和 S 差不多（+33% 對 +36%），M 在這個規格只用到約 1/8 的訓練量。
  - 下一步：用模型比較讓 9 個模型在同一張桌直接對打（每對 200 seed，比 benchmark 更敏感；`scripts/model_matrix.py`），並把 M 拿到 10 BB 與 50 BB 量測；結果出來後判定驗收。原本的模型檔已遺失（見 A1 的「研究結果遺失」），以相同設定重新訓練中，結果放 `H:\tableforge-research\v3-retrain`。

### A0 演算法介面（完成）
- 規格見 [architecture/01-system.md](architecture/01-system.md) 6.2。`learning/algorithms.py` 的 `AlgorithmSpec`（打法、說明器、牌桌玩家、Worker executor、checkpoint schema）與 Coordinator 端的 `ORCHESTRATORS`（每個演算法的輪次流程）；NFSP 是第一個實作。
- 原本寫死 NFSP 的地方都改走介面：模型頁打法、上傳 checkpoint、遠端與本機對打、練習、牌譜檢討、重播與策略表 Job、對手池、模型比較、benchmark 的模型成員、Worker executor 註冊、實驗的學習者 agent id 與存成模型。實驗設定新增 `algorithm`（預設 `nfsp`）。
- **位元一致**：導入介面前用 `scripts/algorithm_baseline.py` 端對端錄下三個小型 NFSP 實驗（錦標賽、含對手池與歷史版本的錦標賽、HU 含 League population，共 20 個 Job）每個 Job 的 payload 與輸出 sha256，以及 checkpoint 與訓練曲線；改完後完全相同。這份紀錄存在 `tests/fixtures/nfsp_fingerprint.json`，由 `tests/integration/test_nfsp_fingerprint.py` 每次比對：開發機（錄製的 Windows）上全部比對；PyTorch 的浮點結果會隨作業系統與 CPU 不同（GitHub 的 Windows runner 在不同區域用不同 CPU，訓練出的權重也不同），所以 CI 與 Linux 只比對 Job 流程（鍵、類型、狀態）與未訓練的 round 0 payload。
- 驗收：單元測試加入一個假的演算法（checkpoint 就是一個 Rule Bot 名稱），不改核心程式就能在模型頁顯示打法、上桌對打、當對手池成員、註冊 Worker executor；兩個演算法不能宣告同一種 Job；不是 NFSP 時對手池與比較 payload 才寫出演算法欄位。

### A1 PPO（程式完成；小型實驗顯示自我對打會循環，下一步 MMD）
- 規格見 [architecture/01-system.md](architecture/01-system.md) 6.3。`learning/ppo.py`（策略與價值網路、GAE、clipped PPO、checkpoint）、`learning/ppo_training.py`（收集經驗與本機訓練迴圈）、`learning/ppo_iterative.py`（平台的 init／actor／learner／match Job）、`learning/ppo_explain.py`（說明器）。
- 透過 A0 介面接上：模型頁、重播、策略表、對打、練習、牌譜檢討、模型比較、對手池都能用 PPO 模型，不用改這些功能的程式。打法：`sample`（依策略機率，預設）、`greedy`（最可能的動作）。
- Preset：`6max-pko-10bb-ppo-train`（和 NFSP 10 BB preset 同樣的錦標賽、對手與 benchmark；每輪 4 個 actor × 250 場，4 個 epoch）、`6max-pko-10bb-ppo-smoke`。研究腳本 `tournament_probe.py --algorithm ppo`。
- 實驗畫面只顯示該演算法用得到的設定（PPO 不顯示 NFSP 的 anticipatory、epsilon、replay 容量；NFSP 不顯示 PPO 的 entropy、GAE λ、clip）。訓練曲線與模型頁的 ROI 依演算法使用預設打法。
- 相容：NFSP 的 Job payload、輸出、checkpoint 與訓練曲線完全不變（NFSP 指紋測試通過）。
- 驗收（程式）：
  - 單元測試：GAE 與手算結果相同（含 λ=1 時等於報酬減價值、episode 之間不互相 bootstrap）；clipped loss 在超過 clip 後不再推動；未結束的 episode 被拒絕；學習會把策略推向有獎勵的動作；checkpoint 讀寫位元一致，且讀回後繼續訓練的結果和原本完全相同；每個座位的決策連續記錄；v3 模型可打 9 人桌並使用 10 種動作；v2 encoder 拒絕 9 人桌；兩種打法都能完成 benchmark；錯誤設定被拒絕。
  - 整合測試：`6max-pko-10bb-ppo-smoke` 在平台上完整跑完（1 個 init、4 個 actor、2 個 learner、2 個 match Job），經驗資料只有 transitions 且最後一筆是 episode 結束，訓練曲線有 `sample`／`greedy` 兩個系列，學習者 agent id 是 `ppo:<實驗 id>`，可存成模型；說明器與兩種打法在牌桌上可用；設定範圍檢查。
- **小型實驗**（10 BB PKO，3 個 seed 各 60,000 場，75% 座位自我對打，用 T3 的混合 Rule Bot benchmark，每點 1,800 場；3 個程序約 12 分鐘）：

  | 場數 | ROI（依機率出手，3 個 seed） | ROI（最可能動作） | 熵 |
  |---|---|---|---|
  | 0 | −20.3% / −16.9% / −11.3% | – | – |
  | 15,000 | **+18.2% / +20.3% / +15.5%**（下限都 > 0） | +14.9% / +15.5% / +19.6% | 約 0.5 |
  | 30,000 | −6.9% / −6.3% / −1.8% | +6.6% / −1.3% / +8.7% | 約 0.9–1.5 |
  | 60,000 | −4.0% / −3.7% / −2.5%（都不顯著） | +13.6% / +5.1% / +11.1% | 約 1.2 |

  - PPO 學得很快：15,000 場就達到 NFSP 在 100,000 場以上才有的 +15–21%（ITM 由 23% 升到約 39%）。
  - 但之後三個 seed 都退回 0 附近，熵回升：典型的自我對打循環（策略在幾種打法之間來回追逐）。這正是下一步 MMD 正則化要解決的問題（把策略拉向一個參考策略，讓自我對打收斂），所以 PPO 暫不正式訓練，先做 MMD，再用同一套成績卡比較 PPO、PPO+MMD 與 NFSP。
  - 15,000 場的模型沒有存下來（腳本只存最後一版）；之後的實驗會在每個檢查點存檔。
  - 結果檔：研究結果資料夾 `research-results/ppo-a1`。
- 記憶體估計：`scripts/sweep.py` 依 `--v3` 用正確的輸入大小（v3 每筆 549 個數字）估計；同時把子程序的 OpenMP 設為單執行緒並關閉它自己的親和性設定，避免啟動時偶發的「Cannot set thread affinity mask」。

### T5 MTT 階段模擬（完成）
- 規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 11 節。使用者決定：權益用數值積分的精確 ICM、賽場上限 1,000 人；階段打到指定人數或本桌只剩 1 人就結束，還在的人拿 ICM 權益；第一版不補位；新增 v4 輸入格式。
- 程式：`poker/mtt_stage.py`（`StageSpec`、內建階段 `bubble`／`itm`／`final_tables`、`FieldState` 模擬其他桌）、`poker/field_icm.py`（全場 ICM 與本桌獎金階梯）、`TournamentConfig.stage`、`TournamentHandInfo.stage`、`learning/encoding_v4.py`、`TournamentSampler.stages`／`stage_share`、`TournamentSpec.stage`、錦標賽表單的「大型 MTT 階段」選項。
- 權益算法研究（使用者選擇前）：數值積分的精確 ICM 和精確遞迴差 < 0.0002 buy-in，1,000 人泡沫期每手 2–20 ms；蒙地卡羅（2,000 次）誤差 3–11% 且每手雜訊 0.1–0.23 buy-in；常態近似與籌碼占比誤差數十到數千 %。
- Preset：`mtt-pko-v4-train`／`-smoke`（v4，v3 的混合加 50% 階段；曲線用泡沫期）、benchmark `mtt-pko-{bubble,itm,final-tables}-{pushfold,pushfold_nash}`。
- 相容：沒有階段的錦標賽、sampler、checkpoint 與 Job payload 完全不變（NFSP 指紋與 PPO checkpoint sha256 相同）。
- 驗收（程式）：全場 ICM 與精確 ICM 一致（10–13 人，誤差 < 0.0005）；全場權益加總等於剩下的獎金；本桌獎金階梯遞減且顯示泡沫；階段錦標賽籌碼與 bounty 守恆、按時結束、淘汰者拿全場名次獎金；只蓋牌的人結果為正；每手權益加總等於最終結果；手牌資訊描述全場；ICM Push/Fold Nash baseline 在泡沫期跟注比 chip EV 時緊 5 個百分點以上（實測 9 人桌 10 BB：跟注 22% 對 34%，5 BB 開局全下 42% 對 62%）；v4 同時打單桌與階段；`mtt-pko-v4-smoke` 與階段 benchmark 在平台上跑完；v3 用於階段訓練被拒絕。
- 小型 benchmark（每個階段 30 seed × 9 座位，對混合 Rule Bot，單位 buy-in；結果是贏或輸的權益，平均約 0）：泡沫期 Push/Fold Nash baseline +0.30、push/fold bot +0.05、tight bot +0.25；剛進錢圈 +0.29／−0.37／+0.62；最後三桌 −0.56／−2.02／−0.26。信賴區間都很寬（最後三桌 ±4 buy-in），還看不出誰較好；需要更多 seed。
- 速度：階段錦標賽每場 0.03–0.1 秒（規則 bot）；Push/Fold Nash baseline 每個階段 30 seed 約 5 分鐘（每個情境要求解）。

### A1b MMD 正則化（程式完成；小型實驗未達驗收）
- 規格見 [architecture/01-system.md](architecture/01-system.md) 6.3。PPO 設定 `magnet_coef`（α）與 `magnet_every`（N）：損失多一項 α·KL(策略‖參考策略)，參考策略每 N 次更新換成當下的策略（使用者選擇的做法）。實驗設定、preset `6max-pko-10bb-mmd-train`（α 0.1、每 5 輪）、`-mmd-smoke`、研究腳本 `--magnet-coef`、`--magnet-every`。研究腳本改為每個檢查點都存檔。
- 相容：α = 0 不建立參考策略；一般 PPO 的 checkpoint 與訓練結果和加入 MMD 前位元一致（同一個訓練的 sha256 前後相同），NFSP 指紋不變。
- 驗收（程式）：KL 對相同策略為 0、只算合法動作；α = 0 與一般 PPO 位元一致；同樣的訓練下 α 大時策略離起點較近；參考策略每 N 次更新才換；MMD checkpoint 讀寫一致、讀回後繼續訓練結果相同；`6max-pko-10bb-mmd-smoke` 在平台上跑完 3 輪（learner 輸出帶參考策略，更新次數 3）。
- 小型實驗：10 BB PKO，每組 3 個 seed、各 60,000 場，每 15,000 場評估並存檔（T3 的混合 Rule Bot benchmark，每點 1,800 場）。驗收：不再先升後跌，60,000 場時 3 個 seed 的 ROI 下限都大於 0。**四組都沒有達到。**

  | 組 | 0 | 15,000 | 30,000 | 45,000 | 60,000 場（依機率出手 ROI，3 個 seed） |
  |---|---|---|---|---|---|
  | P：PPO（學習率 3e-4） | −16% | **+15% ~ +20%** | −7% ~ −2% | −9% ~ −1% | −4% ~ −3% |
  | L：PPO（學習率 1e-4） | −16% | +10% ~ +14% | −6% ~ −1% | −5% ~ −1% | −8% ~ −3% |
  | M1：MMD α 0.05、每 2,500 場換參考 | −16% | +9% ~ +14% | −10% ~ −4% | −6% ~ +2% | −12% ~ −2% |
  | M2：MMD α 0.2、每 10,000 場換參考 | −16% | +4% ~ +8% | −7% ~ 0% | −6% ~ −1% | −7% ~ −2% |

  - 四組都在 15,000 場左右最好，之後退回 0 附近或略負；60,000 場時沒有一組的信賴區間下限大於 0。「最可能動作」打法在 60,000 場是 −3% ~ +14%，同樣不穩。
  - MMD 和降低學習率都只是讓高點變低，沒有阻止回落。KL 只有 0.01–0.08，參考策略每隔幾千場就跟著換掉，拉力太弱；而且退步的方向在三組都一樣，比較像是「75% 自我對打」本身讓策略離開對 Rule Bot 有利的打法（對手越來越像自己），而不是單純的循環。
  - 結果檔：研究結果資料夾 `research-results/mmd-p`、`mmd-m`（每個檢查點都有存檔）。
- **追加實驗：降低自我對打比例**（使用者選擇；同樣設定，學習率 3e-4，只改自我對打比例）：

  | 自我對打比例 | 0 | 15,000 | 30,000 | 45,000 | 60,000 場 |
  |---|---|---|---|---|---|
  | 75%（上面的 P 組） | −16% | +15% ~ +20% | −7% ~ −2% | −9% ~ −1% | −4% ~ −3% |
  | 25% | −16% | +9% ~ +16% | −7% ~ +3% | −5% ~ −3% | −9% ~ −5% |
  | 0%（其他座位全是 Rule Bot） | −16% | −5% ~ −3% | −10% ~ −8% | −10% ~ −4% | −11% ~ −5% |

  - **假設不成立**：自我對打越少結果反而越差；0% 時從來沒有超過 −3%，即使對手完全固定。所以回落不是自我對打造成的。
  - 0% 時每場只有學習者一個座位，每次更新的資料量約是 75% 時的 1/4，可能是學不起來的原因之一；但 75% 時「先升後跌」也說明問題在 PPO 本身的學習（例如更新太大、價值與策略共用梯度裁剪、優勢估計的雜訊），還要進一步診斷。
  - 結果檔：研究結果資料夾 `research-results/ppo-selfplay`（已遺失，見下）。
- **診斷**（使用者選擇）：`PpoAgent.learn` 另外回報每次更新的策略變化量（`approx_kl`）、被 clip 截掉的比例、價值網路的解釋程度（更新前／後）、優勢的標準差、兩個網路的梯度大小（只讀取，不影響訓練：同一個訓練的 checkpoint sha256 與加入前相同，有測試）；研究腳本每個檢查點輸出平均與最大值，並另存每次更新的明細（`<名稱>-updates.jsonl`）。實驗：P 組設定，3 個 seed 各 30,000 場，每 5,000 場評估。

  | 場數 | ROI（3 個 seed） | 策略變化量 KL（平均） | 被截掉比例 | 價值網路解釋程度（更新前→後） |
  |---|---|---|---|---|
  | 5,000 | **+9% ~ +11%** | 0.008 ~ 0.010 | 8% | 0.05 → 0.08 |
  | 10,000 | −9% ~ −2% | 0.017 ~ 0.018 | 17% ~ 19% | 0.07 ~ 0.08 → 0.12 ~ 0.14 |
  | 15,000–30,000 | −9% ~ 0% | 0.008 ~ 0.017 | 3% ~ 16% | 0.05 ~ 0.08 → 0.10 ~ 0.15 |

  - 這次高點出現在 5,000 場（之前每 15,000 場才量一次，所以看到的是 15,000 場），三個 seed 都在 5,000–10,000 場之間退回。
  - **更新幅度不算大**：KL 0.008–0.018，截掉比例最多約 19%，在 PPO 的常見範圍內；退回的那一段 KL 和截掉比例確實最高，但只是一般大小，不像是主因。
  - **主因看起來是價值網路幾乎沒學到東西**：更新前只解釋 5–8% 的回報變化，更新後也只有 10–15%；優勢的標準差約 2.3（獎勵 ÷ 0.3 之後），等於優勢幾乎全是雜訊（全下輸贏的運氣）。PPO 一開始能學到「別做明顯的蠢事」，之後就被雜訊帶著漂移。
  - 另外兩個加重因素：價值損失用 Huber（誤差大於 1 時只學到中位數，NFSP 在 P1-b 遇過同樣的問題），而回報因為除以 0.3 而變大；策略與價值共用同一個梯度上限 0.5，價值網路的梯度（約 0.3）比策略（約 0.5）小。
  - 結果檔：`H:\tableforge-research\ppo-diag`（含每次更新的明細）。修正方式待使用者決定。
- **研究結果遺失**：2026-10-10 05:16 研究結果資料夾（`%TEMP%\opencode\research-results`）裡所有檔案被清空（同一時間、所有子資料夾，資料夾本身還在；看起來是 Windows 暫存檔清理），包括 v3 正式訓練的 9 個模型與各實驗的原始結果。數字都已記在本文件；模型庫（`%LOCALAPPDATA%\TableForge`）沒有受影響。之後研究結果改放 `H:\tableforge-research`（使用者選擇）；v3 的 9 個模型重新訓練中（見上）。

### H1 匯入 GG／Natural8 牌譜（完成，**尚未用真實匯出檔驗證**）
- 依 GG 公開的文字牌譜格式（和 PokerStars 相同的版面）撰寫：`poker/hand_history.py` 讀取錦標賽手牌的標頭（賽事、等級、盲注、ante、日期）、座位與籌碼（含座位上的 bounty 金額）、ante 與盲注、發到的手牌、各街動作、公牌、攤牌、未跟注退回與彩池分配。UI 與文件都標明「尚未用真實牌譜驗證」，`verified_against_real_exports` 為 false。
- **每一手都在引擎上重新打一次**（`TableHand` 加上指定的手牌與公牌、以及「每人都下的 ante」作為死錢）：輪到誰、金額是否合法都由引擎檢查，讀不懂或不合規則的手列在「無法讀取」並附行號，不會靜默略過；引擎算出的彩池分配和牌譜的「collected」不同時也會列出。沒攤牌的對手手牌只用來補位，不會顯示。
- 不支援（會列為無法讀取）：現金桌、straddle、run it twice、死盲注；2–9 人都可以讀。
- `learning/hand_import.py`：轉成重播格式（重播頁的元件直接顯示，可逐手逐動作看）；選了教練模型時，像練習模式一樣檢討有發到手牌的玩家（你）的每個決策，產生同樣的漏洞報告。牌譜沒有完整賽事資訊，教練用自己訓練時的獎金結構與起始籌碼（以 BB 計）估計，bounty 依座位金額換算；超過教練人數的手只顯示不檢討。
- 只在 `tableforge ui`（這台電腦）處理：「牌譜分析」頁可選多個檔案或直接貼上，最多 5 MB、2,000 手；教練要先下載到這台電腦。
- 驗收：
  - 自己依公開格式寫的 5 手樣本（`tests/fixtures/gg_hands.txt`）：讀到 4 手，被截斷的第 5 手列為無法讀取並附行號；每手結束的籌碼正好是下一手開始的籌碼；彩池分配全部和牌譜相同（含未跟注退回）；沒攤牌的手牌不顯示。
  - 錯誤情況：不是 GG 格式、不是錦標賽、標頭沒有等級、加注超過籌碼。
  - 真實教練模型（tiny 模型的整合測試、`pko-10bb-nfsp-c1` 的實機測試）：檢討你的 5 個決策並產生報告。
- **待使用者提供真實樣本後驗收**：GG 實際格式和公開範例可能有差異（例如金額格式、bounty 欄位、名稱中的特殊字元），屆時依真實檔修正並加入測試。

### H2 練習模式（完成）
- 對打時勾「練習模式」並選一個教練模型（任何模型，不一定要坐在牌桌上）。每個決策**之後**才顯示教練的看法：它最常選的動作、各動作機率、Q 值，以及「Q 損失」（依教練的估計，你的選擇比它最好的選擇少多少 buy-in）。開局前不會先給答案；想先看建議另外勾「顯示提示」。
- 漏洞報告：依位置、籌碼深度（0–5、5–10、10–20、20–40、40+ BB）、情境（前面都蓋牌、有人跛入、面對加注、面對再加注、面對全下、翻牌後）與街分組，列出決策數、和教練一致的比例、Q 損失合計、「可能的錯誤」（Q 損失 > 0.02 buy-in 且教練選這個動作的機率 < 10%），以及「和教練不同、損失最多」的 10 個決策；可以下載 JSON。教練的策略（機率）和 Q 值不一定一致，所以和教練一致的決策不列入最差清單。
- **對局存到伺服器**：每個動作後寫入一個 JSON（設定加上你的動作；每個座位對同一個 seed 都是確定的，所以重新播放就回到原狀態）。遠端存在叢集資料位置的 `games`，本機存在 `<home>/local-games`；每人最多保留 20 場，服務重啟或頁面關閉後都能回來繼續或看報告。
- 驗收：
  - 單元測試：練習時開局前沒有提示、決策後有回饋、Q 損失計算（假教練：蓋牌比全下少 0.4）、報告分組與排序、非練習局不能要報告、存檔後新的服務還原出完全相同的畫面與報告、別人看不到、刪除後不再出現、6 人與 9 人的位置名稱。
  - 整合測試：真實 NFSP 教練模型經 HTTP 練習三個決策，清空服務（模擬重啟）後畫面與報告完全相同，存檔檔案存在。
  - 實機（`tableforge ui`，教練 `pko-10bb-nfsp-c1`，對手 s12 兩個與 3 個 Rule Bot）：簡單腳本打完一整場 90 個決策（第 2 名），報告：和教練一致 63%、Q 損失合計 2.75 buy-in、12 個可能的錯誤，翻牌後的損失最多；停止並重新啟動叢集服務後報告完全相同。截圖確認回饋與報告頁面。

### S4 可剝削程度量測（完成）
- 規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 9 節：凍結目標、學習者只和目標對打，以學習者**最佳回應**對「其他座位全部是目標」的 ROI 作為目標可剝削程度的下限。
- 平台：preset `6max-pko-10bb-exploit`（`exploit_target` = 模型 ID 或 `rule:<bot>`；模型目標會自動採用它的錦標賽規格，學習者從目標本身開始，round 0 先量目標自己的最佳回應）；每輪 match 額外量對目標的 ROI，訓練曲線與實驗頁顯示；模型頁「量可剝削程度」一鍵建立並開始。研究腳本 `tournament_probe.py --exploit-target`。
- 驗收：整合測試（3 人規格的模型目標：規格自動換成目標的、對手池只有目標、學習者從目標開始、round 0–2 都有對目標的 ROI、checkpoint 一律採用）；錯誤設定被拒絕（沒指定目標、目標不存在、未知 bot、自我對打比例不是 0、非錦標賽 preset）。
- **實測（10 BB PKO，每點 200 seed × 6 座位 = 1,200 場，seed 1,800,000,000 起）**：
  - **從零開始**（4 個程序，60,000 場，約 45 分鐘）：學習者對 c1、s12 的最佳回應 ROI 從 −20% ~ −51% 進步到 −8% ~ −3%，最好的一點 +1.0%（區間含 0）。60,000 場仍打不贏目標，**不能用來估計可剝削程度**（只證明它們不會被「從零學 60,000 場」的對手打敗）。
  - **從目標開始**（4 個程序，40,000 場，約 35 分鐘）：
    - round 0（目標自己的最佳回應對目標）：**c1 +26.0%（+16.3% ~ +35.6%）、s12 +15.6%（+7.2% ~ +24.0%）**。也就是說，模型自己的 Q 網路已經知道怎麼打贏自己的平均策略，平均策略至少可被剝削這麼多。
    - 繼續只和目標訓練 10,000–40,000 場：對目標的最佳回應在 −18% ~ +19% 之間大幅波動（c1 第二組 20,000 場 +18.7%，其他多數點不顯著），沒有超過 round 0；平均策略對目標約 −7% ~ +5%。只打同一個對手時 Q 網路很快過度擬合、忘記原本的優勢，這個訓練設定不適合做為更強的剝削者。
  - **對 ICM Push/Fold Nash baseline bot**：每個決策都要依當下籌碼重新求解，快取命中率低，1 小時只完成 round 0（最佳回應 −15% 與 −54%，兩個 seed 差異大），已停止。量 baseline bot 的可剝削程度需要預先算好的策略表，留待之後。
- 結論：目前最強的兩個 10 BB 模型，**平均策略可被剝削至少約 16–26% ROI**（以它們自己的最佳回應計），c1 比 s12 更容易被剝削。這和 S1 看到的「面對全下跟注過緊、幾乎不打 push/fold」一致，是之後訓練要改進的方向；PSRO／族群訓練（第 7 項）會直接用這個量值。
- 結果檔：研究結果資料夾 `research-results/s4`（從零開始）、`research-results/s4-warm`（從目標開始）。

### C 對強對手的表現（達到驗收，有保留）
- **驗收條件**（使用者定）：對模型池整體 ROI 的 95% 信賴區間下限 > 0；對混合 Rule Bot 不明顯退化。每個舊模型的對戰結果都記錄，但不要求每一個都不輸（策略之間可能互相剋制）。
- **對手池**（`learning/opponents.py`）：成員可以是 Rule Bot、模型庫的模型（同規格才會選入，依引擎設定比對、不看名稱）、或學習者自己的較早版本。非學習者座位依權重抽選：
  - `uniform`：平均抽。
  - PFSP `hard`：學習者常輸的對手多抽（權重 (1 − 勝率)²）；`variance`：勢均力敵的多抽。權重有下限，任何成員都不會消失。
  - 歷史版本：每隔一段場數把學習者目前的平均策略凍結加入，只保留最新幾版。
- **對模型池 benchmark**（`learning/pool_benchmark.py`、`scripts/pool_compare.py`）：
  - 整體：其他座位由池中模型輪流坐（每個 seed 每個座位輪一次），受測者本身若在模型庫中會被排除（leave-one-out）。
  - 單一對手：其他座位全部是某一個模型，每個成員各一列。
  - 配對差：兩個受測者在完全相同的錦標賽上比較，用來檢查對 Rule Bot 是否退化。
- **基準**（10 BB PKO，現有 6 個模型；每個對其他 5 個，1,000 seed × 6 = 6,000 場；單一對手各 1,800 場）：

  | 受測者 | 對模型池 ROI | 輸得最多的單一對手 |
  |---|---|---|
  | `pko-10bb-nfsp-s12` | **+5.1%**（+1.2 至 +9.1） | s11 −6% |
  | `pko-10bb-nfsp-s1` | +0.1% | s13 −3% |
  | `pko-10bb-nfsp-s11` | +0.1% | s12 −3% |
  | `pko-10bb-nfsp-s14` | −0.9% | s1 −2% |
  | `pko-10bb-platform` | **−5.6%**（−9.5 至 −1.8） | s12、s11 −11%（顯著） |
  | `pko-10bb-nfsp-s13` | **−6.4%**（−10.2 至 −2.5） | s12 −5% |
  | Tight bot | −14.4%（顯著） | 每個模型都顯著輸 |
  | `hunter` / `HeuristicPushFoldBot` | −21.9% / −20.9%（顯著） | 每個模型都顯著輸 |

  現有模型彼此大致打平（只有 s12 顯著為正），Rule Bot 對模型池大輸，所以這個 benchmark 比混合 Rule Bot 嚴格得多。
- **第一批：從頭訓練並加入對手池**（6 萬場；對手池 = 6 種 Rule Bot + 6 個 10 BB 模型；自我對打 50%；每個設定 2 個 seed；對模型池評估 300 seed × 6 = 1,800 場）：

  | 設定 | 對 Rule Bot | 對模型池 |
  |---|---|---|
  | 平均抽選 | +14.2%、+11.6% | −4.4%、−5.1% |
  | PFSP hard | +14.4%、+11.2% | −4.5%、−9.1% |
  | PFSP hard + 歷史版本 | +15.7%、+12.4% | −5.7%、−4.0% |

  - 從頭訓練 6 萬場還沒有超過現有模型，而且對模型池仍在上升（1.5 萬場約 −12% 至 −22%）。
  - PFSP 的權重幾乎沒有拉開：6 人錦標賽中「贏過某一個對手」的雜訊很大，對各成員的勝率都在 0.41–0.45 之間。
- **平台**：錦標賽 NFSP 訓練新增設定 `pool_models`（`library` 或模型 ID）、`pool_history`（加入本實驗最近幾版）、`pfsp`、`pool_eval_seeds`。
  - Coordinator 每輪把對手池寫成內容定址的 artifact（Rule Bot 名稱 + 模型 checkpoint 的 artifact 與 sha256），Actor 與 Match Job 驗證後載入。
  - Match 在錦標賽之外另外評估對模型池，訓練曲線新增「對模型池 ROI」；最後一輪另外評估每個成員，實驗明細顯示「對模型池的表現」表格。
  - 新欄位只在與預設不同時寫入 payload，既有實驗不變。
- **第二批**：(1) 從對模型池最強的 s12 繼續訓練 3 萬場，比較「加入對手池（PFSP + 歷史版本，自我對打 50%）」與「只自我對打（75%）」；(2) 第一批的 PFSP + 歷史版本再訓練 6 萬場（共 12 萬場）。正式評估：對模型池 1,000 seed × 6 = 6,000 場、每個對手 1,800 場；對 Rule Bot 與 s12 在同一批 6,000 場錦標賽上配對比較。

  | 候選 | 對模型池 ROI | 對 Rule Bot 比 s12（配對差） | 單一對手最差 |
  |---|---|---|---|
  | s12 + 3 萬場自我對打，seed 1 | **+4.5%**（+0.6 至 +8.5） | +2.2%（−3.2 至 +7.5） | s11 −4% |
  | s12 + 3 萬場自我對打，seed 2 | +1.1% | **+5.5%**（+0.2 至 +10.9） | s1 0% |
  | s12 + 3 萬場對手池，seed 1 | +1.6% | +3.5% | s1 0% |
  | s12 + 3 萬場對手池，seed 2 | +1.8% | +3.6% | s11 +2% |
  | 從頭 12 萬場對手池，seed 1 | +3.8%（−0.2 至 +7.8） | +4.7% | s12 −3% |
  | 從頭 12 萬場對手池，seed 2 | +1.6% | +1.2% | s12 −1% |

  - 第一名 `s12 + 3 萬場自我對打 seed 1` 用另一批從未用過的 6,000 場錦標賽再評估一次：對模型池 **+6.5%（+2.5 至 +10.5）**，兩次都顯著大於 0；對 Rule Bot 沒有退化（比 s12 +2.2%，不顯著）。**達到驗收條件**，已存成 `pko-10bb-nfsp-c1`。對每個舊模型的結果：s11 −4%、s12 −3%（皆不顯著）、s14 +4%、s1 +5%、s13 +10%（顯著）、platform +13%（顯著）。
  - **保留**：換 seed（seed 2）沒有同樣顯著；所有候選對模型池都在 +1% 至 +7% 之間，彼此差距在誤差範圍內。
  - **對手池與 PFSP 本身沒有帶來額外好處**：從同一個模型出發，加入對手池與只自我對打的結果相近；從頭訓練時對手池版本需要約 12 萬場才追上現有模型。主要的進步來自「從最強的模型繼續訓練」。可能原因：池中模型彼此實力接近、PFSP 權重在 6 人錦標賽中幾乎拉不開（對各成員的勝率都在 0.41–0.45）。
  - 平台的對手池、PFSP、歷史版本與對模型池評估都已完成，之後池中出現風格差異較大的模型（例如不同演算法、不同籌碼深度訓練後調整）時再重新比較。

### B1 錦標賽規格參數化（完成）
- **原則**：不把任何網站的賽制寫死。Natural8 等網站的 PKO 起始籌碼、桌子人數與盲注結構依賽事而不同；預設只取常見值（6 人、65/35、50/50 漸進式 bounty），模擬某場實際賽事時再依 lobby 的結構建立 preset。
- **錦標賽規格**（`poker/tournament_spec.py`，`TournamentSpec`）：人數（2–6）、起始籌碼（BB）、每級手數、BB ante、獎金分配（65/35、50/30/20、贏家全拿，或任意比例如「40/25/15/12/8」）、是否 PKO、bounty 比例、淘汰時現金比例、冠軍是否拿回自己的 bounty、buy-in，以及自訂盲注表（每級小盲、大盲、ante、手數，例如照 lobby 填入）。可以存成 JSON；轉成引擎的 `TournamentConfig`。9 人桌留待之後（需要擴充引擎與 v2 輸入）。
- **實驗可以換錦標賽**：建立任何錦標賽 preset（benchmark 或 NFSP 錦標賽訓練）時，可以用 `settings.tournament`（規格物件）與 `settings.opponents` 指定錦標賽與對手；UI「建立實驗」卡片新增「自訂錦標賽規格」表單。錦標賽設定存在實驗與模型中，模型的遊戲以存下的完整規格為準，名稱只拿來顯示。
- **同一套評估設定**（`EvaluationProtocol`）：錦標賽設定、對手組合、seed、座位輪換包成一個物件，模型與每個基準都用它評估，並記錄設定的 sha256。`scripts/tournament_compare.py` 用同一個設定一次評估多個模型與 Rule Bot，輸出 ROI、Bounty EV、ITM。
- **策略表跟著錦標賽走**：位置依人數產生（2–6 人，heads-up 時 SB 是按鈕）；可以選盲注等級；籌碼深度預設為起始籌碼的 0.3–2 倍（10 BB：3、5、8、10、15、20；25 BB：8、12、20、25、38、50；50 BB：15、25、40、50、75、100）；新增「面對開局加注」情境（對方加注到 2.5 BB，可以蓋牌、跟注、再加注、全下），6 人桌共 35 種情境。
- **Preset**：`6max-pko-25bb-train`、`6max-pko-50bb-train`、`6max-pko-25bb-top3-train`（50/30/20），以及 50 BB 的 PKO benchmark。`6max-pko-10bb-train` 的錦標賽設定與之前完全相同（已驗證 JSON 一致）。
- **基準**（同一套評估設定：6 人 PKO、65/35、50/50 bounty、混合 Rule Bot、500 seed × 6 座位 = 3,000 場）：

  | 受測者 | 25 BB ROI | 50 BB ROI |
  |---|---|---|
  | Tight bot | **+12.5%**（+6.6 至 +18.4） | **+16.0%**（+10.3 至 +21.7） |
  | Aggro bot | +2.5% | −4.1% |
  | Call bot | +0.7% | −0.1% |
  | Random bot | −6.6%（顯著） | −9.4%（顯著） |
  | `hunter` | −13.9%（顯著） | −9.9%（顯著） |
  | `HeuristicPushFoldBot` | −14.9%（顯著） | −10.7%（顯著） |

  深籌碼時只會全下或蓋牌的 bot 大幅輸錢，最強的基準變成 Tight bot。25/50 BB 的模型驗收以 Tight bot 為比較對象。
- **訓練結果**（照 T3 的設定訓練 6 萬場；評估用同一套設定：1,000 seed × 6 座位 = 6,000 場，未用過的評估 seed）：

  | 模型 | 平均策略 ROI | 最佳回應 ROI | Bounty EV | 基準 Tight bot |
  |---|---|---|---|---|
  | `pko-25bb-nfsp-s1` | **+32.9%**（+27.9 至 +38.0） | +79.8% | 0.739 | +10.3% |
  | `pko-25bb-nfsp-s2` | **+34.8%**（+30.0 至 +39.5） | +70.3% | 0.744 | +10.3% |
  | `pko-25bb-nfsp-s3` | **+32.5%**（+27.6 至 +37.3） | +48.3% | 0.732 | +10.3% |
  | `pko-50bb-nfsp-s1` | **+32.1%**（+27.4 至 +36.7） | +93.5% | 0.742 | +16.6% |
  | `pko-50bb-nfsp-s2` | **+37.7%**（+32.6 至 +42.8） | +79.9% | 0.768 | +16.6% |
  | `pko-50bb-nfsp-s3` | **+38.7%**（+34.1 至 +43.3） | +92.6% | 0.773 | +16.6% |

  - 25 BB 與 50 BB 各 3 個 seed 全部顯著大於 0，且遠高於最強基準 Tight bot，符合驗收。50 BB seed 1、3 第一次因記憶體不足中止（見下），以新的記憶體實作重跑完成（每個程序約 1.5 GB）。
  - 在 10/25/50 BB 用同一組設定都學得起來，深籌碼時暫時不需要更細的加注尺寸；翻牌後的表現之後以重播與「面對開局加注」策略表檢查。
  - 模型已存入本機模型庫：`pko-25bb-nfsp-s1`、`-s2`、`-s3`、`pko-50bb-nfsp-s1`、`-s2`、`-s3`。
- **記憶體（使用者回報 RAM 快用完）**：同時跑 6 個訓練時每個程序佔 7–9 GB，共約 51 GB（總共 61.6 GB）。
  - 原因：RL 與 SL buffer 把每個狀態存成 Python tuple（406 個 Python 浮點數物件）；另外每次評估最佳回應時，腳本重新載入整個模型（包括 SL reservoir，再轉成 tuple）。
  - 修正：`learning/memory.py` 改用連續的 NumPy float32 矩陣存放（每筆約 1.7 KB，依需要倍增到上限；每手結束的訓練不需要 next state，完全不配置）；最佳回應評估只載入網路權重（`from_actor_bundle`）。
  - 驗證：同 seed 訓練 400 場後模型檔的 sha256 完全相同（訓練結果不變）；相同的 8,000 場訓練加 2 次評估，峰值實體記憶體由 **4.65 GB 降到 1.87 GB**，時間由 527 秒降到 263 秒（少建立大量 Python 物件）。
  - `scripts/sweep.py` 啟動前估計每個程序的峰值記憶體，超過可用記憶體的 70% 時拒絕啟動（可調整或 `--force`）。

### A 在叢集上完整跑一次正式訓練（通過）
- **設定**：獨立的 `tableforge ui`（暫存資料位置），建立叢集、開 8 個 Worker（依序分到 CCD0、1、0、1…），跑 `6max-pko-10bb-train`：20 輪 × 4 個 Actor × 750 場，每輪 31,000 步，每輪評估 300 × 6 場。
- **流程**：20 輪全部完成，共 121 個 Job 全部成功，總耗時 77 分鐘（每輪約 3.5–4 分鐘，大部分是 Learner）。
  - 122 次執行中只有 1 次重試：第一輪兩個 Worker 同時把同一個 checkpoint 寫入本機快取，Windows 拒絕替換正被開啟的檔案（`WinError 5`），自動重試後成功。已修正：同名檔（以 sha256 命名，內容相同）已存在時直接使用；新增 2 個測試。
  - 升級判定：20 輪中 19 輪升級，第 6 輪因顯著較差（−9.8 ROI 點）被拒絕，下一輪從第 5 輪繼續。
  - CPU 負載（207 次取樣）：平均 CCD0 約 1.3、CCD1 約 1.9 個執行緒，最高 3.1 與 5.4；Learner 單執行緒的時間佔大部分，所以同時忙碌的 Worker 不多。
- **訓練曲線**（每輪同一批 1,800 場，平均策略 ROI）：

  | 輪次 | 1 | 5 | 10 | 12 | 15 | 17 | 18 | 19 | 20 |
  |---|---|---|---|---|---|---|---|---|---|
  | 平均策略 | −10.2% | +0.6% | −0.4% | +5.1% | **+13.9%** | **+15.7%** | **+17.7%** | **+14.1%** | **+23.5%** |
  | 最佳回應 | −1.5% | +13.1% | +55.2% | +52.3% | +50.2% | +39.9% | +24.4% | +65.7% | +23.1% |

  從第 15 輪（4.5 萬場）起平均策略都顯著大於 0。最佳回應的起伏大，因為它每一輪都是對當下對手的純策略。
- **驗收評估**（與 T3 完全相同：同一份錦標賽設定、同一組混合 Rule Bot、座位輪流、1,000 seed × 6 座位 = 6,000 場、同一批評估 seed）：

  | 模型 | 平均策略 ROI | 最佳回應 ROI |
  |---|---|---|
  | **`pko-10bb-platform`**（平台，本次） | **+15.4%**（+11.0 至 +19.9） | **+32.0%**（+27.1 至 +37.0） |
  | 研究模型 seed 1、11–14 | +15.5% 至 +21.2% | +29.6% 至 +62.3% |

  平均策略與研究模型同一水準（Bounty EV 0.616、ITM 33.3%、平均名次 2.99）。模型已存成 `pko-10bb-platform`，放在本機模型庫。
- **分析功能**：用這個模型產生重播（6 手、7 個帶決策說明的決策）與策略表（300 張，約 6 秒）都正常。
- **觀察（非驗收項目）**：「對方 bounty 越大，跟注越寬」並不是每個模型都學到。在策略表的所有「面對全下」情境（5、10、15 BB）中，平均跟注比例由 bounty 1× 到 3× 的變化：`pko-10bb-nfsp-s11` 32.6% → 38.1%，`-s13` 33.7% → 31.6%，`-s12` 25.6% → 23.5%，`pko-10bb-platform` 25.0% → 24.6%。策略表的情境是所有人籌碼相同、在第一級盲注，其中高 bounty 的組合在實際訓練中很少出現，模型在這些組合的判斷可信度較低。B1 之後的評估應另外量測各模型在「對手 bounty 較大」時的實際表現，而不只看策略表。

### R3 錦標賽訓練接上平台、模型、重播與策略表
- **決策說明介面**（`learning/explain.py`，規格見 `architecture/01-system.md` 6.1）：所有演算法都用同一種格式回答「這個局面你會怎麼做」：四個統一動作（蓋牌、過牌／跟注、加注、全下）的機率（必填），加上有名稱與單位的演算法特有數值（選填），以及模型宣告的打法清單（第一個是預設）。重播與策略表只依賴這個介面，之後加入其他演算法不需要改畫面。
  - `NfspExplainer`：打法「平均策略」（預設）與「最佳回應」；數值是各動作的 Q 值，錦標賽中換算成 buy-in（這手結束時權益的預期變化）。
  - `RuleBotExplainer`：Rule Bot 的動作機率為 1。
- **錦標賽訓練接上分散式流程**：沿用 HU 的 Init／Actor／Learner／Match Job，新增欄位只在與預設不同時寫入 payload，既有實驗的 Job 內容與冪等鍵完全不變。
  - Actor 設定 `tournament_json` 時打整場錦標賽（自我對打比例、每手結束），輸出同樣的 experience shard。
  - Match 在錦標賽時改為對混合 Rule Bot 的錦標賽 benchmark：平均策略、最佳回應與上一版在同一批錦標賽上比較；升級判定用平均策略與上一版的配對 ROI 差（只有顯著較差才拒絕）。每輪使用同一批評估錦標賽，訓練曲線可以直接比較。
  - 新實驗設定 `self_play_share`、`hand_horizon`、`double_q`、`rl_capacity`、`sl_capacity`（UI 自訂參數可調前三項以外的也可調）；錦標賽訓練需要 v2 輸入。
  - Preset `6max-pko-10bb-train`（T3 驗收設定：20 輪 × 4 個 Actor × 750 場，每輪 31,000 步，每輪評估 300 × 6 場）與 `6max-pko-10bb-smoke`。
  - 訓練曲線改為顯示錦標賽 ROI（平均策略與最佳回應），各 seed 最新一輪的表格顯示 ROI、Bounty EV、ITM 與「比上一版」。
- **Learner 加速**：NFSP 訓練步驟改用 NumPy 建立批次張量（結果完全相同，已比對 loss），CPU 單執行緒每步由 6.9 ms 降到 5.3 ms；Worker 預設 PyTorch 只用 1 個執行緒（`TABLEFORGE_WORKER_THREADS` 可改），避免同一台電腦的多個 Worker 互搶核心。
- **模型**（「模型」頁）：模型庫改放在叢集主機的資料位置（`coordinator\models`），Coordinator 可以直接讀取；T3 驗收的 5 個模型已搬過去。NFSP 實驗完成後可在實驗明細「存成模型」（保存目前採用的 checkpoint、完整訓練設定與最後一輪評估）；模型頁列出 ROI、說明，並可刪除。
- **重播**（「重播」頁）：選模型、打法、其他座位（混合 Rule Bot、全部是這個模型、或某一種 Rule Bot）、模型座位與 seed，Worker 打一整場並記錄完整牌譜。
  - 顯示整場結果（名次、名次獎金、賞金、淨結果），手牌清單可篩選（模型有參與、全下、淘汰別人、被淘汰）。
  - 逐動作前後播放：桌面、公牌、底池、每人籌碼（含 BB 數）、頭上 bounty、按鈕位置、每人最後的動作；預設顯示所有人的手牌，可以切換成只看模型的。
  - 模型的每個決策顯示四個動作的機率與 Q 值；手結束時顯示淘汰與 bounty 分配。
  - 網址可以帶參數直接開到某一場、某一手、某一步（`/replay?model=…&seed=…&run=1&hand=…&step=…`）。
- **策略表**（「策略表」頁）：Worker 一次算出翻牌前 169 種起手牌在所有情境下的打法：前面都蓋牌時的 5 個位置，以及面對別人全下時的 15 種位置組合 × 3 種 bounty 倍數（1×、2×、3×），籌碼深度 3、5、8、10、15、20 BB，共 300 張表，約 10 秒。
  - 13×13 熱圖：預設用顏色比例顯示所有動作，也可以只看某一個動作；圖例顯示全部 1,326 種起手組合中各動作的比例；游標停在格子上顯示機率與 Q 值。
  - 匯出 CSV（所有情境、所有手牌、機率與 Q 值）。
  - 例：`pko-10bb-nfsp-s11` 平均策略在 BB 面對 BTN 10 BB 全下時，對方 bounty 1× 時 A9o 跟注約 59%、3× 時約 93%。這個傾向並非每個模型都有，見「A 在叢集上完整跑一次正式訓練」的觀察。
- 分析 Job 不屬於任何實驗，以內容做冪等，同樣的重播或策略表只算一次，之後直接讀取結果。
- 驗收：
  - 整合測試：錦標賽訓練在平台上從建立到完成（2 輪），Actor 確實打錦標賽、每輪評估用同一批錦標賽、曲線含平均策略與最佳回應、存成模型與刪除；舊 payload 位元組不變；所有 explainer 輸出同一種格式；重播記錄每一手、所有手牌、只有模型座位有決策說明、籌碼跨手連續；策略表涵蓋所有情境與手牌、最佳回應的機率只有 0 或 1、面對全下時只有蓋牌或跟注；錯誤輸入被拒絕。
  - 實機：在 `tableforge ui` 建立叢集、放入 `pko-10bb-nfsp-s11`，重播約 2 秒、策略表約 9 秒完成，並截圖確認模型、重播與策略表頁面。
- **分散式訓練的效果（已驗證）**：平台是「每輪固定模型收集資料、再集中訓練」，研究時是「每手都訓練」。以同樣設定在單機模擬平台的輪次流程（評估 300 × 6 場，信賴區間約 ±8%）：

  | 每輪場數 | seed | 1 萬場 | 3 萬場 | 6 萬場 |
  |---|---|---|---|---|
  | 3,000（= preset） | 11 | 約 −6% | +6.0% | **+18.4%**（+9.3 至 +27.4） |
  | 3,000（= preset） | 12 | 約 −3% | +2.8% | **+10.7%**（+2.4 至 +19.1） |
  | 1,000 | 11 | 約 −8% | 約 +8% | **+11.1%**（58,000 場） |
  | 1,000 | 12 | 約 −4% | 約 +16% | **+21.1%**（55,000 場） |

  前 2 萬場比研究時慢，但 6 萬場時 4 次都顯著大於 0，與研究時（+15% 至 +21%）同一水準。`6max-pko-10bb-train` 的設定因此可以產生正式模型。

### T2 PKO
規格見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 5 節。
- **設定**：`TournamentConfig.bounty = BountyConfig(bounty_share, cash_share, winner_keeps_own)`，預設 buy-in 的 50% 是起始 bounty、淘汰別人時拿 50% 現金（其餘加到自己頭上）、冠軍拿回自己頭上的 bounty。三個值都可設定（例如 `cash_share=1` 就是一般 knockout）。沒有 bounty 的設定 JSON 與 T1 完全相同，已有實驗不受影響。
- **誰收 bounty**：被淘汰者有資格爭奪的最高那個 pot 的贏家，這個人一定蓋得過被淘汰者；平分 pot 就平分 bounty；對手蓋牌時由沒蓋牌的人收。
- **資訊**：每手開始時，每個人都知道各座位頭上的 bounty 與起始 bounty（`TournamentHandInfo.bounties`、`starting_bounty`）。每手記錄 bounty 前後值與淘汰紀錄（被淘汰者、收取者、現金、加到頭上的金額），與 ICM 分開。
- **參考對手**：`HeuristicPushFoldBot` 新增 `bounty_weight`：跟注別人的全下時，若自己蓋得過對方，依對方 bounty 是起始 bounty 的幾倍放寬門檻。Worker 名稱 `hunter`（`bounty_weight=1.5`）。
- **Preset**：`6max-pko-10bb`、`6max-pko-25bb`（受測者 `HeuristicPushFoldBot`），以及 `-hunter` 版本（受測者賞金獵人版）；對手都是混合 Rule Bot，3,000 場。錦標賽結果卡片會顯示 PKO 規則。
- **量測**（10 BB、3,000 場）：`hunter` 的 Bounty EV 0.350（0.321–0.380），一般版 0.331（0.293–0.368）；ROI 分別為 −7.3% 與 −10.5%。PKO 下兩者都輸給混合 Rule Bot（同一個 bot 在一般 SNG 是 +14.6%），因為獎池只剩一半、只會全下的策略收到的 bounty 不足以彌補。放寬跟注有幫助但差距還不顯著；T3 的學習要用這組 preset 當比較基準。
- 驗收：
  - 單元與性質測試 9 個：設定驗證與 JSON 來回、沒有 bounty 時 JSON 不變、一般淘汰的現金與累進、side pot 時 bounty 給主池贏家、平分 pot 時平分 bounty、每手的 bounty 資訊，以及 150 組隨機 PKO 錦標賽（2–6 人、3 種起始籌碼、3 種 payout、3 種 bounty 比例、3 種現金比例、冠軍拿或不拿自己的 bounty）的守恆：**任何時刻已付出的現金 + 所有人頭上的 bounty = 所有人投入的 bounty**，被淘汰的人頭上歸零，收取者一定還在場，每個被淘汰的人都恰好被收一次。
  - 整合測試：PKO preset 從建立到 Worker 執行再到合併結果（Bounty EV 大於 0，結果含 PKO 規則）。
  - **10 萬場 PKO soak**（`uv run python -m tableforge.poker.tournament_soak --tournaments 100000 --workers 12 --pko`，三種 bounty 設定輪流）零錯誤：50 萬次淘汰，其中 5,823 次 bounty 被平分，116 秒。
  - 實際在 `tableforge ui` 跑 `6max-pko-10bb-hunter`：3 個 Worker 15 秒完成 3,000 場，截圖確認結果卡片。
- **尚未做**：同時淘汰多人時，bounty 各自依所在的 pot 計算；「只能收自己蓋得過的人」以 pot 資格自然滿足，不另外設定。T3 開始前需要決定 v2 輸入的 bounty 欄位怎麼填（目前錦標賽環境已提供資料）。

### T3 錦標賽上的學習（進行中）
設計見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 6 節（NFSP、ICM 權益變化加 bounty 現金當獎勵、10 BB PKO、一半自我對打、驗收：ROI 顯著 > 0 且贏過基準）。
- **實作**：
  - `Tournament.equity(player)`：剩餘獎金的 ICM 份額 + 已拿到的 bounty 現金；結束時剛好等於名次獎金 + bounty 現金。`Tournament.finish()` 從 `run` 拆出，訓練迴圈可以一手一手打。
  - `NfspAgent` 可以打錦標賽：`begin_hand` 收到每手的盲注與錦標賽狀態，v2 輸入的錦標賽欄位（盲注、ante、等級、剩餘人數、payout、各座位 bounty）因此有值；以當手 BB 計算金額。v1 模型不能打錦標賽。
  - `learning/tournament_training.py`：學習者固定一個座位（依 seed 輪換），其他座位各 50% 是自己、否則從 6 種 Rule Bot 抽；每手結束給權益變化；可選擇決策跨手連接（預設）或每手結束（`hand_horizon`）。
  - `NfspConfig.double_q`：Double DQN 目標（預設關閉，舊模型不變）。
  - `scripts/tournament_probe.py`：單機訓練，每隔一段場數對混合 Rule Bot 跑錦標賽 benchmark。
  - 測試 7 個：起始權益 = 公平份額；25 組隨機錦標賽（有無 PKO、3 種起始籌碼、3 種自我對打比例）中**每個學習座位的獎勵加總 = 最終結果 − 起始權益**；錦標賽欄位確實有值；`hand_horizon` 與 Double DQN；v1 被拒絕；訓練後的模型能跑 benchmark。
- **第一輪探測**（10 BB PKO、hidden 128、每手訓練一次、每個設定 2 個 seed、每次評估 3,000 場）：

  | 設定 | 場數 | ROI | Q loss |
  |---|---|---|---|
  | 未訓練 | 0 | −7% 至 −13% | |
  | 跨手連接（預設） | 1 萬 | −16% 至 −17% | 22–28 |
  | 跨手連接、學習率 3e-4 | 1 萬 | −26% 至 −27% | 約 1 |
  | 跨手連接、獎勵 ÷ 0.3 | 1 萬 | −11% 至 −13% | 16–29 |
  | Double DQN、獎勵 ÷ 0.3 | 3,000 | −10% 至 −16% | 2–3 |
  | 每手結束、獎勵 ÷ 0.3 | 6,000 | −11% | 約 0.6 |
  | 每手結束 + Double DQN、獎勵 ÷ 0.3 | 6,000 | **−7% 至 −8%** | 約 0.5 |

  - 決策跨手連接時 Q 值不穩定（loss 一直很高），訓練反而讓模型變差。
  - 改成每手結束後穩定得多。因為 ICM 權益本身就是「之後能拿多少錢」的估計，每手結束等於「以 ICM 為準選擇這手最好的打法」，是 SNG 常見的做法。
  - **最佳回應（Q 網路本身）比平均策略好**：同一個模型（每手結束 + Double DQN，3,000 場訓練），Q 網路 ROI −0.3%（−8.1% 至 +7.6%），平均策略 −7.2%。NFSP 的平均策略學得比最佳回應慢。
  - 同一批評估 seed 的 Rule Bot：aggro **+9.6%**（顯著）、call −2.9%、tight −1.7%、`hunter` −3.8%、`HeuristicPushFoldBot` −6.8%、random −35%。這個對手組合對積極打法有利，模型還沒學到。
- **第二輪：每手結束 + Double DQN，2 萬場**（每個設定 2 個 seed，評估 3,000 場）：

  | 設定 | 5,000 場 | 1 萬場 | 1.5 萬場 | 2 萬場 |
  |---|---|---|---|---|
  | anticipatory 0.1 | −16%、−6% | −4%、−3% | −3%、−3% | −3%、−6% |
  | anticipatory 0.3 | −8%、−4% | −8%、−9% | +2%、−4% | −7%、−5% |

  - 1 萬場後穩定在 −3% 至 −6%，比 `HeuristicPushFoldBot`（−6.8%）好、與 `hunter`（−3.8%）相近，但還沒顯著大於 0；1 萬場之後不再進步。
  - anticipatory 0.3 沒有幫助。
- **第三輪：大 buffer（RL 20 萬、SL 100 萬），3 萬場，3 個 seed**（評估 3,000 場；粗體 = 顯著大於 0）：

  | 自我對打比例 | seed 1 | seed 2 | seed 3 | 平均 |
  |---|---|---|---|---|
  | 50% | **+10.4%** | +2.4% | +5.9% | +5.6% |
  | 75% | **+10.4%** | **+9.9%** | +4.5% | **+8.3%** |

  - 大 buffer 是關鍵：6 個 seed 在 3 萬場時全部為正，都贏過 `hunter`（−3.8%）與 `HeuristicPushFoldBot`（−6.8%）。Bounty EV 與 Prize EV 同時上升。
  - 75% 自我對打較好，且多數 seed 到 3 萬場仍在上升。
- **確認實驗：T3 第一版達到驗收**
  - 設定：v2 輸入、hidden 128、batch 128、每手訓練一次、學習率 1e-3、Double DQN、每手結束（`hand_horizon`）、獎勵 ÷ 0.3、RL buffer 20 萬、SL buffer 100 萬、75% 自我對打、6 萬場（約 62 萬手，CPU 單執行緒約 100 分鐘）。
  - seed 1 與 4 個新 seed（11–14）；每次評估 1,000 seed × 6 座位 = 6,000 場，評估 seed 未曾用過。

  | seed | 未訓練 | 1.5 萬場 | 3 萬場 | 4.5 萬場 | 6 萬場（平均策略） | 6 萬場（Q 網路） |
  |---|---|---|---|---|---|---|
  | 1 | −12.9% | +2.4% | **+10.3%** | **+20.9%** | **+17.0%**（+12.7 至 +21.3） | **+44.5%**（+39.4 至 +49.7） |
  | 11 | −13.4% | **+7.4%** | **+10.2%** | **+16.6%** | **+20.5%**（+16.0 至 +25.1） | **+62.3%**（+57.1 至 +67.5） |
  | 12 | −12.1% | +1.2% | **+9.1%** | **+19.7%** | **+21.2%**（+16.5 至 +26.0） | **+55.5%**（+50.4 至 +60.7） |
  | 13 | −18.6% | −0.6% | **+7.0%** | **+15.4%** | **+15.5%**（+11.1 至 +19.9） | **+29.6%**（+24.5 至 +34.6） |
  | 14 | −15.7% | +0.6% | **+13.3%** | **+17.4%** | **+21.2%**（+16.5 至 +25.8） | **+61.0%**（+55.9 至 +66.1） |

  （粗體 = 95% 信賴區間下限 > 0；對手為混合 Rule Bot；同一批評估 seed 上 `hunter` 約 −4%、`HeuristicPushFoldBot` 約 −7%。）
  - **5 個 seed 全部顯著大於 0，且遠高於兩個基準**，符合驗收條件。
  - 平均策略 6 萬場平均 **+19.1%**；Q 網路（直接用最佳回應出手）平均 **+50.6%**。Q 網路的 Bounty EV（0.67–0.84）與 ITM（39–46%）都明顯較高。
  - Q 網路是針對訓練時的對手組合學出的最佳回應，可能較容易被針對；平均策略較穩健。兩者都保留，另做對其他對手的穩健性檢查（見下）。
  - 模型檔保存在研究用資料夾（未納入 repo）；正式訓練將改由平台的分散式流程產生。
- **穩健性檢查**（seed 11 的模型，換成沒評估過的對手組合，每種 600 場；對「其他訓練模型」240 場）：

  | 對手（其餘 5 席全部是） | 平均策略 ROI | Q 網路 ROI |
  |---|---|---|
  | tight | **+40.8%** | **+78.7%** |
  | call | **+48.1%** | **+102.1%** |
  | aggro | **+40.1%** | **+82.7%** |
  | `HeuristicPushFoldBot` | **+50.6%** | — |
  | `hunter` | **+52.0%** | **+109.5%** |
  | 其他 4 個 seed 訓練出的模型（平均策略） | +9.0%（−11.0 至 +29.0） | +17.7%（−4.1 至 +39.5） |

  - 面對每一種單一 Rule Bot 都大幅獲利，不是只會剝削混合組合。
  - 面對其他訓練模型時為正但不顯著（場數少、對手同樣強）：學到的不是只能打 Bot 的策略，但也沒有明顯壓過同級模型。
- **下一步**：錦標賽訓練接上平台（分散式 Actor/Learner Job、UI preset 與訓練曲線），再擴充到 25/50 BB 與一般錦標賽；之後做 Worker 平均分散到各 CCD。
- 背景訓練改為平均分配到兩個 CCD、以「低於一般」優先權執行（見「下一步候選」第 2 項）。

### P1-b0 訓練用的 UI
讓 P1-b 的調參可以在 `tableforge ui` 的「實驗」頁完成，不需要寫腳本。
- **每輪 Rule Bot benchmark**：新增實驗設定 `benchmark_deals`。大於 0 時，每一輪的升級判定 Job 會讓新模型對 4 種 Rule Bot 各打 `benchmark_deals` 局（每局兩手、交換座位）。`benchmark_deals` 為 0 的舊實驗 payload 完全不變，已存在的實驗不受影響。
- **訓練曲線**：NFSP 多輪訓練的實驗明細新增「訓練曲線」卡片，畫出每一輪對 4 種 Rule Bot 的 bb/100 與 95% 信賴區間色帶（SVG，不新增前端套件）。
- **自訂訓練參數**：建立實驗時勾選「自訂訓練參數」，可修改輸入格式、輪數、每輪 Actor 數、每個 Actor 手數、每輪訓練步數、隱藏層、batch、學習率、目標網路更新間隔、anticipatory、ε、benchmark 局數、升級判定局數。
  - 只開放給 NFSP 多輪訓練的 preset；Coordinator 會檢查欄位名稱、型別與範圍，不合法時回 400 並說明原因。
  - 學習率、anticipatory、ε、目標網路更新間隔會傳到 Worker 端的 NFSP agent（之前固定為預設值）。等於預設值時不寫入 payload，舊實驗的 Job 內容與 idempotency 不變。
- **多 seed 群組**：填入「1, 2, 3」會以相同設定建立 3 個實驗（最多 16 個），彼此只差 seed，共用群組 ID。實驗列表新增「群組」欄；群組內任一實驗的明細都會並排畫出所有 seed 的曲線（目前這個是實線、其他是虛線），下方表格列出各 seed 最新一輪對每個 Rule Bot 的 bb/100，信賴區間不含 0 時以顏色標示。
- **新 preset `hu-nfsp-v2-train`**：v2 輸入、10 輪 × 4 個 Actor × 2,000 手、每輪 2,000 步、hidden 128、batch 128，每輪對每個 Rule Bot 1,000 局 benchmark。作為 P1-b 的起點。
- API：`GET /api/training-options`（各 preset 可調欄位、預設值與範圍）；`POST /api/experiments` 新增 `settings` 與 `seeds`；`ControlService` 的 `training_options` 查詢與 `create_experiment` 指令同樣支援。
- 驗收：
  - 整合測試 5 個：參數驗證（範圍、型別、未知欄位、非 NFSP preset）、舊 payload 位元組不變、群組建立、實際跑 2 輪訓練並確認每輪都有對 4 種 Rule Bot 的 benchmark、控制 API 的群組建立與可調欄位。
  - 實際在 `tableforge ui` 以縮小的 `hu-nfsp-v2-train`（4 輪 × 2 個 Actor × 1,500 手、每輪 1,500 步、benchmark 500 局）建立 3 個 seed 的群組，3 個 Worker 共 164 秒完成，並截圖確認訓練曲線、各 seed 表格與實驗列表的群組欄。這麼短的訓練還贏不了 Rule Bot，而且 seed 之間差異很大（例如第 4 輪對 random 從 −83 到 +208 bb/100），再次確認 P1-b 需要多 seed 比較。
- 節點測速與輸入格式 R² 診斷仍留在腳本（`scripts/nfsp_speed.py`、`scripts/encoding_r2.py`）。

## P1 模型強度

之前為了先完善平台而暫停，停在「已找到主要瓶頸、尚未修正」的狀態。現在與錦標賽路線一起重新排定（見下方「路線」）。

### 路線（已定案，尚未動工）

最終目標是錦標賽／PKO 策略，而目前的 HU cash 環境（每手獨立、100BB 重置、獎勵為單手 BB 盈虧）學不到存活、短籌碼、盲注上升、名次獎金與 bounty。因此：

```text
P1-a（v2 輸入）→ T1（錦標賽環境）─┬→ P1-b（HU cash 長時間訓練）
                                 └→ T2（PKO）→ T3（錦標賽上的學習）
```

- **P1-a**：v2 輸入一開始就設計成 2–6 人通用，並預留錦標賽欄位。
- **T1**：6 人錦標賽環境與 benchmark。
- T1 完成後，**P1-b 與 T2/T3 平行推進**，P1-b 不再是錦標賽工作的前提。
- **HU bb/100 benchmark 保留**，作為基礎牌技的回歸測試。
- 完整規格見 [architecture/08-tournament.md](architecture/08-tournament.md)。

### 量測工具
- `learning/benchmark.py`：對 random、call、tight、aggro 4 種 Rule Bot 各打固定數量的牌局；每副牌交換座位各打一次（duplicate），使用與訓練不重疊的 seed 範圍，輸出 bb/100 與 95% 信賴區間。「顯著獲勝」指信賴區間下限大於 0。
- `scripts/nfsp_probe.py`：用 NFSP 自我對打訓練，每隔一段手數跑一次 benchmark，輸出 JSON 行。例：`uv run python scripts/nfsp_probe.py --hands 20000 --checkpoints 4 --deals 1000`。

### 基準結果
目前預設設定（hidden 128、batch 128、每 4 手訓練 1 次）自我對打 2 萬手，每個對手 2,000 手評估（bb/100，粗體表示信賴區間不含 0）：

| 訓練手數 | random | call | tight | aggro |
|---|---|---|---|---|
| 0 | -17 | -30 | **-219** | -58 |
| 5,000 | **+274** | -134 | **-315** | -92 |
| 10,000 | **+324** | -16 | **-352** | -28 |
| 20,000 | **+258** | **-165** | **-358** | +46 |

只穩定贏 random，一直顯著輸給 tight，而且沒有隨訓練改善。另外以純 DQN 單獨對 tight 學 4 萬手（每手訓練 1 次），結果在 -106 到 +278 之間跳動，信賴區間都含 0。

### 找到的瓶頸：輸入看不出牌力
以監督式學習測試目前的輸入能否表達「牌有多強」：隨機產生手牌與 0、3、4、5 張公牌，以 Monte Carlo 算出對隨機手牌的 equity，用與 NFSP 相同結構的網路（hidden 128）學習預測，2 萬筆資料、30 epochs：

| 輸入 | 測試集 R² |
|---|---|
| `poker-hunlhe-vector-v1`（手牌、公牌各 52 維 one-hot 與下注資訊） | 0.46 |
| v1 加上 30 個牌力特徵（兩張手牌大小、同花、對子、目前成牌類型與最大牌） | 0.78 |

在最單純的監督式設定下，網路都難以從 one-hot 牌面學到牌型組合；RL 的訊號（每手一個有雜訊的輸贏）弱得多，因此只加長訓練或換更強的 GPU 幫助有限。

### P1-a 結果（已完成）
- **實作**：
  - `poker/strength.py`：只用自己的手牌與公牌計算的牌力特徵（Chen 分數、手牌大小、同花、對子；成牌類型與最大牌、頂對、超對、同花聽牌、兩頭與卡順聽牌、公牌成對／三條／同花／可能成順）。
  - `learning/encoding_v2.py`：406 維，同時接受兩人與 2–6 人的 Observation；對手固定 5 個槽位；籌碼、彩池、下注以 BB 計；錦標賽欄位由 `TournamentContext` 提供，cash 時為 0。每個欄位有名稱（`feature_names()`），方便除錯。
  - `learning/encoders.py`：依名稱選擇輸入格式。`NfspConfig.observation_schema` 預設仍是 v1；checkpoint、experience shard 與 init payload 都記錄格式，舊 checkpoint（沒有這個欄位）照常以 v1 載入；learner 會拒絕與模型格式不同的 shard。
  - 新增 preset `hu-nfsp-cpu-iterative-v2`；v1 的 init payload 位元組完全不變（不影響既有實驗的冪等鍵）。
- **監督式測試**（`scripts/encoding_r2.py`，2 萬筆、30 epochs、每筆 100 次 Monte Carlo，約 30 秒資料加 1 分鐘訓練）：

  | seed | v1 R² | v2 R² |
  |---|---|---|
  | 7 | 0.476 | **0.887** |
  | 11 | 0.493 | **0.863** |

  比先前「v1 加 30 個特徵」的 0.78 更高。
- **NFSP 冒煙測試**（`scripts/nfsp_compare.py`，預設設定、自我對打 2 萬手、每個 Bot 2,000 手評估、2 個 seed）：

  | 輸入／seed | random | call | tight | aggro |
  |---|---|---|---|---|
  | v1 / 1 | **+258** | **−165** | **−358** | +46 |
  | v1 / 2 | **+349** | +54 | **−372** | −1 |
  | v2 / 1 | **+353** | +43 | **−185** | **+264** |
  | v2 / 2 | **+242** | **−274** | **−300** | **−225** |

  粗體表示 95% 信賴區間不含 0。v2 能正常訓練；seed 1 對 tight 與 aggro 明顯較好，但 seed 2 沒有，兩個 seed 差距很大。**光換輸入不足以打贏 tight**，訓練設定本身（每手訓練次數、學習率、目標網路更新、buffer）仍是瓶頸，留給 P1-b。
- **速度**（`scripts/nfsp_speed.py`，hidden 128）：CPU 自我對打約 500 手/秒、benchmark 約 3,800 手/秒；GPU 反而較慢（約 220 與 2,350 手/秒），因為每個決策只算一筆，傳資料的成本大於計算。一次完整 benchmark（4 個 Bot 各 4,000 手）在 CPU 上約 5 秒。自我對打 2 萬手約 80 秒（v1）至 110 秒（v2）。
- 驗收：
  - 單元與性質測試 22 個：Chen 分數、成牌與聽牌判斷（含河牌不算聽牌、公牌同花不算自己的同花聽牌）、欄位數與名稱一致、兩人引擎與 6 人引擎在兩人時編碼完全相同、6 人時對手槽位依座位順序、金額以 BB 計（盲注放大 10 倍編碼不變）、錦標賽欄位（cash 為 0）、所有欄位都在小範圍內，以及 150 組隨機牌局的資訊隔離測試（換掉別人的手牌與未發的公牌，編碼不變）。
  - NFSP v2 測試 6 個：v1 仍是預設、v2 訓練與 checkpoint 來回一致、舊 checkpoint 照常載入、checkpoint 格式與設定不一致會被拒絕、shard 記錄並檢查格式、v1 init payload 位元組不變。

### P1-b 進度：找到並修正獎勵縮放問題
以 `scripts/nfsp_probe.py`（新增 `--target-update`、`--reward-scale`、`--threads`、`--tag`）在 CPU 上平行跑，每個設定 3 個 seed，每隔一段手數對 4 種 Rule Bot 各打 1,000 局（2,000 手）。粗體 = 3 個 seed 平均；「顯著贏」是 12 個 seed×對手中信賴區間下限大於 0 的個數。

**第一批：調整訓練頻率、學習率與網路大小（6 萬手）都沒有用**

| 設定 | random | call | tight | aggro | 顯著贏 |
|---|---|---|---|---|---|
| A 預設（每 4 手訓練 1 次） | +336 | −149 | −297 | +27 | 3/12 |
| B 每手訓練 1 次 | +415 | −203 | −305 | +110 | 4/12 |
| C 每手 1 次、學習率 3e-4、目標網路 1,000 步 | +418 | −109 | −249 | +311 | 6/12 |
| D 每 2 手 1 次、hidden/batch 256 | +410 | −149 | −331 | +77 | 4/12 |

**原因**：Q 網路用 Huber loss 學每手的 BB 盈虧。Huber loss 在誤差大於 1 時是線性的，而每手盈虧常是幾十到上百 BB，Q 值因此學到接近「中位數」而不是「期望值」：大多數手牌小輸，少數手牌大贏或大輸，於是 Q 值系統性低估跟注、過度蓋牌（Q loss 一直在 20–35）。

**修正**：`NfspConfig.reward_scale`：Q 學習前把獎勵除以這個值（100 = 起始籌碼 BB 數），讓誤差落在 Huber 的二次區間；所有動作的 Q 值同比例縮放，選擇不變。預設 1.0，舊 checkpoint 與舊實驗不變；UI 的自訂參數可調；`hu-nfsp-v2-train` 預設 100。

**第二、三批：reward_scale = 100**

| 設定 | 手數 | random | call | tight | aggro | 顯著贏 |
|---|---|---|---|---|---|---|
| E 預設 + 縮放 | 4.5 萬 | +417 | +384 | −66 | +529 | 9/12 |
| H 預設 + 縮放 | 15 萬 | +380 | +525 | −1 | +744 | 9/12 |
| I 目標網路 1,000 步 | 15 萬 | +357 | +385 | −40 | +640 | 9/12 |
| J anticipatory 0.3 | 15 萬 | +444 | +419 | −49 | +680 | 9/12 |

- 每個 seed 都顯著贏 random、call、aggro；對 tight 從 −250 至 −350 進步到約打平（−130 至 +93，大多不顯著）。Q loss 從 20–35 降到約 0.04。
- 把縮放與「每手訓練、較低學習率」合併（F）反而較慢，暫不採用。
- 目標網路更新間隔與 anticipatory 在這個範圍內沒有明顯差別。

**第四批：50 萬手，一般 buffer 與 4 倍 buffer（RL 20 萬、SL 40 萬）**

| 設定 | random | call | tight | aggro | 顯著贏 |
|---|---|---|---|---|---|
| K 預設 buffer | +438 | +411 | −54 | +716 | 9/12 |
| L 4 倍 buffer | +471 | +425 | **+49** | +777 | 10/12 |

- 較大的 buffer 讓對 tight 由負轉正：L 的 seed 3 在 37.5 萬與 50 萬手都**顯著贏 tight**（+209、+175），其他兩個 seed 在 −62 至 +99 之間。
- 訓練 50 萬手之後就沒有再明顯進步，瓶頸不是訓練長度。

**第五批：100 萬手，對每個 Bot 評估 4,000 局（8,000 手，符合驗收）** —— **P1-b 達到驗收**

| 設定 | random | call | tight | aggro | 顯著贏 |
|---|---|---|---|---|---|
| M buffer 10 倍（RL 50 萬、SL 100 萬） | +532 | +634 | **+164** | +701 | **12/12** |
| N buffer 4 倍（RL 20 萬、SL 40 萬） | +420 | +582 | +61 | +739 | 10/12 |

- M 的 3 個 seed 在 50 萬、75 萬、100 萬手都顯著贏 4 種 Rule Bot（對 tight：+74 至 +288），換 seed 也成立。
- **驗收設定**：v2 輸入、hidden 128、batch 128、每 4 手訓練一次、學習率 1e-3、目標網路 200 步、`reward_scale=100`、RL buffer 50 萬、SL buffer 100 萬、自我對打 100 萬手（CPU 單執行緒約 75 分鐘）。
- 之後任何輸入或演算法修改，都以這組設定的 HU benchmark 當回歸基準。

**下一步**：tight 只在翻牌前用強牌加注或跟注、翻牌後一律過牌或跟注，需要學會對它加注價值、少詐唬。接著試：更長訓練（50 萬手）、跨輪保留 RL buffer、對 tight 的評估加到 4,000 手以上（驗收標準）；若仍打平，再檢查動作抽象（目前加注只有半池、滿池、all-in）。

### 已決定
- 新增輸入格式 `poker-hunlhe-vector-v2`：
  - 牌力特徵：翻牌前加入兩張手牌大小、同花、對子、手牌類別；翻牌後加入目前成牌類型與最大牌、同花聽牌、順子聽牌、公牌結構（公牌成對、三張以上同花）。
  - **2–6 人通用**：對手固定 5 個槽位，依相對座位排列；兩人牌局只用一個槽位。
  - **數值以 BB 為單位**：籌碼、彩池、下注都除以 BB；另外提供實際 SB、BB、ante 與盲注等級。
  - **預留錦標賽欄位**（cash 時補 0）：剩餘人數、固定長度 6 的 payout 比例向量、bounty。
  - 只使用自己看得到的資訊，並延續既有的資訊隔離測試。
  - 詳細欄位見 [architecture/08-tournament.md](architecture/08-tournament.md) 第 3 節。
- v1 保留；checkpoint 已記錄輸入格式版本，舊模型照常載入。新 preset 使用 v2。

### 尚未決定
- 是否加入 Monte Carlo equity 特徵：牌型特徵已讓 R² 到 0.86–0.89，暫時不需要；P1-b 若仍卡在牌力判斷再考慮。
- 加入特徵並調整參數後仍無法穩定贏 tight 時：先檢查 NFSP 實作與動作抽象（目前加注只有半池、滿池、all-in），或提早引入其他演算法（例如 Deep CFR）。

### 下一步
1. ~~T1~~ 已完成（見上方「T1 6 人錦標賽環境」）。
2. ~~P1-b0~~ 已完成（見上方「P1-b0 訓練用的 UI」）。
3. **P1-b**（進行中，見上方「P1-b 進度」）：以 v2 為基礎調整訓練設定（每手訓練次數、Q 網路學習率與目標網路更新頻率、跨輪保留 RL buffer），長時間訓練並用不同 seed 重跑。自我對打在 CPU 較快，可以多個 seed 平行。

### 驗收條件
- **P1-a**：v2 的監督式 equity 測試 R² 明顯高於 v1 的 0.46；資訊隔離測試通過；兩人 NFSP 能用 v2 訓練。
- **P1-b**：對 4 種 Rule Bot 全部顯著獲勝（95% 信賴區間下限 > 0，每個對手至少 4,000 手評估），換不同 seed 重跑也成立；訓練設定與曲線記錄在本文件。
- 之後任何輸入或演算法修改，HU bb/100 benchmark 都不能變差（回歸測試）。

## 延後項目

### CUDA Worker 容器
這台電腦的 Docker Desktop 使用 Hyper-V 後端，無法把 GPU 交給容器（只有 WSL2 後端支援 `--gpus`）。另外，Docker 的網路層（gVisor）同時下載多個數百 MB 的 CUDA 套件時會停住，建置 `worker-cuda` 兩次都失敗。目前改在主機上執行 GPU Worker。要驗證 CUDA 容器時：
1. Docker Desktop 改用 WSL2 後端，以 `%USERPROFILE%\.wslconfig` 設定記憶體上限。
2. 先在主機下載好 CUDA 套件，建置時從本機安裝，不經過 Docker 網路。
3. `docker run --gpus all tableforge/worker-cuda doctor` 確認看得到 GPU，再加入 Compose 跑一次 GPU 實驗。

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
| Artifact 傳輸（本機 store） | Coordinator 不轉送大型 artifact | 使用 S3 時 Worker 直連；使用本機 store 時仍經由 Coordinator，單檔上限 1 GiB |
| Experience 格式 | Parquet shard | NPZ 形式的 zip（規格允許的替代格式，避免新增 pyarrow） |
| RL buffer 跨輪保留 | 保留最近幾輪資料 | 每輪只使用當輪 shard；reservoir buffer 跨輪保留 |

已修正的差異：NFSP 訓練改讀 dataset snapshot；experiment 改由背景推進；outbox 事件已加入；metadata 支援 PostgreSQL；artifact 支援 S3 並由 Worker 直連。

## 下一步候選

依建議優先順序：

使用者已確認的順序（D1 之後）：

1. **S1 ICM Push/Fold Nash baseline（先 SNG 再 PKO）＋ P2 模型比較**：完成（見上）。
2. **S4 可剝削程度量測**：完成（見上）。
3. **H2 練習模式**：完成（見上）。
4. **H1 匯入 GG／Natural8 牌譜**：完成，尚未用真實牌譜驗證（見上；有樣本後補驗收）。
5. **A0 演算法介面**：完成（見上）。
6. **encoder v3 與新動作格式（一次包含 S2 下注尺度、T4 9 人桌、S3 多結構訓練）**：正式訓練完成（三組都 +26% ~ +43%），驗收用的模型比較待做（見上）。
7. **新訓練演算法**：PPO、MMD 程式完成；小型實驗四組都是 15,000 場最好、之後退回 0（見上），下一步待使用者決定 → PSRO 族群訓練，與 NFSP 用同一套成績卡比較（跑正式訓練前先問算力預算）。
8. **T5 MTT 階段模擬**：完成（見上）；v4 模型的訓練待使用者決定算力預算。
9. **S6 對手建模**。

其他（延後）：與測試者實機驗收、Worker 開機自動啟動、CUDA Worker 容器。

長期願景（包括最終的聯邦式對等網路 D1）見 [vision.md](vision.md)，不屬於目前的工作計畫。
