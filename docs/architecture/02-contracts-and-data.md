# 契約與資料模型

## 1. 契約原則

所有跨程序資料均使用 Protobuf；資料庫 row 與 Python class 不作為網路契約。每個 message 必須遵守：

- package 使用 `tableforge.v1`
- enum 的 `0` 為 `UNSPECIFIED`
- 時間使用 UTC `google.protobuf.Timestamp`
- 大小使用 bytes，籌碼使用整數最小單位
- ID 使用不可重用的 UUIDv7 字串
- 新欄位只能新增，不重用已刪除 field number
- payload 帶 `schema_version`
- artifact reference 必須包含 SHA-256

## 2. 核心識別

```text
NodeId       UUIDv7
ExperimentId UUIDv7
JobId        UUIDv7
AttemptId    UUIDv7
AgentId      UUIDv7
CheckpointId UUIDv7
ArtifactId   UUIDv7
MatchId      UUIDv7
EventId      UUIDv7
```

顯示名稱不是 ID。Agent 的 `nfsp-v21` 可重複，但 `AgentId + CheckpointId + sha256` 必須全域唯一。

## 3. JobSpec

```proto
message JobSpec {
  string schema_version = 1;
  string job_id = 2;
  string experiment_id = 3;
  JobType type = 4;
  int32 priority = 5;
  ResourceRequest resources = 6;
  repeated ArtifactRef inputs = 7;
  bytes payload = 8;
  string payload_type = 9;
  string idempotency_key = 10;
  int32 max_attempts = 11;
  google.protobuf.Timestamp not_before = 12;
  google.protobuf.Timestamp created_at = 13;
}
```

`payload_type` 使用完整名稱，例如 `tableforge.v1.SimulationJobPayload`。Coordinator 只接受已註冊的 payload type。`idempotency_key` 在 experiment 範圍唯一，相同 key 與相同 canonical request 回傳既有 Job；相同 key 但內容不同回傳 conflict。

### Job 狀態

```text
QUEUED → LEASED → RUNNING → SUCCEEDED
                   ├──────→ RETRY_WAIT → QUEUED
                   ├──────→ FAILED
                   └──────→ CANCELLED
```

狀態更新使用 compare-and-swap：請求帶 `expected_version`，成功後 version 加一。過期 attempt 不得完成 Job。

### Lease

Lease 包含：

```text
attempt_id
job_id
node_id
lease_token_hash
leased_at
expires_at
heartbeat_at
attempt_number
```

原始 lease token 只回傳給 Worker，資料庫只存 hash。`renew` 與 `complete` 都必須附 token。Coordinator 以自身時間判定 expiration，不能信任 Worker clock。

## 4. SimulationJobPayload

```proto
message SimulationJobPayload {
  string match_id = 1;
  GameConfig game = 2;
  repeated SeatAssignment seats = 3;
  uint64 seed_start = 4;
  uint64 hand_count = 5;
  uint32 shard_hand_count = 6;
  ExperiencePolicy experience_policy = 7;
}

message SeatAssignment {
  uint32 seat = 1;
  string agent_id = 2;
  string checkpoint_id = 3;
  ArtifactRef inference_bundle = 4;
}
```

每個 hand seed 由 `H(experiment_id, match_id, seed_start + index)` 決定。重試相同 Job 必須得到相同發牌與 seat rotation。隨機 Agent 的 RNG 另由 hand seed、seat、decision index 派生。

## 5. Artifact

```proto
message ArtifactRef {
  string artifact_id = 1;
  string sha256 = 2;
  uint64 size_bytes = 3;
  string media_type = 4;
  ArtifactKind kind = 5;
  string schema_version = 6;
}
```

Artifact lifecycle：

```text
PENDING_UPLOAD → AVAILABLE → RETIRED
                └─────────→ CORRUPT
```

上傳流程：

1. Worker 取得 temporary upload grant。
2. Worker 串流計算 SHA-256 並上傳暫存 key。
3. Worker 呼叫 commit，提交 hash 與 size。
4. Store 驗證內容後移至 content-addressed key：`sha256/ab/cd/<full-hash>`。
5. Coordinator 將狀態切為 AVAILABLE。

Job output 僅能引用 AVAILABLE artifact。相同 hash 可共用 bytes，但各 metadata record 可保有不同 provenance。

## 6. Checkpoint 與 Agent Manifest

