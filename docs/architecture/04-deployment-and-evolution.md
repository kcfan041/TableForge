# 部署、設定與演進

## 1. 發布單位

第一版發布三個 container image，皆來自同一 monorepo 與版本：

```text
tableforge-coordinator:<version>
tableforge-worker:<version>
tableforge-cli:<version>
```

Worker image 可包含 CPU runtime；GPU 另建 CUDA variant：

```text
tableforge-worker:<version>-cpu
tableforge-worker:<version>-cuda12
```

映像以 digest 部署，不使用浮動 `latest`。Python dependencies 由 lock file 固定。使用者不需安裝 Python/Rust/Node；只需 Docker。GPU Node 額外安裝相容的 NVIDIA Driver 與 NVIDIA Container Toolkit。

## 2. 單機開發部署

Compose services：

```text
coordinator
worker-cpu
db-volume
artifact-volume
```

使用 SQLite 與 LocalArtifactStore。即使是單機，Worker 仍使用 Coordinator gRPC API，artifact 透過 artifact service/grant 存取，不 mount Coordinator 的 metadata DB。

設定範例：

```yaml
node:
  id_file: /var/lib/tableforge/node-id
  roles: [simulation, evaluation]
coordinator:
  endpoint: coordinator:7443
storage:
  backend: local
  root: /var/lib/tableforge/artifacts
metadata:
  backend: sqlite
  dsn: /var/lib/tableforge/tableforge.db
scheduler:
  default_lease_seconds: 300
  heartbeat_seconds: 30
```

## 3. 兩節點部署

### Node A

- Coordinator
- PostgreSQL
- MinIO
- GPU Worker/Learner（如有 GPU）

### Node B

- CPU/GPU Worker
- 本地 artifact cache
- 不配置 DB credentials

兩台透過 Tailscale 或 WireGuard 私有位址連線。只開放 Coordinator gRPC、MinIO presigned endpoint；PostgreSQL 不對 Node B 或 Internet 開放。

```text
Node B Worker ──gRPC/TLS──> Node A Coordinator
Node B Worker ──HTTPS─────> Node A MinIO
```

備份至少涵蓋 PostgreSQL、MinIO bucket、Coordinator signing keys。Worker cache 不需備份。

## 4. 設定分層

優先順序：

```text
default < config file < environment < CLI flag
```

Experiment config 與 infrastructure config 分離。前者需 revision/hash 並進入可重現紀錄；後者包含 endpoint、cache path、concurrency，不應改變實驗語義。

禁止在 YAML 中保存 token、password、private key。Secret 使用 Docker secret、Kubernetes secret 或 host secret file mount。

## 5. 可觀測性

所有服務輸出 structured JSON log：

```text
timestamp
level
service
node_id
request_id
experiment_id
job_id
attempt_id
event
error_code
duration_ms
```

Metrics 使用 Prometheus exposition format：

- jobs queued/running/succeeded/failed
- lease expiration count
- simulation hands/sec
- invalid hands
- artifact upload/download bytes與 failures
- learner samples/sec、steps/sec、GPU memory
- RPC latency/error rate
- cache hit ratio

Trace context 透過 gRPC metadata 傳遞。第一版可不部署完整 tracing backend，但 request/job/attempt correlation ID 必須存在。

## 6. 容量與背壓

Coordinator 不承載 experience bytes。Worker 本地 bounded queue；artifact upload 速度低於資料產生速度時暫停新 simulation job。每個 Node 設定：

```text
max_concurrent_jobs
max_cpu_workers
max_gpu_jobs
artifact_cache_bytes
local_spool_bytes
```

Scheduler 以已租用資源扣除 available capability，避免同一 GPU 被多個 Job 過度承諾。OOM 不以無限制 retry 處理；記錄 peak memory 並依 policy 降低 batch 或送往更大 Node。

## 7. Artifact 保留與垃圾回收

保留政策：

- active/historical checkpoint：永久或明確 retire
- training-state checkpoint：依最近 N 版與時間窗口
- experience shards：被 dataset snapshot 引用期間不可刪
- evaluation result：永久
- diagnostic artifacts：依 retention days
- orphan upload：grace period 後刪除

GC 採 mark-and-sweep：由 metadata 中的 checkpoint、dataset snapshot、job output 與 retention pin 標記 reachable hash，再刪除過 grace period 的 unreachable object。不得只依目錄日期刪檔。

