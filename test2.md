# TableForge 專案企劃書

## 1. 專案名稱

**TableForge**

副標題：

**Distributed Multi-Agent Poker AI Training & Evaluation Framework**

中文可稱：

**分散式多代理德州撲克 AI 訓練與評估框架**

---

# 2. 專案背景

現有 Poker AI 研究通常集中於單一演算法，例如：

* Counterfactual Regret Minimization（CFR）
* Deep CFR
* Neural Fictitious Self-Play（NFSP）
* PPO / Actor-Critic
* Search + Neural Network
* Rule-based Solver

這些方法通常在各自的訓練環境中獨立運作。

然而實際 Poker 策略並非存在單一固定對手，而是在多種玩家類型、策略版本及歷史策略之間形成動態 meta。

因此 TableForge 的核心目標不是單純訓練一個 Poker Bot，而是建立一個：

> 可讓不同 AI 演算法、不同版本及不同策略風格共同存在、對戰、學習與演化的 Poker AI 生態。

此外，Poker simulation 與 self-play 具有高度平行化特性，因此本專案將進一步支援多台不同地點的電腦共同提供：

* CPU simulation
* CFR traversal
* GPU training
* inference
* artifact storage

形成非同步的分散式 AI 訓練環境。

---

# 3. 專案願景

TableForge 最終希望成為：

> 一個無單點依賴、支援多種 Poker AI 演算法、多節點非同步訓練、模型版本管理與 League Evolution 的分散式研究平台。

每個節點皆能：

* 獨立進行模擬
* 獨立執行訓練
* 接收其他節點工作
* 提供模型給其他節點
* 保存部分全域狀態
* 與其他節點非同步同步

即使部分節點離線，其餘節點仍可繼續工作。

---

# 4. 核心設計理念

TableForge 採用：

## Federated Multi-Master Architecture

而非：

```text
單一 Master
      ↓
大量 Worker
```

設計為：

```text
Node A ↔ Node B
  ↕         ↕
Node C ↔ Node D
```

所有 Node 皆可以同時具有部分或全部功能：

```text
Coordinator
Simulation Worker
Learner
Agent Registry
Artifact Store
League Manager
Peer Sync
```

因此不存在永久固定的中央節點。

---

# 5. 專案主要目標

第一階段建立完整 Poker AI Training Framework。

支援：

* Heads-Up NLHE
* 6-Max NLHE
* Rule-based agents
* NFSP
* PPO
* Deep CFR
* Historical checkpoints
* League training
* Cross-play evaluation

第二階段加入：

* Neural Search
* Subgame solving
* Search + CFR + NN
* Exploit agents
* Opponent adaptation

第三階段完成：

* 分散式 Node
* Multi-master synchronization
* Remote simulation
* Remote learners
* Distributed artifact storage

最終階段支援：

* 多使用者
* 第三方 Agent submission
* Experiment management
* API
* Dashboard
* Agent Registry
* 多機器資源調度

---

# 6. AI 生態系統

TableForge 不要求所有 Agent 使用相同演算法。

Population 可以包含：

```text
NFSP
Deep CFR
PPO
Search Agent
Rule Bot
Exploit Bot
Historical Agent
Human Strategy Bot
```

例如 6-Max Table：

```text
Seat 1 → NFSP_v42
Seat 2 → DeepCFR_v18
Seat 3 → PPO_v31
Seat 4 → NFSP_v20
Seat 5 → TightBot
Seat 6 → AggroBot
```

每局結束後：

```text
NFSP → NFSP replay
Deep CFR → CFR samples
PPO → rollout buffer
```

不同演算法保留自己的訓練資料。

---

# 7. League Training

TableForge 將 Poker AI 視為一個 population。

League 包含：

```text
Current Agents
Historical Agents
Baseline Agents
Exploit Agents
Experimental Agents
```

Matchmaking 不只安排：

```text
最新模型 vs 最新模型
```

而會混入：

```text
舊模型
不同演算法
固定策略
專門 exploit 的模型
```

避免：

* catastrophic forgetting
* strategy cycling
* narrow self-play meta
* 過度適應單一 opponent

---

# 8. 系統整體架構

