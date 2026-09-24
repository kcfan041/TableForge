# TableForge 實作架構

## 1. 目標與邊界

本文件定義第一個可執行版本，而非概念藍圖。第一版必須支援：

- 單機與兩節點使用同一套程式
- Heads-Up No-Limit Texas Hold'em
- Rule Agent 與 NFSP Agent
- Simulation Job、Evaluation Job、Training Job
- immutable checkpoint 與 content-addressed artifact
- Coordinator 故障後可恢復，但第一版不提供同時多主寫入
- Worker 斷線、任務逾時、重複提交不破壞狀態

第一版不實作：multi-master、gossip、第三方任意程式碼、Web UI、6-Max、Deep CFR、PPO。這些功能必須能透過既有介面加入，而不是修改核心狀態模型。

## 2. 執行拓撲

```text
                         Control Plane
                 ┌────────────────────────┐
                 │ Coordinator API        │
                 │ Scheduler              │
                 │ Registry               │
                 │ League Service         │
                 └───────────┬────────────┘
                             │ gRPC
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
       │ Sim Worker  │ │ Learner  │ │ Eval Worker │
       │ CPU         │ │ GPU      │ │ CPU/GPU     │
       └──────┬──────┘ └────┬─────┘ └──────┬──────┘
              └──────────────┼───────────────┘
                             │
                  ┌──────────▼──────────┐
                  │ Artifact Store      │
                  │ local FS / S3       │
                  └─────────────────────┘
```

單機模式將上述元件放在同一台機器，但仍透過相同 service interface 操作。測試模式可使用 in-process adapter；正式執行不允許 Worker 直接讀 Coordinator 的資料庫或本機路徑。

## 3. Repository 結構

```text
TableForge/
├── pyproject.toml
├── uv.lock
├── proto/
│   └── tableforge/v1/
│       ├── common.proto
│       ├── coordinator.proto
│       ├── jobs.proto
│       └── artifacts.proto
├── src/tableforge/
│   ├── domain/
│   │   ├── ids.py
│   │   ├── jobs.py
│   │   ├── agents.py
│   │   ├── artifacts.py
│   │   ├── experiments.py
│   │   └── events.py
│   ├── poker/
│   │   ├── cards.py
│   │   ├── evaluator.py
│   │   ├── betting.py
│   │   ├── pots.py
│   │   ├── state.py
│   │   ├── observation.py
│   │   └── environment.py
│   ├── agents/
│   │   ├── protocol.py
│   │   ├── rule/
│   │   └── nfsp/
│   ├── coordinator/
│   │   ├── service.py
│   │   ├── scheduler.py
│   │   ├── league.py
│   │   └── registry.py
│   ├── worker/
│   │   ├── runtime.py
│   │   ├── simulation.py
│   │   ├── evaluation.py
│   │   └── learner.py
│   ├── application/
│   │   ├── ports.py
│   │   └── commands.py
│   ├── infrastructure/
│   │   ├── sqlite/
│   │   ├── postgres/
│   │   ├── local_artifacts.py
│   │   ├── s3_artifacts.py
│   │   └── grpc/
│   └── cli/
├── migrations/
├── configs/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   └── failure/
└── deploy/
    ├── compose.yaml
    └── containers/
```

`domain` 不依賴 gRPC、SQLAlchemy、PyTorch 或檔案系統。`application` 定義 use case 與 port；`infrastructure` 實作 port。如此可將 SQLite 換成 PostgreSQL、本機 artifact 換成 S3，而不改 Scheduler 或 League 邏輯。

## 4. 元件責任

### Coordinator

唯一的第一版 metadata writer，負責：

- Node 註冊與 heartbeat
- Job 建立、claim、lease renewal、完成與失敗
- Experiment revision 驗證
- Agent/checkpoint/artifact metadata
- League matchmaking 與 checkpoint promotion
- outbox event 發布

Coordinator 不執行牌局、不載入神經網路、不代理大型 artifact bytes。

### Worker Runtime

每個 Node 啟動一個 Runtime，宣告 capability：

