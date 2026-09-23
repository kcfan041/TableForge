# 長期對手建模、跨手牌記憶與自適應剝削

## 1. 目標與非目標

本文件定義 TableForge 在既有 Job、Artifact、Checkpoint、Experiment revision、Worker runtime 與 Protobuf 邊界內加入 opponent modeling 的方法。此能力是 M0–M5 之上的擴充研究線，不得阻塞第一版 Heads-Up、Rule Bots、NFSP 與兩節點 MVP。

目標：

- 在跨手牌且換座位後維持穩定的 session participant identity。
- 僅以決策當時可觀察資訊建立統計、標籤條件與 learned representation。
- 支援 session memory、long-term profile、匿名模式與 ablation。
- 在 at-least-once Job execution 下提供可重現、可重試且不重複計入的更新。
- 保留不同 Session 的平行能力，明確表示同一 Session 內的時間依賴。
- 評估 adaptive policy 的收益、反應速度與 profile 錯誤風險。

非目標：

- 不修改既有 Job lifecycle、lease、fencing 或 Artifact 上傳流程。
- 不把 AgentId 當作真人或平台帳號身份。
- 不從行為直接推論人格、情緒或 Tilt；只描述 observable strategy distribution shift。
- 不保證 adaptive policy 優於 equilibrium-oriented baseline。
- 不在第一階段實作 Meta-RL、任意第三方 Agent 或跨 Coordinator mutable profile。

## 2. 核心概念

```text
Experiment
└── Session
    ├── SessionParticipant
    ├── SessionSegment 1
    │   └── Hand 1..N
    ├── SessionSegment 2
    │   └── Hand N+1..M
    └── ...
```

`Match` 保留既有 evaluation matchup 與 immutable MatchSpec 語義，不作為跨手牌記憶的 identity container。Session 可引用一個 MatchSpec template，但 Session、Match 與 Table 不互為 identity。

核心物件：

- `Session`：固定 participant set、規則與有序手牌範圍。
- `SessionParticipant`：某個 Agent checkpoint 在該 Session 中扮演的穩定邏輯玩家。
- `OpponentProfileRevision`：某個 profile subject 的不可變長期 profile revision。
- `SessionMemoryState`：完成某個 segment 後的 session-level deterministic state。
- `OpponentContextSnapshot`：Agent 在特定 hand boundary 可讀的 immutable context。
- `ProfileObservationDelta`：Simulation 從合法可見事件產生、供後續聚合的增量。
- `PrivatePolicyMemory`：某個 Agent instance 的 recurrent hidden state，不屬於共享 opponent context。

## 3. Session 與 Participant Identity

### 3.1 Identity 分層

```text
AgentId
    演算法/策略 lineage，不表示玩家身份

CheckpointId
    immutable 模型版本

ParticipantId
    Experiment 範圍內的 pseudonymous profile subject

SessionParticipantId
    Participant 在特定 Session 的 participation identity

Seat
    單手牌或 seat schedule 中的位置
```

`ParticipantId` 可跨 Session 保留，用於 known-identity long-term profile；其 namespace 限定於 Experiment，不能映射為真人身份。`SessionParticipantId` 每次 Session 建立，永不跨 Session 重用。

同一 `AgentId + CheckpointId` 可以同時建立多個 Participant 或 SessionParticipant。這可避免 identity 洩漏 checkpoint，也支援同模型扮演不同邏輯玩家。反之，同一 Participant 可在明確 policy 設定下跨 Session 使用不同 Checkpoint，但 profile provenance 必須記錄各 observation 的 checkpoint。

### 3.2 Seat Rotation

SessionParticipant 與 seat 分離。Session 包含 deterministic `SeatSchedule`：

```text
hand_index → SessionParticipantId → seat
```

換座位只改 SeatAssignment，不改 SessionParticipantId 或 ParticipantId。Observation 只能看見當前 hand 允許公開的 opponent handle；不能因 seat rotation 暴露 AgentId 或 CheckpointId。

### 3.3 Identity Visibility

Session 的 `IdentityMode`：

- `KNOWN_PSEUDONYM`：提供 SessionParticipantId；若 policy 允許，可解析為跨 Session ParticipantId 並讀取固定 long-term profile revision。
- `SESSION_ONLY`：只提供 SessionParticipantId，不提供跨 Session linkage。
- `ANONYMOUS`：Agent 只取得當前 Session 的隨機 opponent handle；不同 Session 不可連結。
- `NO_PROFILE`：不提供 opponent context，作為 baseline/ablation。