```text
                  TableForge Federation

        ┌─────────────── Node A ───────────────┐
        │ League / Learner / Worker / Storage  │
        └─────────────────┬────────────────────┘
                          │
                     Async Sync
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
       ▼                  ▼                  ▼
    Node B             Node C             Node D
Simulation          GPU Learner        Simulation
+ Storage           + Worker           + CFR
       │                  │                  │
       └──────────────────┼──────────────────┘
                          ▼
                    Shared State
                 Eventual Consistency
```

---

# 9. TableForge Node

每個節點都運行一個：

```text
TableForge Node
```

Node 包含：

```text
Peer Manager
Job Manager
Agent Registry Replica
Experiment Registry
Model Cache
Artifact Manager
Simulation Worker
Learner Manager
League Client
Metrics Collector
```

並依該機器資源啟用不同角色。

例如：

```text
Node A
GPU 32GB
32 CPU

Roles:
Simulation
NFSP learner
PPO learner
Storage
```

Node B：

```text
64 CPU
No GPU

Roles:
Simulation
Deep CFR traversal
```

Node C：

```text
48GB GPU

Roles:
Learner
Inference
```

---

# 10. Peer Discovery

初期採：

```text
Whitelist Peer Pool
```

例如：

```yaml
peers:
  - node-a.example
  - node-b.example
  - node-c.example
```

或 VPN 網段：

```text
10.10.0.11
10.10.0.12
10.10.0.13
```

每個 Node 定期：

```text
ping peer
↓
exchange state summary
↓
discover missing events
↓
sync metadata
```

---

# 11. 非同步同步模型

TableForge 不採：

> 所有 Node 必須保持完全同步。

而採：

> Eventual Consistency

例如：

```text
Node A
NFSP_v120

Node B
NFSP_v121
```

Node A 可以繼續使用：

```text
NFSP_v120
```

等下一次 sync 才知道：

```text
NFSP_v121
```

這種 temporary stale state 是允許的。

---

# 12. State Synchronization

不直接同步整個 Database。

採：

```text
Append-only Event Log
```

事件例如：

```text
AGENT_CREATED
CHECKPOINT_CREATED
JOB_CREATED
JOB_CLAIMED
JOB_COMPLETED
MATCH_FINISHED
EXPERIMENT_CREATED
MODEL_PROMOTED
NODE_JOINED
NODE_LEFT
```

例如：

```text
event_id:
nodeA-18432

type:
CHECKPOINT_CREATED

agent:
NFSP

checkpoint:
ckpt_abc91
```

Peer 只需要詢問：

```text
我缺哪些 event？
```

而不需要完整比較資料庫。

---

# 13. Job System

TableForge 不依賴中央 Scheduler。

Job 可由任意 Node 建立。

例如：

```text
job:
simulate

agents:
NFSP_v42
PPO_v19
DeepCFR_v11

hands:
500000
```

所有 Node 都可以看到 job。

有資源的 Node 可以：

```text
claim(job)
```

---

# 14. Job Lease

為避免重複執行：

每個 Job 具有：

```text
Lease Holder
Lease Expiration
```

例如：

```text
job_42

owner:
Node B

lease:
30 minutes
```

Node B 定期 heartbeat。

若 B 離線：

```text
lease expired
↓
job becomes available
↓
Node C can claim
```

因此不需要中央 failure recovery。

---

# 15. Experiment Ownership

完全 leaderless 對 Experiment 編輯不實際。

因此採：

```text
Experiment Owner
```

例如：

```text
Experiment #21

owner:
Node A
```

Node A 負責：

* Experiment config
* League parameters
* stopping criteria
* promotion policy

其他 Node 可以：

```text
run jobs
generate data
train models
```

但不能任意修改 experiment config。

---

# 16. Owner Failover

若 Owner Node 長期離線：

可進行：

```text
ownership transfer
```

例如：

```text
Experiment 21

Node A
↓ offline

Node B
↓ claim ownership

Experiment 21
owner = Node B
```

未來可加入：

```text
quorum
```

避免 network partition 時出現兩個 owner。

---

# 17. Agent Identity

禁止使用單純：

```text
NFSP_v21
```

作為全域 ID。

正式 Agent ID：