```json
{
  "cpu_cores": 32,
  "memory_bytes": 68719476736,
  "gpus": [{"device": "cuda:0", "memory_bytes": 34359738368}],
  "roles": ["simulation", "evaluation", "nfsp_learner"],
  "runtime_version": "0.1.0"
}
```

Runtime 依序執行：capability discovery、heartbeat、poll/claim、下載 input artifact、驗證 hash、執行前 preflight、執行、上傳 output、complete。每種 executor 必須可被單獨停止，且不得改寫 input artifact。Capability snapshot 需版本化；Job attempt 記錄 claim 時使用的 capability revision。

### Scheduler

Scheduler 只根據 JobSpec、NodeCapability 與 versioned WorkloadProfile 配對，不理解 Poker 演算法內部。配對條件包括 role、CPU、RAM、GPU 類型、VRAM、runtime 與已保留資源。Scheduler 輸出結構化 CompatibilityAssessment；只有 Experiment 明確允許的 fallback 才能產生 degraded execution plan。第一版採 pull model：Worker 主動要求任務，避免 Coordinator 需要連入 NAT 後方節點。

### League Service

League 將 experiment population 轉成 immutable MatchSpec。MatchSpec 固定 seats、checkpoint hashes、game config、seed range 與 hand count。開始後即使有新 checkpoint，也不得改變既有 match。

### Artifact Store

介面必須提供：

```python
class ArtifactStore(Protocol):
    def begin_upload(self, descriptor: ArtifactDescriptor) -> UploadGrant: ...
    def commit_upload(self, artifact_id: str, sha256: str, size: int) -> Artifact: ...
    def get_download(self, artifact_id: str) -> DownloadGrant: ...
    def exists(self, sha256: str) -> bool: ...
```

第一版 LocalArtifactStore 供單機使用；兩節點部署使用 MinIO。metadata 只保存 URI、hash、size、media type 與狀態。

## 5. Poker 核心界面

```python
class PokerEnvironment(Protocol):
    def reset(self, spec: HandSpec) -> Mapping[Seat, Observation]: ...
    def legal_actions(self, seat: Seat) -> tuple[LegalAction, ...]: ...
    def step(self, seat: Seat, action: Action) -> StepResult: ...
    def snapshot(self) -> PublicSnapshot: ...
```

Environment 內部狀態與 Observation 分離。Agent 只收到 `Observation`、`LegalAction`、`DecisionContext`。訓練 transition 必須由 seat-specific observation 產生，不得從完整 HandState 序列化後再由 learner 過濾。

下注 amount 在 domain 層一律表示 `raise_to` 的總 street contribution，避免 `raise_by` 與 pot fraction 歧義。抽象 action 先由 ActionTranslator 轉為 chip amount，再交由 BettingRules 驗證。低於 minimum raise 的非 all-in action為非法；短碼 all-in 可不重開已行動玩家的 raise 權。

## 6. Agent 執行界面

```python
class Agent(Protocol):
    @property
    def manifest(self) -> AgentManifest: ...
    def act(self, observation: Observation, legal_actions: tuple[LegalAction, ...], context: DecisionContext) -> Action: ...
    def on_transition(self, transition: AgentTransition) -> None: ...
    def on_hand_end(self, result: AgentHandResult) -> None: ...
```

訓練器與推論 Agent 分離：

```python
class Learner(Protocol):
    def consume(self, shards: Sequence[ArtifactRef]) -> TrainMetrics: ...
    def checkpoint(self) -> CheckpointBundle: ...
```

CheckpointBundle 至少包含 model、optimizer、algorithm state、schema version、code version、parent checkpoint、training counters 與 manifest。推論程序只需 model 與 manifest；不得被迫下載 optimizer state。

## 7. 擴展邊界

- 新演算法：新增 executor、AgentManifest schema extension、artifact type，不改 Job lifecycle。
- 6-Max：擴充 GameConfig 與 Poker Engine 測試，不改 Worker protocol。
- 多 Coordinator：未來以 replicated event log/consensus 替換 MetadataRepository；Worker protocol 與 JobSpec 不變。
- 多租戶：在所有 aggregate ID 上加入 namespace/owner authorization，不改 artifact hash。
- Rust 加速：以 Python extension 實作 evaluator/simulation adapter，domain object 與測試向量保持一致。