## 8. 資料庫遷移

Migration 單向、編號且由 Coordinator 啟動前執行。正式環境先備份；不允許兩個不同版本 Coordinator 同時 migration。可回滾應透過 restore backup，不假設所有 schema migration 都可逆。

SQLite 適合單 Coordinator 與開發。進入兩節點長期運行前切換 PostgreSQL，因為 lease/claim/outbox 需要更可靠並發。切換透過 repository export/import 工具，artifact bytes 不移動。

## 9. 開發里程碑與驗收

### M0：契約骨架

- Python package、Protobuf generation、domain IDs
- Job/Artifact/Repository ports
- SQLite 與 local artifact adapter
- contract tests

### M1：Poker Engine

- Heads-Up engine 與 observation isolation
- Random/Call/Tight/Aggro bots
- deterministic seed
- 1M hands invariant test

### M2：單機工作流

- Coordinator gRPC
- Worker runtime
- simulation/evaluation jobs
- lease、heartbeat、retry、artifact manifest
- Node capability discovery、`tableforge doctor` 與 CPU-only compatibility tests
- Coordinator/Worker restart tests

### M3：兩節點

- PostgreSQL、MinIO、TLS/private network
- remote claim與artifact transfer
- Node loss、network interruption、duplicate completion tests

### M4：NFSP

- versioned observation encoding
- replay/reservoir dataset snapshots
- learner jobs與immutable checkpoint
- candidate evaluation/promotion

### M5：League

- current/historical/baseline population
- reproducible matchmaking
- matchup matrix與CI
- 10M-hand soak test

只有 M3 穩定後才開始 federation research。只有 M5 能穩定重現後才加入 PPO/Deep CFR/6-Max。

Capability線與主里程碑交錯執行：M2完成C0–C1（domain、discovery、doctor），第一個GPU learner前完成C2–C4（GPU probe、測試tier、Scheduler filtering、WorkloadProfile與fallback）；C5 UI呈現屬平台化階段。核心判定由Application/Scheduler提供，CLI-first，Dashboard不阻塞MVP。

使用者最小流程為：

```text
tableforge doctor
tableforge node start
tableforge experiment create --preset <name>
tableforge experiment start <id>
tableforge experiment status <id>
```

## 10. 從單 Coordinator 演進至 Federation

第一版刻意保留以下不變契約：JobSpec、Attempt fencing、ArtifactRef、Checkpoint identity、Experiment revision、domain event envelope。演進順序：

1. 將 Coordinator 變成可水平讀取，但仍單 writer。
2. PostgreSQL HA 與 object storage replication，先消除基礎設施單點。
3. 以 consensus leader 管理 command writes；Worker 可連任一 endpoint。
4. 若確實需要跨地點自治，再引入 experiment home/owner 與 event replication。
5. 最後才評估 multi-master；衝突範圍限定在不同 experiment，單一 experiment 保持單 owner/leader。

不建議讓任意 Node 同時 claim 同一全域 Job 並靠 eventual consistency 解衝突。Job ownership、experiment revision 與 promotion 是強一致資料；agent discovery、metrics replica、artifact location 可採 eventual consistency。

## 11. 平台化演進

多使用者版本新增：

- Identity Provider/OIDC
- project/namespace 與 RBAC
- quota、billing/accounting
- submitted Agent sandbox與image scanning
- REST gateway與Dashboard
- audit retention與administrative API

具體範圍、分階段路線（S0–S5）與驗收條件見 `07-multi-party-and-trust.md`；第一個目標是信任的小團體共用一個叢集。

這些功能位於 control plane 外層。Poker Engine、Worker executor、Job lifecycle、ArtifactRef 不應因加入 UI 或帳號系統而改寫。

## 12. 技術選擇

第一版固定：

```text
Python 3.12
PyTorch
NumPy
Protobuf + gRPC
SQLAlchemy + Alembic
SQLite（開發）/ PostgreSQL（兩節點）
Local filesystem（開發）/ MinIO（兩節點）
Docker Compose
pytest + Hypothesis
Prometheus metrics
```

Rust 僅在 profiler 證明 evaluator、betting 或 traversal 為瓶頸後加入。Dashboard 才需要 TypeScript。Kubernetes、NATS、Gossip 與 Multi-Master 均不是第一版依賴。