匿名化是輸入契約，不是只在 UI 隱藏欄位。Snapshot 與 experience 不得包含可反推長期 ParticipantId 的 identifier。

## 4. Session Lifecycle

```text
CREATED → READY → RUNNING → COMPLETED
                    ├─────→ PAUSED → RUNNING
                    ├─────→ FAILED
                    └─────→ CANCELLED
```

Session 建立時固定：

- `SessionId`、ExperimentId 與 Experiment revision
- GameConfig hash
- participant list 與 checkpoint hashes
- identity/profile visibility mode
- deterministic seat/seed schedule
- hand count 與 segment size
- initial profile revisions
- context/stat schema versions

Session immutable spec 建立後不得改寫。Pause/Resume 只控制尚未建立或尚未執行的 segment，不改變 seed、participant 或 profile input。

### 4.1 Segment

一個 5000-hand Session 可拆成 50 個 100-hand `SessionSegment`。每個 segment 對應既有 Simulation Job，其 immutable payload 增加：

```text
session_id
segment_index
hand_start_inclusive
hand_end_exclusive
previous_session_state ArtifactRef（segment 0 可省略）
initial_context_snapshot refs
```

同一 Session 的 segment 形成 dependency chain：segment N 只有在 N-1 成功並產生 SessionMemoryState 後才進入 QUEUED。不得讓 segment N 在缺少正確 predecessor state 時執行。不同 Session 完全平行；系統吞吐由 independent-session parallelism 提供。

若不需要 session memory，Session 可標記 `STATELESS_SEGMENTS`，此時 segments 可平行，但不能聲稱支援跨 segment opponent adaptation。

### 4.2 Resume 與 Retry

Session resume 從最後一個 committed segment boundary 開始。Worker 在 segment 中間崩潰時，整個 segment 由 predecessor state 重跑；第一版不建立 hand-level remote checkpoint。segment size 應使最壞重跑成本可接受，預設 100–1000 hands，依 Agent state 大小與吞吐調整。

相同 SessionSpec、predecessor state hash、checkpoint hashes、runtime determinism profile 與 seeds 必須產生語義相同的 trajectory 與相同統計結果。GPU 浮點輸出不保證跨硬體 byte-identical；需要 bitwise determinism 的實驗必須固定 runtime、library、device、deterministic kernels 與 serialization。否則以 action trace、counter state及容差內 tensor digest作為 determinism assertion。

## 5. Opponent Profile

`OpponentProfileRevision` 是 Participant 的不可變長期視圖，由獨立 ProfileAggregation Job 產生：

```text
Profile
├── subject ParticipantId
├── profile revision
├── observed statistics
├── externally assigned tags
├── learned representation refs
├── uncertainty metadata
├── source observation frontier
└── schema/model versions
```

Simulation Job 只讀 Session 啟動時固定的 profile revision，不直接更新 global profile。Simulation 輸出 `ProfileObservationDelta`；Aggregation Job 對 committed deltas 依 deterministic order 聚合並建立新 revision。此單一寫入邊界避免多個 Session 競爭修改共享 mutable state。

Profile revision 使用 compare-and-swap promotion：若 base revision 已非 current，Aggregator 必須用新的 base 重新聚合，不能覆蓋。Profile bytes 與大型 learned representation 為 immutable artifact；current revision pointer 是 Coordinator metadata。

## 6. 三種資訊來源

### 6.1 外部 Tag

`OpponentTagAssertion` 是帶 provenance 的主觀 assertion：

```text
tag key/value
source type與source id
created_at
valid_from / optional expires_at
confidence
provenance
visibility policy
```

例如 `style=maniac` 不表示客觀真值。多個來源可同時存在且互相矛盾；Snapshot builder 按 Experiment policy 選取、聚合或隱藏，不能默默覆蓋。

Tag Only 模式只提供允許的 tag assertions，不提供統計、identity linkage 或 learned representation。外部 tag 不能使用由 privileged hole cards 推導的資料，除非該實驗明確標記為 evaluator-only oracle condition。

### 6.2 Observed Statistics

每項統計保存 numerator、denominator、sample count、missing/censored count、時間範圍、schema version 與更新 frontier。比率不單獨保存為權威值；Snapshot materialization 時才計算。

範例：

