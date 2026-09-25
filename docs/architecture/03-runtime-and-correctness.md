# 執行流程與正確性語義

## 1. Node 啟動

1. 載入 node config 與長期 NodeId。
2. 建立到 Coordinator 的 TLS channel。
3. 呼叫 `RegisterNode`，提交 capability、版本與可支援的 Job type。
4. Coordinator 回傳 session token、heartbeat interval、最大 lease duration。
5. Runtime 啟動 heartbeat 與各 executor poll loop。
6. 本地 cache 掃描既有 artifact，hash 不符者隔離。

NodeId 儲存在本機 state volume；容器重建但 volume 保留時仍視為同一 Node。複製 volume 到另一機器是不允許的，Coordinator 同時看到相同 NodeId 的兩個 session 時撤銷舊 session。

## 2. Job 建立與執行

```text
Client → CreateJob(idempotency_key)
Coordinator → validate experiment revision and inputs
Coordinator → persist QUEUED + outbox
Worker → ClaimJobs(capability)
Coordinator → atomically create Attempt + Lease
Worker → download and verify inputs
Worker → StartAttempt
Worker → execute and upload outputs
Worker → CompleteAttempt(outputs, metrics)
Coordinator → verify artifacts and active lease
Coordinator → mark SUCCEEDED + publish event
```

Job 提供 at-least-once execution，不承諾 exactly-once。Exactly-once effect 由 idempotent completion、immutable outputs、attempt fencing 達成。

如果 Worker 完成後回應遺失，它可重送 `CompleteAttempt`；相同 attempt 與相同 outputs 回傳成功，內容不同回傳 conflict。Lease 到期後才抵達的 completion 回傳 fenced，不得覆蓋新 attempt。

## 3. Simulation

Simulation executor：

1. 驗證 MatchSpec 的所有 checkpoint hash。
2. 從 cache 載入 inference bundle；缺少才下載。
3. 建立 PokerEnvironment 與每座位 Agent instance。
4. 依 seed range 執行固定數量 hands。
5. 每 `shard_hand_count` 產生 experience shard 與 hand-summary shard。
6. 檢查每手 chip conservation、合法 action、terminal state。
7. 上傳 shards，最後提交 output manifest。

單一 Job 不在執行中切換 checkpoint。為限制重試成本，建議每 Job 10k–100k hands，而不是 1M hands；1M-hand request 由 Coordinator 展開為多個 child jobs，seed range 不重疊。

### 資金守恆 invariant

每手必須滿足：

```text
sum(initial_stack) = sum(final_stack)
sum(contributions) = sum(awarded_pots)
所有 stack、pot、contribution 均為非負整數
```

違反 invariant 時立即終止 shard，不得將資料標記為可訓練；上傳 diagnostic artifact 並使 Job FAILED。

## 4. NFSP 訓練

第一版 NFSP learner 使用兩類輸入：RL transition shard 與 supervised behavior shard。TrainingJobPayload 固定：

- base checkpoint
- input shard 清單或 dataset snapshot
- hyperparameter revision
- train step count
- random seed
- output artifact requirements

Learner 不查詢「最新資料」，避免重試時輸入改變。Dataset snapshot 是 immutable artifact，列出所有 shard refs 與 hash。

完成時輸出：

- inference bundle
- training-state bundle
- metrics artifact
- checkpoint manifest

Coordinator 驗證後建立 candidate checkpoint。Promotion 是獨立 command，不因 Training Job 成功自動覆蓋 active checkpoint。

## 5. Promotion 與 League

Promotion policy 第一版採明確門檻：

1. Candidate 完成固定 baseline evaluation。
2. Evaluation 使用 paired/duplicate seeds。
3. 結果包含 bb/100、standard error、95% CI、invalid hand count。
4. 無 engine error，且 policy 指定指標通過，才可設為 active。

Promotion 只更新 `Agent.active_checkpoint_id` 指標；舊 checkpoint 仍可被 historical pool 引用。League 建立 MatchSpec 時 snapshot population revision，確保後續可重現。

## 6. Evaluation

Evaluation Job 與 Training data generation 分離。每個 matchup 拆成多個 seed block，交換座位以降低牌運偏差。聚合器只接受：