```json
{
  "schema_version": "1.0",
  "agent_id": "...",
  "checkpoint_id": "...",
  "algorithm": "nfsp",
  "runtime": "python-pytorch",
  "entrypoint": "tableforge.agents.nfsp.runtime:load_agent",
  "observation_schema": "poker-hunlhe-vector-v1",
  "action_schema": "nlhe-discrete-v1",
  "parent_checkpoint_id": "...",
  "artifacts": {
    "inference": {"artifact_id": "...", "sha256": "..."},
    "training": {"artifact_id": "...", "sha256": "..."}
  },
  "requirements": {
    "cpu_cores": 1,
    "memory_bytes": 2147483648,
    "gpu_memory_bytes": 1073741824
  },
  "code_version": "git:a07a8cc"
}
```

Manifest 必須先通過 schema validation，再註冊 checkpoint。Checkpoint immutable；繼續訓練會建立新 CheckpointId 並指向 parent。

## 7. Experience Shard

Experience 不透過 gRPC message 逐筆傳輸，使用 Parquet shard。必要欄位：

```text
experiment_id
match_id
hand_id
seat
agent_id
checkpoint_id
step_index
observation_schema
observation_bytes
legal_action_mask
action
action_amount
reward_bb
terminal
policy_metadata
seed
```

`policy_metadata` 依演算法保存，例如 NFSP 的 anticipatory mode、behavior probability。完整 privileged hand history 使用不同 artifact kind 與 ACL，不可混入 Agent experience。

Shard manifest 記錄 row count、min/max hand id、schema version、producer node、producer code version、hash。Learner 先驗證 manifest 與 hash，再接受 shard。

## 8. Metadata 資料表

第一版 SQLite 與 PostgreSQL 使用相同邏輯 schema：

```text
nodes
experiments
experiment_revisions
agents
checkpoints
artifacts
artifact_locations
jobs
job_attempts
job_outputs
matches
events
outbox
idempotency_records
```

關鍵約束：

- `jobs(experiment_id, idempotency_key)` unique
- `checkpoints(agent_id, checkpoint_id)` unique
- `artifacts(sha256, size_bytes)` index
- 同一 job 最多一個未過期 active lease
- checkpoint artifact 必須 AVAILABLE 才能 promotion
- experiment revision 單調遞增

SQLite claim 使用短 transaction 與 `BEGIN IMMEDIATE`。PostgreSQL adapter 使用 `SELECT ... FOR UPDATE SKIP LOCKED`。兩者必須通過相同 repository contract tests。

## 9. Event 與 Outbox

每次 aggregate 更新與 outbox insert 在同一 DB transaction 完成。Publisher 將 outbox 轉為 domain event：

```json
{
  "event_id": "...",
  "event_type": "JOB_SUCCEEDED",
  "aggregate_type": "job",
  "aggregate_id": "...",
  "aggregate_version": 7,
  "occurred_at": "...Z",
  "producer_node": "...",
  "payload_schema": "tableforge.v1.JobSucceeded",
  "payload": {}
}
```

Consumer 以 EventId 去重，並以 aggregate version 拒絕倒退更新。第一版 event 用於 audit 與內部通知；未來 federation 可複用，但不宣稱目前 event log 已具備共識能力。

## 10. Node Capability 與 Workload Compatibility

Worker 註冊時提交 versioned `NodeCapability` snapshot，包含 CPU、RAM、GPU/VRAM、storage、runtime 與 enabled roles。動態 availability 由 heartbeat 更新，不覆寫 static snapshot。Workload 以 immutable `WorkloadProfile` 宣告 minimum/recommended resources、supported runtimes 與允許的 adjustments。

Scheduler 產生 `CompatibilityAssessment` 與 `ResolvedExecutionPlan`。Job attempt 必須記錄 capability revision、workload profile version、選定 device、batch/precision adjustment 與 peak resource metrics。這些是既有 Job/Attempt metadata 的擴展，不建立另一套 lifecycle。詳細契約見 `06-capability-and-user-operations.md`。

## 11. 版本相容策略

- Coordinator 支援當前 major 與前一個 minor worker protocol。
- Worker 註冊時交換 protocol range、runtime version、supported payload types。
- 不相容 Worker 標記 `INCOMPATIBLE`，不得 claim job。
- Observation/action schema 不做隱式轉換；需要顯式 adapter 並記入 MatchSpec。
- Artifact 永不就地 migration；產生新 artifact 並保留 provenance。