```text
VPIP = voluntary_preflop_investments / eligible_preflop_opportunities
PFR = preflop_raise_events / eligible_preflop_opportunities
3Bet = preflop_3bet_events / eligible_3bet_opportunities
FoldToCBet = folds_to_cbet / faced_cbet_opportunities
WTSD = went_to_showdown / saw_flop
W$SD = won_at_showdown / went_to_showdown
```

每個 metric 的 eligibility、事件分類、split-pot 處理與缺失規則必須在 versioned metric registry 中定義。不同 street 具有不同 denominator，缺失值不能當作 0。

只在公開 showdown 才能使用 hole cards 或標記 observed bluff。Fold 後未公開的牌為 censored，不得由完整 engine state補入 online profile。`showdown_bluff_observations` 必須保存判定規則版本，且不宣稱代表全部 bluff frequency。

統計視圖：

- lifetime：截至固定 committed frontier 的累積計數。
- session：自 Session hand 0 起累積。
- recent window：固定最近 N 個 eligible events/hands。
- decayed：按事件 hand index或timestamp套用固定 decay policy。

### 6.3 Learned Representation

`OpponentEmbeddingDescriptor` 引用 encoder checkpoint、input history schema、history window、vector shape/dtype、normalization 與 embedding artifact。Embedding 是模型輸出，不是人格或真值。

```text
observable action sequence
→ versioned encoder
→ z_opp
```

Embedding 可按 hand boundary更新；同一 snapshot 必須記錄 encoder CheckpointId 與 input frontier。模型預期 embedding v2 而 snapshot 只有 v1 時，Agent loader 必須拒絕或使用 manifest 明示的 adapter；不得靜默截斷、補零或重算。

第一階段只保留契約；GRU、Transformer、Meta-RL 的選擇屬 research decision。

## 7. Memory 層級

### Hand Memory

存在於 Environment/Agent 的當前 hand，包含公開 action sequence 與 Agent 自己可見的 cards。Hand 結束後不直接持久化為 session state；先經 information-safe event projector。

### Session Memory

由 `SessionMemoryState` 表達：

- 已完成的 segment/hand frontier
- session statistic accumulators
- recent-window event ring或其壓縮狀態
- identity handle map與seat schedule position
- shared learned opponent encoder state（若啟用）
- state/schema/model versions

它是 segment output 的 immutable artifact，也是下一 segment input。Coordinator 只提交 active attempt 產生的 state；舊 attempt output 不得成為 successor dependency。

### Long-Term Memory

由不可變 OpponentProfileRevision 與 current pointer 組成，跨 Session 保存。Simulation 不直接改寫。ProfileAggregation Job 對唯一的 committed delta identity 去重，輸出下一 revision。

### PrivatePolicyMemory

Recurrent policy hidden state屬於特定 observing Agent/SessionParticipant，而不是對手共享資料。它使用獨立 `PRIVATE_POLICY_STATE` section或artifact，受 Agent runtime 存取控制，不放入 OpponentContextSnapshot。這避免其他 Agent 或 learner誤讀私有策略狀態。

## 8. OpponentContextSnapshot

Snapshot 是某個 observing SessionParticipant 在 hand boundary 可讀的 immutable context：

```text
snapshot_id
session_id
observer_session_participant_id
as_of_hand_index
visible opponent handles
profile/session/recent statistic views
tag assertions（依visibility policy）
embedding descriptors/values（若啟用）
uncertainty與sample metadata
profile revisions
schema/model versions
content hash
```

Snapshot 不包含 private recurrent state、未公開 hole cards、future board、deck order、其他 observer 的私有觀察或 privileged HandState。

### 8.1 建立頻率

第一版在每手開始前建立邏輯 snapshot，但採 canonical serialization 與 content hash 去重。若 context 未變，多手可引用同一 SnapshotId。大型 history 不嵌入 snapshot；recent-window state保存聚合或引用 SessionMemoryState。

Learned embedding 可設定每 N hands重算，但 Snapshot 必須標示 embedding 的 `as_of_hand_index` 與 staleness。每 decision 建 snapshot 僅適用於研究模式，預設不使用。

### 8.2 儲存

小型 snapshot 可集中寫入 Parquet snapshot shard；Experience row 保存 SnapshotId，Dataset manifest引用 snapshot shard ArtifactRef。單一 snapshot不必各自成為 artifact object。大型 embedding matrix可獨立使用 content-addressed artifact並由 descriptor引用。

