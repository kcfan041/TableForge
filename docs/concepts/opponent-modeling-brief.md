你正在維護一個名為 **TableForge** 的專案。

TableForge 是一個分散式 Poker AI 訓練與評估框架，目前已有以下第一版實作規格文件：

* `README.md`
* `01-system.md`
* `02-contracts-and-data.md`
* `03-runtime-and-correctness.md`
* `04-deployment-and-evolution.md`

請先完整閱讀這些文件，**以現有文件作為第一版工程決策的唯一基準**。不要自行推翻或重設已有架構。

現有第一版核心原則包括但不限於：

* 單一 Coordinator writer
* Worker pull model
* lease / heartbeat / retry / fencing
* immutable Job input
* immutable checkpoint
* content-addressed artifact
* Protobuf + gRPC 跨程序契約
* Simulation / Evaluation / Training Job 分離
* Poker Environment 與 Agent 分離
* Agent 只能取得 seat-specific Observation，不可接觸 privileged HandState
* 單機與兩節點使用相同 service interface
* 第一版以 Heads-Up NLHE、Rule Bots、NFSP 為核心
* Federation、PPO、Deep CFR、6-Max 等屬後續演進
* 新功能應透過既有 Job / Artifact / Agent / Checkpoint 邊界擴充，避免修改核心 lifecycle

你的任務是新增：

`05-opponent-modeling.md`

主題為：

# 長期對手建模、跨手牌記憶與自適應剝削

此文件要解決目前 TableForge 尚未正式定義的一個能力：

> Poker Agent 不只根據「當前這一手牌」做決策，而可以根據「同一個對手在過去多手牌中的行為」建立 opponent model，並根據對手長期或近期策略特徵改變決策。

---

## 一、核心設計問題

請先明確處理以下問題：

### 1. Player Identity

目前系統有：

* `AgentId`
* `CheckpointId`
* `Seat`

但這不足以表達：

> 某一個 Agent 在跨多手牌、換座位後仍然是「同一個對手」。

需要定義適合的 identity model。

請討論並設計：

* `PlayerInstanceId`、`ParticipantId`、`SessionPlayerId` 或其他合理名稱
* Agent identity 與 session identity 的差異
* 同一 Agent checkpoint 是否可以同時存在於不同玩家 instance
* seat rotation 後如何維持身份
* reconnect / retry / job split 後如何維持身份
* identity 是否應跨 Session 保留
* 匿名模式下是否隱藏 identity

不要直接把 `AgentId` 當成真人身份。

---

### 2. Session

目前主要 hierarchy 接近：

```text
Experiment
→ Match
→ Hand
```

請評估並正式加入：

```text
Experiment
→ Session
→ Match/Table
→ Hand
```

或其他更合理模型。

Session 需要代表：

> 一群固定 participant 在一段連續對局中，可以持續累積彼此的觀察與記憶。

請定義：

* `SessionId`
* session lifecycle
* participant list
* seat rotation
* hand range
* session reset
* session resume
* session split into multiple Simulation Jobs
* distributed retry 時 session memory 如何重建
* deterministic replay 如何保持一致

尤其要解決：

> 一個 5000-hand Session 被切成 50 個 100-hand Job 時，Opponent Context 仍需具有正確時間順序。

---

## 二、Opponent Context 資料模型

請正式定義一個：

`OpponentContext`

至少包含下列概念：

```text
OpponentContext
├── identity
├── lifetime statistics
├── session statistics
├── recent-window statistics
├── manual / semantic tags
├── learned embedding
├── uncertainty / confidence
├── sample count
├── recency
└── profile version
```

但不要照抄上述名稱，如果有更好的 domain model 可以重構。

---

## 三、三種資訊來源必須分離

系統必須明確區分：

### 1. Human / externally assigned tags

例如：

* Fish
* Maniac
* Nit
* Calling Station
* Bluff-heavy

這些是：

> 外部輸入的主觀或人工標記。

不能被當成客觀真值。

需要保存：

* tag source
* creation time
* confidence
* optional expiration
* provenance

---

### 2. Observed statistics

例如：

* VPIP
* PFR
* 3-bet %
* Fold to C-Bet
* WTSD
* W$SD
* aggression frequency / factor
* river bet rate
* river overbet rate
* showdown bluff observations

這些是：

> 從實際可見 action history 統計得到的客觀 observation。

