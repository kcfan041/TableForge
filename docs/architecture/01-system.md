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

### 6.1 決策說明介面（重播與策略表共用）

重播、策略表等分析功能只透過 `DecisionExplainer`（`learning/explain.py`）詢問模型，不依賴任何演算法的內部結構；新增演算法時實作這個介面即可，畫面不需要修改。

```python
class DecisionExplainer(Protocol):
    play_modes: tuple[PlayMode, ...]          # 模型可用的打法，第一個是預設
    def begin_hand(self, info: TournamentHandInfo) -> None: ...
    def explain(self, observation, legal, mode) -> DecisionExplanation: ...
    def act(self, observation, legal, mode) -> Action: ...
```

- `DecisionExplanation.probabilities`（必填）：四個統一動作 `fold`、`check_call`、`raise`、`all_in` 的機率，加總為 1。隨機策略給分布；確定性策略與 Rule Bot 給選中的動作 1.0。有多種加注尺寸的演算法把總和放在 `raise`，細分放在 `raise_sizes`。
- `details`（選填）：演算法特有的數值，各自帶名稱與單位，可以是每個動作一個值或整個局面一個值（例如 NFSP 的 Q 值、PPO 的局面價值、Deep CFR 的 regret）。UI 以通用表格顯示。
- `explain` 不得改變模型，且必須能回答沒有在實際對局中出現的局面（策略表會自行構造局面）。
- 目前的實作：`NfspExplainer`（打法：平均策略、最佳回應；details 為各動作 Q 值，錦標賽單位是 buy-in）、`RuleBotExplainer`。
- 分析 Job：`TournamentReplayJobPayload`（一整場錦標賽的完整牌譜與模型座位的決策說明）、`StrategyTableJobPayload`（翻牌前 169 種起手牌在各位置、籌碼深度與 bounty 倍數下的打法）。兩者不屬於任何實驗，以內容做冪等，同一個請求只算一次。

### 6.2 演算法介面（A0，已實作）

演算法以外的程式一律透過 `learning/algorithms.py` 的 `AlgorithmSpec` 使用模型，不直接寫出演算法名稱：

| 欄位 | 用途 | 使用者 |
|---|---|---|
| `key`、`label` | 模型與 checkpoint 上記錄的演算法名稱 | 模型庫、實驗設定 |
| `checkpoint_schema` | checkpoint artifact 的 schema | 上傳模型、對手池 |
| `play_modes` | 打法（第一個是預設） | UI、重播、策略表、對打、比較 |
| `explainer(checkpoint)` | 一個座位的 `DecisionExplainer` | 重播、策略表、對打、練習、牌譜檢討 |
| `player(checkpoint, mode)` | 快速的牌桌玩家工廠（不產生說明） | 對手池、benchmark、模型比較、可剝削程度 |
| `executors(fetch)` | Worker 的 Job executor（依 payload type） | Worker 註冊 |

- 註冊：`ALGORITHMS`（Worker 端）與 `coordinator/experiments.ORCHESTRATORS`（Coordinator 端，每個演算法的輪次流程：actor → learner → 評估 → 採用）。實驗設定新增 `algorithm`（預設 `nfsp`），學習者的 agent id 是 `<algorithm>:<實驗 id>`。
- 模型庫的模型、對手池成員（`PoolModel.algorithm`）、模型比較的參賽者（`Competitor.algorithm`）都帶演算法；只有不是 NFSP 時才寫進 payload，所以現有的 Job payload 與冪等鍵完全不變。
- 匯入很輕：只有真的載入模型時才 import PyTorch，Coordinator 與 Rule Bot Worker 可以使用 registry。
- 位元一致驗證：`scripts/algorithm_baseline.py` 端對端跑三個小型 NFSP 實驗（錦標賽、含對手池與歷史版本的錦標賽、HU 含 League population），記錄每個 Job 的 payload 與輸出的 sha256（artifact id 以佔位符取代）；`tests/fixtures/nfsp_fingerprint.json` 是導入介面前錄下的結果，`tests/integration/test_nfsp_fingerprint.py` 每次都比對（錄製的 Windows 上全部比對；其他平台的 PyTorch 浮點結果不同，只比對 Job 流程與未訓練的 round 0 payload）。
- 新增演算法時：實作一個 `AlgorithmSpec` 與它的 executor，加入 `ALGORITHMS`；實作 coordinator 端的 `Orchestrator`（`reconcile`、`matrix`），加入 `ORCHESTRATORS`；新增 workload profile。其餘功能（模型頁、重播、策略表、對打、練習、牌譜檢討、模型比較、對手池、可剝削程度）自動支援。