Retention：只要 experience dataset、evaluation result或checkpoint provenance引用 snapshot shard，就不可刪除。未被引用的中間 snapshot遵循 session artifact retention。

## 9. Agent Integration

既有 `act(observation, legal_actions, context)` 不變。`Observation` 仍只描述當前 hand seat-specific game information；opponent modeling透過 versioned `DecisionContext` extension提供：

```text
DecisionContext
├── session metadata（允許公開部分）
├── opponent_context_snapshot view
└── private_policy_memory handle（僅該 Agent runtime）
```

高頻決策不透過 ArtifactRef 即時下載。Worker在 segment開始時載入 predecessor state及snapshot shard，並將當前 context直接序列化到 in-process Agent call。ArtifactRef只存在於 Job input/output與dataset manifest。

Agent Manifest 必須聲明：

```text
supported_context_schema
required_identity_mode
required_feature_groups
embedding schema/model compatibility
supports_session_resume
private memory schema
```

不支援 opponent context 的既有 Agent取得空的 extension，行為保持相容。

## 10. Experience Schema Extension

最低限度新增：

```text
session_id
session_participant_id
context_snapshot_id
context_schema_version
```

必要性：

- `session_id`：重建跨手牌順序與切分 train/eval，避免同 Session 洩漏到兩個資料集。
- `session_participant_id`：表示 action 的邏輯 actor，不受 seat rotation影響；匿名資料匯出時可重映射。
- `context_snapshot_id`：連結決策當時 context，不重複儲存大量資料。
- `context_schema_version`：在讀取 snapshot前快速做相容性檢查。

不在每 row 加入 ParticipantId、完整 stats、tag、embedding或memory_state_ref。ParticipantId visibility由 snapshot控制；SessionMemoryState是 segment邊界輸入，不是每個 decision 的資料。

Experience排序鍵增加：

```text
session_id, hand_index, seat, step_index
```

Learner取得 DatasetSnapshot列出的 experience shards與snapshot shards，先驗 hash與schema，再進行 join。

## 11. Opponent Modeling 模式

- `EXPLICIT_TAG`：只使用允許的外部 tag；用於可控工程實驗，不視為真值。
- `STAT_CONDITIONED`：使用 versioned HUD-like統計、denominator、missing mask與uncertainty。
- `LEARNED_EMBEDDING`：使用可觀察 action history編碼的 latent representation。
- `HYBRID`：組合上述 feature groups，必須在 Experiment revision中固定。

Ablation visibility：

```text
NO_PROFILE
TAG_ONLY
STATS_ONLY
EMBEDDING_ONLY
FULL_CONTEXT
```

Identity visibility與feature visibility分開設定。例如 `ANONYMOUS + STATS_ONLY` 可使用當前 Session累積統計，但不可載入跨 Session profile。

## 12. Session Simulation Flow

```text
Coordinator建立SessionSpec與Segment 0 Job
→ Worker載入固定long-term profile revisions
→ 建立initial SessionMemoryState與Snapshot
→ 執行segment hands
→ 每手由public event projector更新session accumulators
→ 產生experience、snapshot shard、ProfileObservationDelta
→ 上傳並CompleteAttempt
→ Coordinator驗證active lease與artifact
→ commit segment frontier
→ 以前段SessionMemoryState建立segment N+1 Job
```

Profile delta的唯一鍵：

```text
session_id + hand_index + observer_session_participant_id + subject_session_participant_id + metric_schema_version
```

Aggregator以此去重。只有 committed segment outputs可進 long-term aggregation；attempt-local或 fenced outputs不得使用。

## 13. Distributed Execution Semantics

- SessionSpec、Segment payload、predecessor state、profile revision與checkpoint均為 immutable input。
- 每個 segment有單調 `segment_index` 與唯一 predecessor state hash。
- Coordinator只允許當前 frontier的下一 segment進入 QUEUED。
- Completion時驗證 output state內的 SessionId、segment index、input state hash、last hand index及schema versions。
- At-least-once execution可重做 computation，但 committed frontier只前進一次。
- Fenced attempt不能提交 state、delta或experience到 Session lineage。
- Stale profile可被允許，但必須由 SessionSpec固定並可重現；Session中途不切換 long-term profile revision。

Independent Sessions可以分散到任意 Node。同一 stateful Session預設一次只有一個可執行 segment。若未來需要 speculative execution，必須證明 predecessor state相同並以 deterministic output驗證；不屬第一階段。