請特別處理：

* denominator
* sample count
* missing data
* censoring
* showdown 才能知道的資訊
* 不可以利用沒公開的 hole cards
* 不同 street 的樣本數不同
* 觀察統計的 schema version

---

### 3. Learned opponent representation

例如：

```text
z_opp
```

由：

* Transformer
* GRU
* MLP
* sequence encoder
* Meta-RL model

從歷史行為學出的 latent embedding。

此 embedding：

* 不必有人類可解釋名稱
* 必須有模型版本
* 必須標明輸入 history window
* 不得直接視為 ground truth
* 需要可重建或 artifact 化

---

## 四、Observation / DecisionContext 擴充

目前 Agent 呼叫形式類似：

```python
act(
    observation,
    legal_actions,
    context
)
```

請設計如何擴充，而不要破壞既有 Agent interface。

例如：

```text
DecisionContext
├── session metadata
├── opponent contexts
└── memory state
```

但需決定：

* OpponentContext 是否進 Observation
* 是否進 DecisionContext
* 是否透過 ArtifactRef
* 是否直接序列化
* 哪些資料屬於 public information
* 哪些是 agent-private memory
* 哪些可以被 training pipeline 看見

必須維持現有核心安全原則：

> Agent 不可因 opponent modeling 而取得原本不可見的 private cards、future board 或 privileged HandState。

---

## 五、跨手牌記憶

請定義三種層級：

### Hand Memory

只存在於當前 hand。

### Session Memory

例如：

* 最近 20 手
* 最近 100 手
* 當前 session 統計
* opponent embedding hidden state

Session 結束後可重設。

### Long-Term Memory

跨 Session 保留，例如：

* lifetime HUD stats
* historical opponent profile
* human tags

請明確說明：

* 哪些 memory 是 immutable artifact
* 哪些是 mutable state
* mutable state 如何 version
* retry 如何避免重複更新
* distributed simulation 如何避免同一 hand 被計算兩次
* memory update 如何達到 idempotent effect

---

## 六、Context Snapshot

不要在每一筆 Experience row 中重複保存大量 opponent history。

設計：

`OpponentContextSnapshot`

Experience row 應只保存：

```text
context_snapshot_id
```

或等價 reference。

Snapshot 可包含：

* current statistics
* recent windows
* tag state
* opponent embedding
* confidence
* profile revision

請定義：

* snapshot immutable
* content hash
* snapshot 建立頻率
* snapshot 是否每 decision / hand / N hands 一次
* deduplication
* artifact storage format
* retention
* learner 如何取得 snapshot

---

## 七、Experience Schema Extension

目前 experience shard 包含：

* experiment_id
* match_id
* hand_id
* seat
* agent_id
* checkpoint_id
* observation
* legal action
* action
* reward
* policy metadata
* seed

請提出最低限度 extension，例如：

```text
session_id
participant_id
context_snapshot_id
opponent_context_schema
memory_state_ref
```

但不要盲目增加欄位。

請說明每一欄的必要性。

---

## 八、Opponent Modeling 訓練模式

請定義至少三種可支援模式。

### Mode A：Explicit Tag Conditioning

模型直接接收人工 / 外部標記。

例如：

```text
Fish
Maniac
Nit
```

目的主要是工程驗證與可控實驗。

---

### Mode B：Statistic-Conditioned Policy

模型接收 HUD-like statistics。

例如：

```text
VPIP
PFR
3Bet
FoldToCBet
...
```

---

### Mode C：Learned Opponent Embedding

模型從歷史 action sequence 自動學習：

```text
history → encoder → z_opp
```

可用：

* GRU
* Transformer
* Meta-RL

第一版不要求實作全部，但資料契約應允許後續加入。

---

## 九、Safe Exploitation

不要假設 opponent model 永遠正確。

需要設計：

```text
baseline policy
+
exploit policy
+
confidence-aware mixing
```

概念可表示為：

```text
π_final =
(1 - α) π_baseline
+
α π_exploit
```

但文件必須說明這只是其中一種實作方法，不要把它寫死成唯一架構。

`α` 可以依：

* sample count
* posterior uncertainty
* recent consistency
* profile confidence
* opponent drift

調整。

請避免使用：

> 樣本多就一定可以完全 exploit

這種過度簡化說法。

---

## 十、Opponent Drift

對手可能改變打法。