- 相同 game config hash
- 相同 Agent checkpoint hash
- 不重疊 seed block
- 相同 evaluation protocol version

不以固定手數宣稱可靠；停止條件可設定 minimum hands、maximum hands、目標 CI width。報告必須同時顯示手數與信賴區間。

## 7. 取消、重試與維護

- QUEUED Job 可直接取消。
- RUNNING Job 標記 cancel requested；Worker heartbeat 收到後合作式停止。
- 超過 grace period 可讓 lease 到期，但不假設遠端程序已停止。
- 非 retryable：schema invalid、artifact hash mismatch、unsupported checkpoint。
- Retryable：network timeout、process crash、temporary object-store failure、GPU OOM（若存在較小資源設定策略）。
- 每次 attempt 保存 error code、摘要與 diagnostic artifact；不得只保存自由文字 stack trace。

## 8. 故障情境

### Worker 斷線

Lease 到期後 Job 回到 QUEUED。舊 Worker 恢復時會被 fencing token 阻止完成舊 attempt。已上傳但未引用的 artifact 由垃圾回收器延遲清除。

### Coordinator 重啟

狀態來自 metadata DB。啟動時掃描 expired leases、未發布 outbox 與 PENDING_UPLOAD。SQLite 模式只能單 Coordinator；使用檔案鎖與 deployment 設定阻止第二實例。

### Artifact Store 暫時不可用

Job 保持 RUNNING，Worker按退避策略重試上傳並續租。若超過最大執行時間則 attempt 失敗。Coordinator 不接受只有 URI、未驗 hash 的 output。

### Learner 在 checkpoint 上傳後崩潰

若未 CompleteAttempt，checkpoint metadata 不建立。bytes 可由 GC 清除或在重試時依 hash 去重。

### 重複 Simulation

相同 seed 與 checkpoint 應得到相同遊戲軌跡。兩個 attempt 的 output hash 若不同，標記 determinism violation 並隔離，不任意選一份。

## 9. 安全與信任邊界

第一版節點由同一管理者控制，但仍採：

- Coordinator 與 Worker 間 TLS；跨網路用 Tailscale/WireGuard private network
- session token 有效期與撤銷
- artifact presigned URL 短效
- Worker 無 DB credentials
- Learner 只能讀被 JobSpec 授權的 artifacts
- secrets 由環境或 secret mount 提供，不寫入 config/artifact/log
- privileged hand history 與 experience 使用不同權限

此前提只防外人闖入與資料損壞，不防已加入的節點作惡。多人共用時的身分、權限與信任擴充見 `07-multi-party-and-trust.md`。

第三方 Agent 尚未支援。加入時必須新增 container sandbox、唯讀 root filesystem、資源限制、無 outbound network、action timeout；不能直接沿用可信 Agent runtime。

## 10. Capability Preflight 與資源錯誤

Scheduler 在 claim 前根據 NodeCapability、WorkloadProfile 與 reserved resources判定相容性；Worker 在執行前再次做本地preflight。GPU缺失、VRAM不足、runtime mismatch或capability未知時不得啟動高風險Job。OOM需分類、保存結構化diagnostic，且只使用Experiment已允許的有限fallback plan重試。

測試分成CPU必跑、GPU可選、VRAM tier與external-node。缺少GPU時，對應測試以明確reason skip，不得使CPU correctness suite失敗，也不得將CPU smoke test宣稱為GPU驗證。詳細規格見 `06-capability-and-user-operations.md`。

## 11. 測試門檻

### Poker Engine

- exhaustive/known-vector hand ranking
- blind 與 heads-up action order
- min-raise、短碼 all-in、reopen action
- side pot、split pot、odd chips
- fold/check/call legality
- property-based chip conservation
- observation information-flow tests

### 分散式核心

- repository contract tests：SQLite/PostgreSQL
- artifact contract tests：local/S3
- lease race、expiration、renew、fencing
- idempotent create/complete
- Worker kill/restart
- Coordinator restart
- network interruption與重複 message
- hash corruption與 partial upload
- deterministic replay across processes

第一版發布門檻：Rule Bots 在固定 seed 下連續 1M hands 無 invariant violation；兩節點測試可在任一 Worker 被終止後完成全部 jobs，且沒有重複計入結果。