## 14. Safe Exploitation

一種可選策略為：

```text
π_final = (1 - α) π_baseline + α π_exploit
```

此公式不是唯一架構。也可使用 action-level constraints、KL regularization、robust optimization或fallback gating。Experiment必須固定 safe-exploitation policy與參數。

`α` 或其他 gating訊號可依：

- metric denominator與effective sample size
- posterior/ensemble uncertainty
- recent與lifetime consistency
- embedding out-of-distribution score
- profile staleness與drift score
- wrong-profile stress test結果

樣本數增加不代表可完全 exploit。對手可能改變策略，模型也可能錯置 identity。任何 adaptive policy都必須提供 baseline fallback，並在 evaluation中報告錯誤 profile下的 downside。

## 15. Drift Handling

同時保存 lifetime、session、recent-window與可選 decayed view。Drift detector比較可觀察 action distribution或feature vector，不推論心理狀態。

可支援：

- 固定 sliding window
- exponential decay
- recent/lifetime divergence
- change-point score
- uncertainty inflation
- 暫時降低 long-term profile權重
- reset session embedding hidden state

Threshold、window與reset policy屬 Experiment revision。偵測到 drift不應改寫歷史 ProfileRevision，而是在新 Snapshot中記錄 drift evidence，並由後續 Aggregation Job建立新 revision。

## 16. Evaluation Protocol

每個 evaluation suite固定：

- Agent/checkpoint與opponent checkpoints
- SessionSpec、identity mode、context mode
- seed blocks、seat schedule、strategy switch hand
- profile revision或oracle fixture
- runtime determinism profile
- metric/evaluation schema versions

使用 paired/duplicate seeds比較：

1. non-adaptive baseline
2. adaptive agent
3. evaluator-only oracle profile condition
4. wrong/noisy/stale profile condition
5. cold-start與warm-start
6. known pseudonym與anonymous
7. stationary與預先宣告的strategy switch

Oracle profile只能作 evaluator對照，不得進正式 online policy pipeline或一般 training feature，除非 Experiment明確標記為 oracle research且輸出隔離。

報告：

- `Δ bb/100`、hands、standard error與95% CI
- adaptation curve與change point後達到指定policy-distance/EV門檻的hands
- wrong-profile downside與worst tested condition
- action prediction log loss/Brier score
- opponent type classification（僅有合成標籤真值時）
- strategy cluster stability，不要求latent dimension可解釋
- context coverage、missing rate、staleness與drift alarms

Profile accuracy不能只用人工 style tag作 ground truth。Future-action prediction與held-out session表現優先。

## 17. Information-Flow Security

所有 profile資料由 seat-specific public event projector建立。Projector輸入是 Agent當時可觀察的 event stream，不是完整 HandState。Showdown公開牌只有在規則確實公開後才能進 stream。

資料分層：

```text
ONLINE_OBSERVABLE
    可進context、experience與online learner

PRIVILEGED_AUDIT
    完整hand history；只供audit/evaluator

ORACLE_RESEARCH
    明確隔離的supervised target或oracle evaluation
```

每個 artifact與欄位帶 information class。Dataset builder禁止將 `PRIVILEGED_AUDIT` 或 `ORACLE_RESEARCH` join到一般 online feature。權限檢查之外，需做 schema allowlist與information-flow tests。

## 18. Failure Semantics

- Worker crash：由最後committed segment state重跑整個segment。
- Duplicate hand processing：未committed attempt不計入；profile delta以唯一鍵去重。
- Snapshot上傳成功但completion失敗：artifact成為orphan或重試時hash去重，不前進Session frontier。
- Stale context：若與SessionSpec固定revision一致則合法；若input revision/hash不符則Job不可重試並標記invalid input。
- Schema mismatch：Agent manifest不支援context schema時在執行前失敗，不開始hands。
- Embedding v2/v1 mismatch：必須有顯式adapter，否則拒絕。
- Partial Session retry：只重跑未commit segment，不拼接attempt中間state。
- Artifact corruption：hash驗證失敗，隔離cache並重新下載；來源也錯則Job retryable failure並告警。
- Identity collision：所有ID使用UUIDv7且受Experiment foreign-key/unique constraint；payload identity與SessionSpec不符立即拒絕。
- Resume on another Node：下載相同predecessor state與inputs；Node-local cache不構成權威state。
- Determinism violation：相同determinism profile下action trace/state digest不同則隔離兩份output並標記Session failed，不任意選擇。