```text
agent_id
experiment_id
checkpoint_id
creator_node
content_hash
```

例如：

```text
agent:
nfsp-exp21

checkpoint:
8fd8-313f-993a

creator:
node-B

sha256:
...
```

這能避免不同 Node 同時建立：

```text
v21
```

造成衝突。

---

# 18. Artifact System

大型資料不透過 peer sync event 傳輸。

Event 只同步：

```text
metadata
hash
location
size
```

例如：

```json
{
  "artifact": "ckpt_91",
  "hash": "...",
  "size": 240000000,
  "locations": [
    "Node A",
    "Node C"
  ]
}
```

Node B 需要時：

```text
找最近的 replica
↓
下載 checkpoint
↓
驗證 hash
↓
cache local
```

---

# 19. Artifact 類型

主要包括：

```text
Model checkpoints
Replay shards
CFR samples
Hand histories
Experiment logs
Evaluation results
```

格式可使用：

```text
safetensors
Parquet
NPZ
JSONL
```

---

# 20. 模型一致性

一場 match 開始時必須固定：

```text
Agent ID
Checkpoint ID
Hash
```

例如：

```text
Seat 1
NFSP
checkpoint ABC

Seat 2
PPO
checkpoint XYZ
```

即使中途產生：

```text
NFSP checkpoint DEF
```

該場 Match 仍使用：

```text
ABC
```

直到結束。

---

# 21. Stale Policy

遠端 Worker 不強制使用最新 checkpoint。

可設定：

```text
max_staleness
```

例如：

```text
latest:
NFSP_v100

allowed:
v95–v100
```

只要在範圍內：

```text
Worker 可以繼續產生資料
```

這避免跨地點網路延遲降低 simulation throughput。

---

# 22. Simulation Infrastructure

Simulation 主要使用 CPU。

假設：

```text
10 台 Simulation Node
```

每台：

```text
16–64 CPU cores
```

則可建立：

```text
數十至數百個 environment workers
```

每個 Worker：

```text
claim match
↓
download agent
↓
simulate hands
↓
produce experience
↓
upload result
↓
publish MATCH_FINISHED
```

---

# 23. GPU Infrastructure

假設：

```text
3 台 GPU Node
```

可以動態執行：

```text
NFSP Training
PPO Training
Deep CFR Networks
Neural Value Training
Inference
```

而非固定：

```text
GPU 1 = NFSP
GPU 2 = PPO
```

Scheduler 根據：

```text
GPU memory
job priority
algorithm
queue
```

分配資源。

---

# 24. Poker Engine

TableForge Poker Engine 必須與 AI 完全解耦。

核心功能：

```text
Deck
Card
Hand Evaluator
Betting
Pot
Side Pot
All-in
Minimum Raise
Position
Blind
Showdown
```

Agent 只能收到：

```text
Observation
Legal Actions
```

不能直接取得 internal state。

---

# 25. Information Isolation

完整 Game State：

```text
Hero Cards
Opponent Cards
Deck
Future Cards
```

只能存在於：

```text
Poker Engine
```

送給 Agent 的資料必須經：

```text
Observation Builder
```

例如：

```text
Internal State
      ↓
Observation Builder
      ↓
Seat 1 observation
Seat 2 observation
Seat 3 observation
```

任何 Agent 永遠拿不到：

```text
opponent hole cards
future board
deck order
```

---

# 26. Agent Interface

所有 Agent 必須遵守：

```python
act(observation, legal_actions)

observe_transition(transition)

end_hand(result)

save()

load()
```

Distributed 版本另外提供：

```text
Agent Manifest
```

描述：

```text
agent type
checkpoint
resource requirements
runtime
version
```

---

# 27. 第三方 Agent

未來允許使用者上傳 Agent。

例如：

```text
Agent Package

manifest.yaml
agent.py
requirements.txt
model.safetensors
```

平台讀取：

```text
CPU requirement
RAM requirement
GPU requirement
entrypoint
```

---

# 28. Sandboxing

第三方 Agent 不得直接執行於 Host。

使用：

```text
Docker
```

或其他 sandbox。

限制：

```text
CPU
RAM
GPU
filesystem
network
execution time
```

例如：

