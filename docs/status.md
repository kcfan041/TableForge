# TableForge 開發進度

最後更新：U1 單一入口 `tableforge ui`：建立或加入叢集、背景叢集服務。每完成一個階段，本文件與程式碼在同一個 commit 更新。

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
| U1 | 單一入口：第一次開啟選「建立／加入」，建立叢集全自動、背景叢集服務、建立者自動成為 Worker | 已完成 | 本階段 |
| U2 | `tableforge ui` 內的叢集管理頁面（依權限顯示）、`ControlService` 補齊、admin 邀請碼 | 未開始 | 下一個 |
| U3 | Coordinator 遠端模式共用同一套 UI 並預設關閉；文件改成只介紹 `tableforge ui` | 未開始 | U2 之後 |
| O0–O7 | 對手建模研究線 | 未開始 | M5 之後 |
| P1 | 模型強度驗證 | 暫停 | 已找到主要瓶頸；先完善平台（見下方「P1 模型強度」） |

目前測試：444 個測試全部通過（含 RTX 5070 Ti 實機 GPU 測試、Docker PostgreSQL 17 與 Docker RustFS 上的測試），Ruff、mypy strict 通過。沒有 GPU、PostgreSQL 或 S3 服務時，相關測試會自動略過。

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

## 暫停中：P1 模型強度

為了讓測試者先使用平台，這項工作暫停在「已找到主要瓶頸、尚未修正」的狀態。回來時從「下一步」接續。

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

### 已決定
- 新增輸入格式 `poker-hunlhe-vector-v2`：保留 v1 全部欄位，另加牌力特徵。翻牌前加入兩張手牌大小、同花、對子、手牌類別；翻牌後加入目前成牌類型與最大牌、同花聽牌、順子聽牌、公牌結構（公牌成對、三張以上同花）。只使用自己看得到的資訊，並延續既有的資訊隔離測試。
- v1 保留；checkpoint 已記錄輸入格式版本，舊模型照常載入。新 preset 使用 v2。

### 尚未決定
- 是否加入 Monte Carlo equity 特徵：效果可能最好，但會拖慢對局，需要先量速度。建議先只加牌型特徵。
- 加入特徵並調整參數後仍無法穩定贏 tight 時：先檢查 NFSP 實作與動作抽象（目前加注只有半池、滿池、all-in），或提早引入其他演算法（例如 Deep CFR）。

### 下一步（回來時）
1. 實作 `poker-hunlhe-vector-v2` 與對應測試，重跑上面的監督式測試確認 R² 提升。
2. 調整訓練設定：每手訓練次數、Q 網路學習率與目標網路更新頻率；跨輪保留 RL buffer。
3. 在 5070 Ti 上長時間訓練，並用不同 seed 重跑。

### 驗收條件
對 4 種 Rule Bot 全部顯著獲勝（95% 信賴區間下限 > 0，每個對手至少 4,000 手評估），換不同 seed 重跑也成立；訓練設定與曲線記錄在本文件。

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

1. **U2、U3（下一個）**：見上方總覽。U2 讓 `tableforge ui` 依權限顯示叢集管理頁面（透過 gRPC `ControlService`，加入者與建立者走同一條路），並補齊事件、診斷與管理者功能；admin 邀請碼需要再次確認、最多 1 小時有效、使用時記錄。U3 讓 Coordinator 的遠端模式共用同一套 UI 並預設關閉，文件改成只介紹 `tableforge ui`。
2. **學習效果驗證**：見上方「暫停中：P1 模型強度」，從「下一步（回來時）」接續。多演算法同桌對戰與 NFSP 多人化都以此為前提。
3. **與測試者實機驗收**：S0 的程式、流程與指南已完成，等測試者可以配合時進行。測試者之間不做隔離；使用者、專案與隔離屬於完整版（S1–S5）。
4. **PFSP**：依 matchup 勝率調整對手權重（目前為固定權重）。
5. **多演算法同桌對戰**：新增 Deep CFR 與 PPO executor，加入 League 與 matchup matrix。
6. **Worker 背景執行**：開機自動啟動、失敗自動重啟（延後，視測試者回饋）。
7. **CUDA Worker 容器**（需要 WSL2 後端）。
8. **O0–O7 對手建模**。

長期願景（包括最終的聯邦式對等網路 D1）見 [vision.md](vision.md)，不屬於目前的工作計畫。