## 19. Protobuf 草案

```proto
syntax = "proto3";

package tableforge.v1;

import "google/protobuf/timestamp.proto";

message SessionSpec {
  string schema_version = 1;
  string session_id = 2;
  string experiment_id = 3;
  uint64 experiment_revision = 4;
  repeated SessionParticipant participants = 5;
  IdentityMode identity_mode = 6;
  ContextMode context_mode = 7;
  uint64 hand_count = 8;
  uint32 segment_hand_count = 9;
  string game_config_sha256 = 10;
  string schedule_schema_version = 11;
  repeated ProfileRevisionRef initial_profiles = 12;
  google.protobuf.Timestamp created_at = 13;
}

message SessionParticipant {
  string schema_version = 1;
  string session_participant_id = 2;
  string participant_id = 3;
  string agent_id = 4;
  string checkpoint_id = 5;
  ArtifactRef inference_bundle = 6;
  string public_handle = 7;
}

message SessionSegmentInput {
  string schema_version = 1;
  string session_id = 2;
  uint64 segment_index = 3;
  uint64 hand_start_inclusive = 4;
  uint64 hand_end_exclusive = 5;
  ArtifactRef previous_session_state = 6;
  repeated ArtifactRef context_snapshot_shards = 7;
}

message SessionMemoryStateDescriptor {
  string schema_version = 1;
  string session_id = 2;
  uint64 completed_segment_index = 3;
  uint64 next_hand_index = 4;
  string input_state_sha256 = 5;
  ArtifactRef state_artifact = 6;
  string context_schema_version = 7;
  string private_memory_schema_version = 8;
}

message OpponentContextSnapshot {
  string schema_version = 1;
  string snapshot_id = 2;
  string session_id = 3;
  string observer_session_participant_id = 4;
  uint64 as_of_hand_index = 5;
  repeated OpponentContext opponents = 6;
  string content_sha256 = 7;
}

message OpponentContext {
  string schema_version = 1;
  string opponent_handle = 2;
  string visible_participant_id = 3;
  repeated StatisticValue statistics = 4;
  repeated TagAssertion tags = 5;
  OpponentEmbeddingDescriptor embedding = 6;
  ConfidenceSummary confidence = 7;
  uint64 profile_revision = 8;
  uint64 as_of_hand_index = 9;
}

message StatisticValue {
  string metric_key = 1;
  int64 numerator = 2;
  int64 denominator = 3;
  int64 missing_count = 4;
  int64 censored_count = 5;
  string metric_schema_version = 6;
  WindowDescriptor window = 7;
}

message TagAssertion {
  string tag_key = 1;
  string tag_value = 2;
  string source_type = 3;
  string source_id = 4;
  double confidence = 5;
  google.protobuf.Timestamp created_at = 6;
  google.protobuf.Timestamp expires_at = 7;
  string provenance = 8;
}

message OpponentEmbeddingDescriptor {
  string embedding_schema_version = 1;
  string encoder_checkpoint_id = 2;
  string input_history_schema = 3;
  uint64 as_of_hand_index = 4;
  repeated uint32 shape = 5;
  string dtype = 6;
  ArtifactRef embedding_artifact = 7;
}

enum IdentityMode {
  IDENTITY_MODE_UNSPECIFIED = 0;
  IDENTITY_MODE_KNOWN_PSEUDONYM = 1;
  IDENTITY_MODE_SESSION_ONLY = 2;
  IDENTITY_MODE_ANONYMOUS = 3;
  IDENTITY_MODE_NO_PROFILE = 4;
}
```

草案新增欄位只能append，不得重用field number。正式實作前需拆入 `sessions.proto` 與 `opponent_context.proto`，並以contract tests驗證Python generated types。

## 20. Storage 與 Artifact Policy

新增 artifact kind控制為三種：

- `SESSION_STATE`：包含shared session memory與分區後的private policy state；每segment一份，作successor input。
- `OPPONENT_CONTEXT_SHARD`：包含多個immutable snapshots及其index。
- `PROFILE_OBSERVATION_SHARD`：information-safe observation deltas，供Aggregation Job使用。

Learned embedding預設放入context shard；只有大型或跨多Snapshot重用時才使用既有generic model/data artifact並由ArtifactRef引用，不新增每種模型專用kind。OpponentProfile metadata與current revision pointer存資料庫；大型revision payload可使用既有artifact系統。

