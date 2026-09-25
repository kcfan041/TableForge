# TableForge 開發進度

最後更新：M3b Docker 部署階段（第一版收尾完成）。每完成一個階段，本文件與程式碼在同一個 commit 更新。

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
| C5 | UI 呈現能力資訊 | 未開始 | 平台化階段 |
| M3a | gRPC 傳輸、enrollment/session、TLS、artifact 傳輸 | 已完成 | `61cf545`、`b5c4c51` |
| M3a 第 2 階段 | Mac 實機驗證、跨平台結果一致性 | 延後 | 需在 Mac 上執行 |
| M3a 實機 | 兩台電腦實際連線驗收 | 延後 | 需要 Tailscale |
| 營運 | 設定分層、outbox、metrics、artifact GC、session 輪換/撤銷 | 已完成 | `5e76237`、`e845d0c` |
| M3b-1 | PostgreSQL metadata、schema migration、SQLite 資料搬移 | 已完成 | `817e9f7` |
| M3b-2 | S3 相容 artifact store、Worker 直連 storage | 已完成 | `5f7e613` |
| M3b-3 | Docker 映像與 Compose 部署、主機 GPU Worker | 已完成 | 本階段 |
| M3b-3 CUDA 容器 | GPU Worker 在容器內執行 | 延後 | 需要 Docker Desktop 的 WSL2 後端 |
| M4 | NFSP 多輪訓練、評估、promotion（CPU） | 已完成 | `5abd094`、`c897b37`、`bf77238` 及本階段 |
| M5 | League、歷史模型、matchup matrix、1,000 萬手 soak | 已完成 | 本階段 |
| O0–O7 | 對手建模研究線 | 未開始 | M5 之後 |

目前測試：314 個測試全部通過（含 RTX 5070 Ti 實機 GPU 測試、Docker PostgreSQL 17 與 Docker RustFS 上的測試），Ruff、mypy strict 通過。沒有 GPU、PostgreSQL 或 S3 服務時，相關測試會自動略過。

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

1. **學習效果驗證**：用 5070 Ti 跑較長的訓練與較多評估手數，確認模型相對 Rule Bots 有顯著進步。目前第 4 輪仍輸給 Tight bot。後續的新演算法與 6-Max 都以此為前提。
2. **多人共用（模式 B：信任的小團體）**：依 `architecture/07-multi-party-and-trust.md` 的 S0–S5 進行。S0 即兩台電腦實機驗收（需要私有網路，例如 Tailscale）與 Mac 驗證。
3. **PFSP**：依 matchup 勝率調整對手權重（目前為固定權重）。
4. **多演算法同桌對戰**：新增 Deep CFR 與 PPO executor，加入 League 與 matchup matrix。需要第 1 項已證明 NFSP 會變強，作為比較基準。
5. **6-Max**：撲克引擎支援最多 6 人，並以同樣嚴格的 property test 與 soak test 驗證；多演算法可真正同坐一桌。
6. **網頁介面**：呈現節點與硬體能力、實驗、checkpoint 與 matchup matrix；只呈現 Application 層的判定結果，不阻塞 CLI。多人共用的權限管理完成後再加入管理功能。
7. **CUDA Worker 容器**（需要 WSL2 後端）。
8. **O0–O7 對手建模**。

長期願景（包括最終的聯邦式對等網路 D1）見 [vision.md](vision.md)，不屬於目前的工作計畫。