需要同時保留：

```text
long-term profile
recent/session profile
```

並處理：

* exponential decay
* sliding window
* recent vs lifetime divergence
* profile drift detection
* reset / down-weight
* session tilt-like behavior

文件不要宣稱模型能直接辨識「情緒」或「Tilt」。

應描述為：

> observable strategy distribution shift

除非有額外資訊支持心理狀態推論。

---

## 十一、Hidden Identity / Ablation Modes

TableForge 應支援實驗模式：

### Known Identity

Agent 知道 opponent identity，並能取得歷史。

### Anonymous

Agent 無法取得跨 session identity，只能根據當前 session 行為推測。

### No Profile

完全不提供 opponent context。

### Tag Only

只提供人工 tag。

### Stats Only

只提供統計。

### Learned Embedding Only

只提供 embedding。

目的為進行 ablation study。

---

## 十二、Evaluation Protocol

不要只測：

```text
bb/100
```

還需要比較：

* baseline agent
* adaptive agent
* oracle profile condition
* wrong/noisy profile condition
* opponent strategy shift
* cold-start
* warm-start
* known identity
* anonymous identity

請設計可重現的 evaluation protocol。

特別需要：

### Profile Accuracy

不能直接要求 latent embedding 可解釋。

但可測：

* opponent type classification
* future action prediction
* strategy cluster consistency

### Adaptation Speed

例如：

> opponent 在 hand 1000 改變策略後，agent 需要多少 hands 才產生明顯 policy adjustment。

### Exploitation Gain

和 baseline policy 比較：

```text
Δ bb/100
```

並顯示 confidence interval。

### Exploitability Risk

如果 opponent profile 錯誤，adaptive policy 會損失多少。

---

## 十三、資料洩漏與公平性

這部分非常重要。

Opponent model 只能使用：

> 該 Agent 在當時真實可觀察的資訊。

不能因為 offline training 有完整 hand history，就把：

* folded opponent hole cards
* future cards
* deck order
* hidden information

放入 profile。

完整 privileged hand history 只能：

* offline audit
* evaluator
* supervised target（如果實驗明確允許）

而且必須與 online Agent input 分離。

請增加 information-flow tests。

---

## 十四、Distributed Correctness

此功能必須符合現有 TableForge：

* immutable JobSpec
* deterministic seed
* at-least-once Job execution
* fencing
* immutable artifact
* retry safety

請處理：

### Session state persistence

如果 Worker 在 Session hand 2370 崩潰：

* 如何重建 state
* 是否從 snapshot 恢復
* 是否 replay 先前 hands
* 如何確認 opponent context 完全一致

### Job slicing

若 Session 被拆成：

```text
Job A: hands 1–100
Job B: hands 101–200
```

Job B 不可在缺少 A 的 context state 時任意執行。

需要決定：

* dependency chain
* context artifact input
* state checkpoint
* session sequence number

---

## 十五、避免破壞 Distributed Parallelism

Opponent memory 不應讓整個 simulation 系統完全失去平行能力。

請區分：

### Independent Session Parallelism

不同 Session 可以完全平行。

```text
Session A
Session B
Session C
```

### Intra-Session Dependency

同一 Session 的 opponent memory 存在時間依賴。

請設計合理的 shard / checkpoint frequency，使：

* Session 可以長期演化
* 不需要每一 hand 都做 network RPC
* failure retry 成本可接受

---

## 十六、資料模型

請提出具體 domain model。

至少考慮：

```text
Session
SessionParticipant
OpponentProfile
OpponentStat
OpponentTag
OpponentContextSnapshot
OpponentEmbedding
SessionMemoryState
```

如果認為其中某些 object 不應獨立存在，可以合併，但需要說明原因。

---

## 十七、Protobuf Extension

請提供概念級 Protobuf 草案。

不要求完整可編譯，但要接近既有 `tableforge.v1` style。

例如：

```proto
message OpponentContext {
    ...
}
```

必須遵守既有契約：

* UUIDv7 string ID
* schema_version
* protobuf timestamp
* immutable artifact 使用 ArtifactRef
* 不重用 field numbers

---

## 十八、Artifact Types

考慮新增：

```text
OPPONENT_CONTEXT_SNAPSHOT
SESSION_MEMORY_STATE
OPPONENT_EMBEDDING
PLAYER_HISTORY_SHARD
```