### 6.3 PPO（A1，已實作）

- 模型：`learning/ppo.py` 的 `PpoAgent`，策略網路與價值網路（和 NFSP 相同的兩層結構），任何 encoder（v2、v3）與動作格式（v1、v2）。checkpoint schema `tableforge.ppo-checkpoint.v1`（權重、Adam 狀態、亂數狀態），沒有 replay buffer。
- 學習：actor 用本輪 checkpoint 打錦標賽（on-policy），每個學習座位的決策依序記錄；獎勵和 NFSP 相同（每手後的錦標賽權益變化，`hand_horizon` 時一手為一個 episode）。learner 用同一個 checkpoint 重新計算行為機率與 V(s)，以 GAE 算 advantage，做幾個 epoch 的 clipped PPO 更新（加上熵獎勵與價值損失）。
- 平台：沿用 NFSP 的輪次流程與 payload（`IterativeNfspOrchestrator` 以 `PipelineJobs` 換成 PPO 的 Job 類型：`PpoInitJobPayload`、`PpoActorJobPayload`、`PpoLearnerJobPayload`、`PpoMatchJobPayload`）；經驗資料格式相同（只有 transitions）；learner 只接受本輪 checkpoint 產生的經驗。實驗的 `train_steps_per_round` 是每輪 epoch 數，`batch_size` 是 minibatch 大小；新設定 `entropy_coef`、`gae_lambda`、`clip`。
- 評估：和 NFSP 同一套錦標賽 benchmark 與升級規則；打法 `sample`（依機率，預設）與 `greedy`（最可能的動作），訓練曲線的系列名稱也是這兩個。說明器回傳各動作機率與狀態價值（單位 buy-in）。
- **MMD 正則化**（magnetic mirror descent）：`magnet_coef`（α）大於 0 時，損失多一項 α·KL(π‖magnet)，只算合法動作；magnet 是另一份策略網路，每 `magnet_every` 次更新（平台上是每幾輪）換成當下的策略。用來防止自我對打在幾種打法之間循環。α = 0 就是一般 PPO：不建立 magnet，checkpoint、payload 與訓練結果和加入 MMD 前位元一致（設定只在不是預設值時寫出）。MMD 的 checkpoint 另存 magnet 權重與更新次數；沒有 magnet 權重的舊 checkpoint 以目前策略當 magnet。
- **價值網路訓練方式**（`value_mode`）：`shared`（預設，最初的做法）：策略與價值共用一個 Adam 與梯度上限，價值損失是 Huber。`separate`：價值網路有自己的 Adam（`value_learning_rate`，預設 1e-3）、梯度上限（`value_max_grad_norm`，5）與均方誤差損失，每批在策略更新之後另外訓練 `value_epochs` 遍（8）。診斷發現共用時價值網路只解釋 5–15% 的回報，優勢幾乎全是雜訊；分開後約 40%（`docs/status.md` A1）。`separate` 的 checkpoint 另存價值網路的 Adam 狀態（`value_opt.*`）。
- **診斷**：每次 `learn` 的 `last_diagnostics`（策略變化量 `approx_kl`、被截掉比例 `clip_fraction`、價值網路更新前後的解釋程度、優勢標準差、平均回報、兩個網路的梯度大小）；只讀取，不影響訓練。
- 尚未支援：League、可剝削程度實驗（從目標模型開始）、HU cash。PSRO 是下一步。

## 7. 擴展邊界

- 新演算法：見 6.2，新增 `AlgorithmSpec`、executor、orchestrator 與 workload profile，不改 Job lifecycle。
- 6-Max：擴充 GameConfig 與 Poker Engine 測試，不改 Worker protocol。
- 多 Coordinator：未來以 replicated event log/consensus 替換 MetadataRepository；Worker protocol 與 JobSpec 不變。
- 多租戶：在所有 aggregate ID 上加入 namespace/owner authorization，不改 artifact hash。
- Rust 加速：以 Python extension 實作 evaluator/simulation adapter，domain object 與測試向量保持一致。