```text
2 CPU
4GB RAM
no outbound network
read-only root filesystem
500 ms action timeout
```

---

# 29. Agent Registry

每個 Agent 具有：

```text
Agent ID
Owner
Algorithm
Version
Checkpoint
Creation Time
Parent
Resource Requirements
Status
Evaluation
```

例如：

```text
Agent:
user42-nfsp

Version:
17

Parent:
version 16

Status:
Active
```

---

# 30. Experiment System

每個 Experiment 保存：

```text
Experiment ID
Owner
Config
Seed
Population
League Rules
Training Parameters
Hardware
Start Time
Status
```

並支援：

```text
Clone Experiment
Fork Experiment
Pause
Resume
Stop
```

---

# 31. Evaluation

不能只用單一 ranking。

保存：

```text
bb/100
EV/hand
variance
confidence interval
VPIP
PFR
3-bet
C-bet
WTSD
W$SD
```

以及：

```text
Agent × Agent Matchup Matrix
```

---

# 32. Matchup Matrix

例如：

```text
          NFSP  CFR  PPO  Aggro

NFSP       -    +2   -1    +6
CFR       -2     -   +1    +4
PPO       +1    -1    -    -3
Aggro     -6    -4   +3     -
```

這可以揭露：

```text
A beats B
B beats C
C beats A
```

這類策略循環。

---

# 33. Dashboard

Dashboard 為後期功能，不影響底層 architecture。

顯示：

```text
Cluster Status
Workers
GPU Usage
CPU Usage

Experiments
Training Progress

League
Agent Population

Evaluation
Matchup Matrix

Jobs
Queue / Running / Failed
```

---

# 34. API

提供：

```text
REST
```

與：

```text
gRPC
```

REST 適合：

```text
Experiment management
Agent registry
Dashboard
```

gRPC 適合：

```text
Node communication
Job transport
Artifact metadata
high-frequency RPC
```

---

# 35. Network

不同地點 Node 建議透過：

```text
WireGuard
```

或：

```text
Tailscale
```

形成 private network。

Node 之間不直接暴露：

```text
database
Redis
filesystem
```

到 Internet。

---

# 36. Security

Peer 必須具有：

```text
Node Identity
```

例如：

```text
public/private key
```

Peer whitelist 不只依賴 IP。

因為：

```text
IP ≠ identity
```

正式版本應使用：

```text
mTLS
```

驗證 Node。

---

# 37. 資料庫

每個 Node 可以保存本地 metadata database。

初期：

```text
SQLite
```

後期：

```text
PostgreSQL
```

但跨 Node 不直接做 DB replication。

跨 Node 使用：

```text
event replication
```

---

# 38. Eventual Consistency

適合 eventual consistency 的資料：

```text
Known Agents
Known Checkpoints
Completed Matches
Artifacts
Metrics
```

不適合任意 merge：

```text
Experiment config
Job ownership
Experiment ownership
League policy
```

這些使用：

```text
owner / lease / version
```

控制。

---

# 39. Gossip Sync

節點增加後可加入 Gossip。

例如：

```text
A → B,C
B → D,E
C → F,G
```

Event 會逐步傳播。

不要求：

```text
A → 所有 Node
```

因此節點數增加時仍可維持可接受的同步負擔。

---

# 40. 初期部署規模

第一階段：

```text
2 Nodes
```

測試：

```text
peer discovery
job claim
artifact transfer
checkpoint sync
offline recovery
```

第二階段：

```text
3–5 Nodes
```

加入：

```text
mixed CPU/GPU
```

第三階段：

```text
10 Simulation Nodes
3 GPU Nodes
```

進行正式 distributed league training。

---

# 41. MVP

第一個真正可用版本只需要：

```text
Poker Engine
Rule Bots
NFSP
League
Checkpoint
```

Distributed 部分：

```text
2 Node
Peer Sync
Job Lease
Artifact Transfer
```

成功標準：

```text
Node A 建立 1M-hand Simulation Job

Node B claim

Node B 完成

Node A 得到結果

Node A 訓練新 checkpoint

Node B 自動發現 checkpoint

下一個 Match 使用新版本
```

如果這條完整跑通：

> TableForge 的分散式核心即成立。

---