但請控制種類數量。

說明：

* 哪些真的需要 artifact
* 哪些只需 metadata row
* 哪些可 content-addressed
* 哪些需要 retention policy

---

## 十九、Failure Cases

至少分析：

* Session Worker crash
* duplicate hand processing
* context snapshot 上傳成功但 Job completion 失敗
* stale context
* profile schema mismatch
* model expects embedding v2 but snapshot is v1
* partial Session retry
* context artifact corruption
* opponent identity collision
* session resumed on another Node

並定義可驗證行為。

---

## 二十、Testing

新增測試門檻至少包含：

### Identity

* seat rotation 不改 participant identity
* 不同 Session identity 不意外共用
* AgentId 不等於 ParticipantId

### Statistics

使用 deterministic action traces 驗證：

* VPIP
* PFR
* 3bet
* fold statistics
* window statistics

### Information Flow

確認：

* folded hole cards 不進 opponent context
* future board 不進 context
* privileged history 不進 online input

### Retry

同一 hand retry：

* 不重複更新 stats
* 不重複更新 embedding state

### Determinism

同一：

```text
Session input
+
seed
+
checkpoint
+
context state
```

必須產生相同 simulation trajectory。

---

## 二十一、開發里程碑

不要直接把 Meta-RL 全做完。

建議提出漸進 roadmap，例如：

### O0

Session / Participant identity

### O1

HUD statistic aggregation

### O2

OpponentContextSnapshot

### O3

Tag / stats conditioned NFSP

### O4

Recent-window + drift

### O5

Learned embedding encoder

### O6

Safe exploitation

### O7

Meta-RL / recurrent policy

但你可以根據現有 M0–M5 roadmap 重新安排更合理順序。

必須指出：

> Opponent modeling 不得阻塞目前 M0–M5 MVP。

應作為既有第一版之上的擴充研究線。

---

# 文件撰寫要求

請輸出完整的：

`05-opponent-modeling.md`

風格必須與現有：

* `01-system.md`
* `02-contracts-and-data.md`
* `03-runtime-and-correctness.md`
* `04-deployment-and-evolution.md`

保持一致。

要求：

1. 使用繁體中文。
2. 使用工程規格語氣，不寫成行銷企劃。
3. 區分：

   * 第一版必要能力
   * 後續研究能力
4. 不重新設計已有 Job lifecycle。
5. 不重新設計 Artifact 系統。
6. 不引入新的 distributed framework，除非現有架構確實無法支援。
7. 優先利用：

   * existing Job
   * ArtifactRef
   * Checkpoint
   * Experiment revision
   * Protobuf
   * Worker runtime
8. 不允許 opponent modeling 破壞 Observation information isolation。
9. 不把人工標籤當 ground truth。
10. 不把 latent embedding 寫成人格或心理狀態。
11. 不把「Tilt」當作可直接觀察心理變數；只能描述 observable behavioral drift。
12. 不宣稱 adaptive strategy 必然優於 equilibrium-oriented baseline。
13. 對不確定或仍屬研究問題的部分，明確標記為 research decision，而非既定事實。
14. 所有設計必須考慮：

    * reproducibility
    * retry
    * determinism
    * distributed execution
    * version compatibility
    * information leakage
15. 若現有規格和新增功能產生衝突，優先保持現有規格，並提出「擴展點」，不要直接修改核心契約。

---

# 最終文件至少應包含

1. 目標與非目標
2. 核心概念
3. Session 與 Participant identity
4. Opponent Profile
5. 統計特徵
6. Tag
7. Learned Embedding
8. Memory 層級
9. OpponentContextSnapshot
10. Agent integration
11. Experience schema extension
12. Session simulation flow
13. Distributed execution semantics
14. Safe exploitation
15. Drift handling
16. Evaluation protocol
17. Information-flow security
18. Failure semantics
19. Protobuf / schema 草案
20. Storage / artifact policy
21. Testing
22. Development milestones
23. 未決研究問題

完成後，再另外附一個短小的：

`Existing Specs Impact`

說明是否需要修改：

* `README.md`
* `01-system.md`
* `02-contracts-and-data.md`
* `03-runtime-and-correctness.md`
* `04-deployment-and-evolution.md`

對每個檔案只列：

```text
No change
```

或：

```text
需要新增哪些最小段落
```

不要大規模重寫既有文件。