所有artifact content-addressed。Retention遵循reference graph：Session successor、DatasetSnapshot、ProfileRevision、checkpoint provenance或evaluation引用期間不可刪。Private policy state不得被其他participant或一般profile learner讀取。

## 21. Testing

### Identity

- seat rotation不改SessionParticipantId。
- AgentId、ParticipantId、SessionParticipantId不相等且不可互換。
- 同checkpoint的兩個participant不共享profile，除非SessionSpec明確引用同一ParticipantId。
- anonymous snapshot不包含跨Session linkage。

### Statistics

以deterministic action traces驗證VPIP、PFR、3Bet、FoldToCBet、WTSD、W$SD、street denominator、missing與censoring。每個metric registry版本使用golden vectors與property tests。

### Information Flow

- folded/unshown hole cards不進projector。
- future board與deck order不進context。
- privileged history不能被online Dataset builder join。
- observer A的private card不能影響A對B的公開profile統計，除非metric明確只使用A合法可見資訊且輸出仍不揭露card。
- oracle fixture只在oracle evaluation namespace可讀。

### Retry 與 Idempotency

- 同segment重試不重複commit frontier。
- 同hand delta重送不重複更新profile。
- fenced attempt不能成為successor input。
- snapshot上傳成功但completion失敗不建立可見Session lineage。

### Determinism

固定Session input、seed、checkpoints、predecessor state、runtime determinism profile時，action trace、statistics與state frontier相同。跨GPU浮點測試依manifest指定bitwise或tolerance policy。

### Compatibility

- Agent不支援context extension時收到空context並維持既有行為。
- schema/model mismatch在hand開始前失敗。
- 顯式adapter輸出記錄來源與目標version。

### Failure/Scale

- hand 2370所在segment崩潰並在另一Node重跑後Session完成。
- 多個Session平行執行時profile aggregation結果不受Job完成順序影響。
- 10k snapshots去重、join、retention與GC正確。
- drift fixture在宣告change point後產生可重現score。

## 22. Development Milestones

Opponent modeling不得阻塞既有M0–M5。建議在M5後建立獨立O線：

- `O0`：Session、Participant identity、seat schedule與ordered segment dependency。
- `O1`：public event projector、HUD metric registry與information-flow tests。
- `O2`：SessionMemoryState、OpponentContextSnapshot shard、experience references與retry tests。
- `O3`：Known/Anonymous/No Profile及Tag/Stats conditioned NFSP ablation。
- `O4`：ProfileAggregation Job、long-term revision、recent windows與drift evidence。
- `O5`：learned embedding encoder、version compatibility與offline prediction evaluation。
- `O6`：confidence-aware safe exploitation與wrong-profile stress suite。
- `O7`：recurrent/meta-RL policy；僅在前述資料與評估證明可靠後研究。

O0–O2為跨手牌context的工程基礎；O3以上為研究能力。任何階段都不能降低Poker Engine、distributed retry或information isolation門檻。

## 23. 未決研究問題

以下是research decisions，不是既定事實：

- 哪種recent/lifetime組合最能處理策略漂移。
- embedding encoder應joint training或獨立預訓練。
- safe exploitation應使用mixture、KL constraint、robust objective或其他方法。
- 多人6-Max中每個opponent embedding如何組合。
- checkpoint改變時是否延續Participant long-term profile，以及應如何衰減。
- 跨硬體可接受的determinism層級與tensor tolerance。
- profile uncertainty使用Bayesian posterior、bootstrap ensemble或校準predictor。
- learned context帶來的收益是否足以抵銷stateful Session降低的intra-session parallelism。

## 24. Existing Specs Impact

```text
README.md
需要新增 05-opponent-modeling.md 的索引與「Opponent modeling 為 M5 後擴充研究線」。

01-system.md
需要新增 Session/Participant/OpponentContext 為 Agent/Job 邊界上的擴展點；不修改核心元件責任。

02-contracts-and-data.md
需要新增 SessionId、ParticipantId、SessionParticipantId、Session segment payload與experience最小欄位；不修改既有Job/Artifact lifecycle。

03-runtime-and-correctness.md
需要新增stateful Session的ordered segment dependency、predecessor state驗證與information-flow tests。

04-deployment-and-evolution.md
需要新增M5後O0–O7研究線；不修改M0–M5發布門檻。
```