# 42. 第二階段

加入：

```text
PPO
Deep CFR
Historical population
Matchup evaluation
Experiment system
```

---

# 43. 第三階段

加入：

```text
Neural Search
Subgame Solving
Exploit Agents
Value Networks
```

---

# 44. 第四階段

正式平台化：

```text
Agent submission
User accounts
API
Dashboard
Docker sandbox
Resource Scheduler
```

---

# 45. 技術棧建議

核心：

```text
Python
PyTorch
NumPy
```

Poker simulation：

```text
Python first

必要時：
Rust / C++ acceleration
```

API：

```text
FastAPI
```

Node RPC：

```text
gRPC
```

Local metadata：

```text
SQLite
```

大型部署：

```text
PostgreSQL
```

Artifact：

```text
MinIO / S3 compatible storage
```

Messaging：

初期：

```text
自製 event sync
```

後期：

```text
NATS
```

Container：

```text
Docker
```

---

# 46. 32GB GPU 定位

32GB GPU 足以作為：

```text
NFSP Learner
PPO Learner
Deep CFR NN Trainer
Inference Server
Neural Value Trainer
```

但不應嘗試：

```text
完整 NLHE game tree
```

放入 GPU。

大量：

```text
Replay
CFR samples
Hands
```

應存放：

```text
CPU RAM
SSD
Object Storage
```

---

# 47. 系統最大風險

## Poker Engine correctness

任何：

```text
side pot
raise
all-in
observation leak
```

錯誤都可能污染大量資料。

---

## Distributed consistency

需避免：

```text
duplicate jobs
split brain
double ownership
checkpoint collision
```

---

## AI evaluation

Poker variance 高。

短期：

```text
winrate
```

不代表真正策略提升。

---

## Deep CFR Cost

Deep CFR traversal 可能成為：

```text
CPU bottleneck
```

---

## Scope Expansion

同時製作：

```text
Poker Engine
NFSP
Deep CFR
PPO
Distributed system
Dashboard
Multi-user
Search
```

會造成範圍過大。

因此必須採：

```text
incremental milestones
```

---

# 48. 成功指標

短期：

```text
100萬 hands 無 engine error
```

```text
2 nodes 可非同步合作
```

```text
NFSP 能產生可觀察策略變化
```

中期：

```text
10M–100M hands
```

```text
NFSP / PPO / CFR cross-play
```

```text
historical agents 維持策略多樣性
```

長期：

```text
10+ nodes
```

```text
multiple simultaneous experiments
```

```text
Search + CFR + NN agent
```

```text
third-party Agent submission
```

---

# 49. 最終定位

TableForge 不只是：

> Poker Bot Trainer

而是：

> 一個針對 imperfect-information multi-agent learning 的分散式實驗框架。

Poker 是第一個核心 application。

架構完成後理論上可擴展至：

```text
Kuhn Poker
Leduc Poker
Texas Hold'em
Dou Dizhu
其他不完全資訊博弈
```

---

# 50. 專案核心原則

**第一：**

Poker Engine 與 AI Algorithm 解耦。

**第二：**

Simulation 與 Training 解耦。

**第三：**

Node 與實際工作角色解耦。

**第四：**

模型以 immutable checkpoint 方式存在。

**第五：**

允許 eventual consistency 與 stale policy。

**第六：**

Job 使用 lease，而非永久 ownership。

**第七：**

Experiment 採 owner 制，避免完全 leaderless 帶來的衝突。

**第八：**

各 Node 可在斷線狀態下繼續執行既有工作。

**第九：**

任何 Node 都不應成為整個系統的永久單點故障來源。

**第十：**

先建立可驗證的兩節點 MVP，再擴張至真正 federation。

---

## 總結

TableForge 的核心不是建立一個單一 Poker AI，而是建立一個可持續演化的：

**Distributed Poker AI Ecosystem**

其中：

```text
AI 彼此競爭
↓
產生資料
↓
訓練新策略
↓
形成新 Population
↓
再次競爭
```

而分散式 Node 系統則讓任何可用 CPU / GPU 都能加入這個生態。

最終形成：

> 多演算法、多版本、多節點、非同步、可容錯、可擴張的 Poker AI League Training Network。
