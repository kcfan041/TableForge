# Multi-Agent Poker League

## 32GB GPU 德州撲克多演算法自我對戰訓練平台

**版本：** v0.1
**專案狀態：** Initial Design
**主要目標：** 建立可讓不同 AI 演算法共存於同一張德州撲克牌桌、互相對戰、產生訓練資料並持續演化的 Multi-Agent Poker Training Framework。

---

# 1. 專案概念

本專案建立一個統一的 No-Limit Texas Hold’em 模擬環境，使多種不同 AI 演算法可以在同一張牌桌中對戰。

預計支援：

* NFSP
* Deep CFR
* PPO
* Rule-Based Bot
* 固定策略 Baseline Bot
* 歷史模型 Checkpoint
* 未來的 Search + CFR + Neural Network Agent

系統不要求所有 Agent 使用相同的訓練方式。

所有 Agent 只需要遵守統一的：

1. Observation Interface
2. Action Interface
3. Game Rules
4. Hand History Format
5. Evaluation Protocol

每種 Agent 可使用自己的：

* Replay Buffer
* Regret Buffer
* Neural Network
* Optimizer
* Reward Design
* Training Frequency
* Checkpoint Policy

---

# 2. 核心研究問題

本專案主要研究：

> 當不同學習機制的 Poker AI 存在於同一個 population 中，它們是否能透過互相對戰形成比單純 self-play 更 robust 的策略？

傳統 self-play：

```text
Agent A
   ↓
A vs A
   ↓
更新 A
   ↓
A' vs A'
```

本專案：

```text
        Population
            │
    ┌───────┼────────┐
    ↓       ↓        ↓
  NFSP   DeepCFR    PPO
    ↓       ↓        ↓
       Poker Table
            ↓
       Hand History
            ↓
    ┌───────┼─────────┐
    ↓       ↓         ↓
NFSP Train CFR Train PPO Train
```

因此系統學到的不是單一 self-play opponent distribution，而是：

```text
P(opponent)
=
NFSP
+ CFR
+ PPO
+ Rule Bots
+ Historical Models
+ Exploit Bots
```

---

# 3. 第一階段目標

第一版不追求完整解出 NLHE。

主要目標是建立：

> 一個可以穩定運行數百萬至數億手牌的 Poker AI Experiment Platform。

第一階段需完成：

* 正確的 Poker Environment
* 多 Agent 共存
* Self-play
* Cross-play
* League matchmaking
* Training pipeline
* Checkpoint 系統
* Matchup matrix
* bb/100 評估
* GPU memory control
* Experiment logging

第一階段成功的定義不是：

> AI 已經超越人類頂尖玩家。

而是：

> 系統能穩定產生可重現的 AI 演化過程。

---

# 4. 硬體目標

主要硬體假設：

```text
GPU:
1 × 32GB VRAM

CPU:
建議 12–32 cores

System RAM:
最低：32GB
建議：64GB
理想：128GB

Storage:
至少 500GB SSD
建議 1–2TB NVMe
```

GPU 主要負責：

* NFSP neural networks
* Deep CFR regret/value networks
* PPO policy/value networks
* Batch inference
* Batch training

CPU 主要負責：

* Poker simulation
* Game tree traversal
* CFR traversal
* Environment workers
* Replay preprocessing

RAM 主要負責：

* Replay buffers
* Reservoir buffers
* CFR samples
* Hand histories
* Experience queues

原則：

> 不將完整 game tree 放入 GPU。

GPU 只保存：

```text
Model
Optimizer
Current Batch
Temporary Activations
```

大量 experience 存放於：

```text
CPU RAM
↓
必要時
↓
SSD
```

---

# 5. 遊戲模式

第一階段支援：

## 5.1 Heads-Up NLHE

作為主要除錯與 benchmark 環境。

```text
Player 1
Player 2
```

用途：

* 驗證 NFSP
* 驗證 Deep CFR
* 計算較可靠的 exploitability proxy
* Debug betting logic

---

## 5.2 6-Max NLHE

最終主要 training environment。

```text
BTN
SB
BB
UTG
HJ
CO
```

預設：

```text
Starting Stack = 100 BB
SB = 0.5 BB
BB = 1 BB
```

第一版不考慮：

* tournament blind increase
* ante
* bounty
* straddle

後續再加入。

---

# 6. Action Space

完整 NLHE betting size 幾乎是連續 action space。

第一版採離散化：

```text
FOLD
CHECK
CALL

RAISE_25
RAISE_33
RAISE_50
RAISE_75
RAISE_100
RAISE_150

ALL_IN
```

其中數字代表：

```text
pot fraction
```

例如：

```text
RAISE_50
=
raise 0.5 pot
```

實際 legality 由 environment 處理。

若下注低於 minimum raise：

```text
clamp → min_raise
```

若下注超過 stack：

```text
clamp → all-in
```

---

# 7. Observation Design

每個 Agent 只能看到符合玩家資訊範圍的資料。

禁止直接輸入：

```text
其他玩家 hole cards
future cards
deck state
```

Observation：

```text
Player State
├── Hole Cards
├── Position
├── Stack
├── Current Bet
└── Player Status

Board
├── Flop
├── Turn
└── River

Pot
├── Main Pot
└── Side Pots

Game State
├── Street
├── Current Player
├── Effective Stack
└── SPR

History
├── Previous Actions
├── Bet Sizes
├── Positions
└── Street boundaries
```

---

# 8. State Encoding

第一版採固定向量 encoding。

範例：

```text
Cards
    rank one-hot
    suit one-hot

Position
    one-hot

Street
    one-hot

Stack
    normalized by BB

Pot
    normalized by BB

Bet
    normalized by pot

SPR
    scalar

Action history
    last N actions
```

未來可以改成：

```text
Transformer Sequence Encoding
```

例如：

```text
BTN raise 2.5
BB call
FLOP Ah 7c 2s
BB check
BTN bet 0.33 pot
```

直接形成 action token sequence。

---

# 9. Agent Interface

所有 AI 都實作：

```python
class PokerAgent:

    def act(
        self,
        observation,
        legal_actions
    ):
        ...

    def observe_transition(
        self,
        transition
    ):
        ...

    def end_hand(
        self,
        result
    ):
        ...

    def train_step(self):
        ...

    def save_checkpoint(self):
        ...

    def load_checkpoint(self):
        ...
```

Environment 完全不需要知道 Agent 內部使用：

```text
CFR
DQN
PPO
Rule system
Random
Transformer
```

---

# 10. 初始 Agent Population

第一版建立以下 population：

```text
Population
│
├─ NFSP_A
├─ NFSP_B
│
├─ DeepCFR_A
├─ DeepCFR_B
│
├─ PPO_A
│
├─ Rule_Tight
├─ Rule_Aggressive
├─ Rule_Loose
├─ Rule_Random
│
└─ Historical Checkpoints
```

一張 6-max table 隨機抽取：

```text
6 agents
```

例如：

```text
BTN → NFSP_v32

SB → DeepCFR_v18

BB → PPO_v51

UTG → TightBot

HJ → NFSP_v12

CO → DeepCFR_v7
```

座位隨機化。

---

# 11. NFSP Agent

NFSP 包含兩個主要 network：

```text
Best Response Network

Q(s,a)
```

與：

```text
Average Policy Network

π(a|s)
```

另外包含：

```text
RL Replay Buffer

SL Reservoir Buffer
```

行為策略：

```text
with probability η:

    best response

otherwise:

    average policy
```

Replay Buffer：

```text
1M–10M transitions
```

放 CPU RAM。

SL Reservoir：

```text
1M–20M samples
```

採 reservoir sampling。

---

# 12. Deep CFR Agent

Deep CFR 核心：

```text
Traversal
    ↓
Counterfactual Regret
    ↓
Regret Buffer
    ↓
Regret Network
```

每個 player 可擁有：

```text
Advantage Network
```

輸出：

```text
A(I,a)
```

表示 infoset `I` 下 action `a` 的 regret approximation。

策略由：

```text
Regret Matching
```

產生。

另外保存：

```text
Strategy Samples
```

用來訓練 average strategy network。

---

# 13. PPO Agent

PPO 在本專案不是 equilibrium solver。

主要角色：

> 提供不同於 CFR-family 的 adaptive opponent。

Network：

```text
Shared Encoder
│
├─ Policy Head
└─ Value Head
```

Reward：

```text
Net Chips Won / BB
```

建議：

```text
reward = Δstack / BB
```

不額外加入：

```text
survival reward
action reward
aggression reward
```

避免改變真正遊戲 objective。

---

# 14. Rule-Based Agents

需要保留固定 bot。

原因：

如果所有 Agent 都會持續更新：

```text
Environment Distribution
```

本身也一直改變。

這使 evaluation 很難。

因此固定：

```text
Tight Passive

Tight Aggressive

Loose Aggressive

Loose Passive

Random

Simple Equity Bot
```

作為 reference points。

---

# 15. League Training

系統核心為：

```text
League Manager
```

負責：

* Agent registration
* Matchmaking
* Checkpoint sampling
* Seat randomization
* Opponent distribution
* Evaluation scheduling

League 中包含：

```text
Main Agents

Historical Agents

Exploit Agents

Baseline Agents
```

---

# 16. Historical Population

每隔：

```text
N training steps
```

保存 checkpoint。

例如：

```text
NFSP_000010
NFSP_000020
NFSP_000030
...
```

歷史模型仍有一定機率被抽回牌桌。

例如 matchmaking：

```text
40% current agents

30% historical agents

20% baseline bots

10% exploit agents
```

避免：

```text
最新模型 A
只學會對付
最新模型 B
```

形成循環 meta。

---

# 17. Matchmaking

第一版可以使用簡單 weighted random。

例如：

```python
opponent_pool = {
    "current": 0.40,
    "historical": 0.30,
    "baseline": 0.20,
    "exploit": 0.10
}
```

未來增加：

```text
PFSP
Prioritized Fictitious Self-Play
```

根據 matchup winrate 選 opponent。

例如：

如果：

```text
A vs B = 95%
```

就降低 A 對 B 的頻率。

如果：

```text
A vs C = 48%
```

提高 C 被抽到的機率。

---

# 18. Training Architecture

系統採 Actor-Learner 架構。

```text
           ┌──────────────┐
           │ LeagueManager│
           └──────┬───────┘
                  ↓
        ┌───────────────────┐
        │ Environment Worker│ × N
        └────────┬──────────┘
                 ↓
          Experience Queue
                 ↓
    ┌────────────┼────────────┐
    ↓            ↓            ↓
NFSP Learner DeepCFR Learner PPO Learner
    ↓            ↓            ↓
Checkpoint   Checkpoint   Checkpoint
    └────────────┼────────────┘
                 ↓
            Model Pool
```

---

# 19. Environment Workers

Poker simulation 本身主要使用 CPU。

例如：

```text
16 CPU cores
```

可運行：

```text
8–32 workers
```

每個 worker：

```text
建立 table
↓
League 選 Agent
↓
打 N hands
↓
送 trajectory
↓
重新 matchmaking
```

Inference 可選：

```text
local CPU inference
```

或：

```text
batched GPU inference
```

推薦後者。

---

# 20. Batched Inference Server

為避免：

```text
每一個 action
都呼叫一次 GPU
```

導致 GPU utilization 極低。

建立：

```text
Inference Server
```

Worker 傳：

```text
Observation Queue
```

Server 收集：

```text
32
64
128
256
```

個 state 後一起 inference。

流程：

```text
Worker 1 ─┐
Worker 2 ─┤
Worker 3 ─┤
Worker N ─┘
          ↓
      Batch Queue
          ↓
         GPU
          ↓
     Action Queue
```

---

# 21. 32GB VRAM Budget

目標限制：

```text
< 28GB active usage
```

保留：

```text
~4GB safety margin
```

初期建議：

```text
NFSP models
≈ 2–4GB

Deep CFR models
≈ 4–8GB

PPO
≈ 1–3GB

Optimizer states
≈ 5–8GB

Training batch
≈ 3–5GB

Inference
≈ 1–2GB
```

不要求所有 network 永久存在 GPU。

使用：

```text
model swapping
```

或：

```text
sequential training
```

例如：

```text
Train NFSP
↓
release training activations

Train CFR
↓
release

Train PPO
```

---

# 22. Network Baseline

第一版不要使用巨大模型。

建议 MLP：

```text
Input
↓
Linear 1024
↓
LayerNorm
↓
ReLU
↓
Linear 1024
↓
ReLU
↓
Linear 1024
↓
ReLU
↓
Heads
```

大約：

```text
5M–30M parameters
```

就足以開始。

第二階段再改：

```text
Transformer
```

---

# 23. Replay Storage

所有 agent 不共用 training buffer。

```text
NFSP
├─ RL Buffer
└─ SL Buffer

DeepCFR
├─ Advantage Buffer
└─ Strategy Buffer

PPO
└─ Rollout Buffer
```

但可以共用：

```text
Global Hand Database
```

---

# 24. Global Hand Database

每局記錄：

```json
{
  "game_id": "...",
  "players": [],
  "button": 3,
  "stacks": [],
  "hole_cards": [],
  "board": [],
  "actions": [],
  "pot_history": [],
  "showdown": {},
  "rewards": []
}
```

正式訓練資料中：

```text
Hole cards visibility
```

必須按照 Agent observation 建立。

完整牌局僅提供：

```text
offline analysis
```

不能洩漏給 actor。

---

# 25. Evaluation

不能單純用：

```text
winrate
```

判斷 Poker Agent 強度。

主要 metrics：

```text
bb/100

EV/hand

matchup matrix

variance

fold frequency

VPIP

PFR

3-bet

C-bet

WTSD

W$SD
```

Heads-up additionally：

```text
Exploitability Proxy
```

---

# 26. Matchup Matrix

定期建立：

```text
             NFSP   CFR   PPO  Tight Aggro

NFSP          --    +2.1  -0.3  +6.2 +1.4

CFR          -2.1    --   +1.2  +5.9 +3.0

PPO          +0.3   -1.2   --   +8.2 -2.4
```

單位：

```text
bb/100
```

至少每 matchup：

```text
100k hands
```

理想：

```text
1M+ hands
```

否則 variance 很大。

---

# 27. 防止 Overfitting

主要措施：

### Historical opponents

永遠保留舊模型。

### Randomized seating

避免學 position-specific opponent identity。

### Opponent ID 不作 input

第一版禁止：

```text
Agent sees opponent algorithm
```

### Mixed population

維持：

```text
CFR
NFSP
PPO
Rule Bot
Old Models
```

---

# 28. Exploit Agents

後期加入特殊 Agent：

```text
ExploitBot_NFSP

ExploitBot_CFR

ExploitBot_PPO
```

只針對某一 fixed checkpoint 訓練。

例如：

```text
ExploitBot_NFSP
```

永遠對：

```text
NFSP_v100
```

訓練。

用途是測：

> NFSP_v100 到底有多容易被 exploit？

這比只看 winrate 有價值。

---

# 29. Repository Layout

建議：

```text
poker-ai/

├── config/
│   ├── game.yaml
│   ├── league.yaml
│   ├── nfsp.yaml
│   ├── deepcfr.yaml
│   └── ppo.yaml
│
├── poker/
│   ├── cards.py
│   ├── deck.py
│   ├── evaluator.py
│   ├── betting.py
│   ├── pot.py
│   ├── state.py
│   └── environment.py
│
├── agents/
│   ├── base.py
│   │
│   ├── nfsp/
│   │   ├── agent.py
│   │   ├── model.py
│   │   ├── replay.py
│   │   └── trainer.py
│   │
│   ├── deepcfr/
│   │   ├── agent.py
│   │   ├── traversal.py
│   │   ├── regret.py
│   │   └── trainer.py
│   │
│   ├── ppo/
│   │   ├── agent.py
│   │   ├── model.py
│   │   └── trainer.py
│   │
│   └── rule/
│       ├── tight.py
│       ├── aggressive.py
│       └── random.py
│
├── league/
│   ├── manager.py
│   ├── matchmaking.py
│   ├── population.py
│   └── checkpoint.py
│
├── workers/
│   ├── environment_worker.py
│   └── inference_server.py
│
├── replay/
│   ├── buffer.py
│   ├── reservoir.py
│   └── storage.py
│
├── evaluation/
│   ├── evaluator.py
│   ├── matchup.py
│   ├── metrics.py
│   └── exploit.py
│
├── data/
│
├── checkpoints/
│
├── logs/
│
├── scripts/
│   ├── train.py
│   ├── evaluate.py
│   └── benchmark.py
│
└── tests/
```

---

# 30. Technology Stack

主要：

```text
Python 3.12
PyTorch
NumPy
```

可選：

```text
Ray
```

用於：

```text
distributed workers
```

初期也可以不用 Ray。

直接：

```text
multiprocessing
```

比較容易 debug。

Logging：

```text
TensorBoard
```

或：

```text
Weights & Biases
```

---

# 31. 訓練循環

主 loop：

```python
while training:

    tables = league.create_matches()

    trajectories = run_games(tables)

    dispatch_experience(trajectories)

    nfsp.train()

    deep_cfr.train()

    ppo.train()

    if checkpoint_due():
        save_models()

    if evaluation_due():
        evaluate_population()
```

---

# 32. Game Generation 與 Training 分離

不要寫成：

```text
打一手
↓
train
↓
打一手
↓
train
```

應改為：

```text
Environment Workers
不停產生 hands

           ↓

Experience Queue

           ↓

Learners
持續 consume samples
```

這樣 CPU 與 GPU 才能同時使用。

---

# 33. 第一版訓練規模

Milestone 1：

```text
100k hands
```

目標：

```text
確認 game logic
確認 buffer
確認 training loop
```

Milestone 2：

```text
1M hands
```

目標：

```text
Agent 開始出現策略差異
```

Milestone 3：

```text
10M hands
```

開始研究：

```text
cross-play
meta
historical robustness
```

Milestone 4：

```text
100M+ hands
```

才開始討論：

```text
長期策略品質
```

---

# 34. 開發順序

## Phase 1

Poker Engine

完成：

```text
cards
ranking
betting
all-in
side pot
showdown
```

必須大量 unit tests。

---

## Phase 2

Rule Bots

先做：

```text
RandomBot
CallBot
TightBot
AggroBot
```

讓：

```text
6 bots
```

可以穩定跑：

```text
1M hands
```

---

## Phase 3

NFSP

第一個 learning agent。

先 Heads-up。

確認：

```text
buffer
DQN
average policy
reservoir sampling
```

---

## Phase 4

League

加入：

```text
checkpoint
historical models
matchmaking
```

---

## Phase 5

Deep CFR

先在：

```text
Kuhn Poker
```

驗證 CFR implementation。

再：

```text
Leduc Poker
```

最後接 NLHE abstraction。

---

## Phase 6

PPO

加入不同 learning dynamics。

---

## Phase 7

6-Max League

形成：

```text
NFSP
DeepCFR
PPO
Rule Bots
Historical Bots
```

混合 ecosystem。

---

# 35. 第一版不做的功能

為避免 scope 爆炸，v0.x 不做：

```text
Full continuous betting space

Real-time subgame solving

Full Nash equilibrium calculation

Opponent natural-language modeling

Human HUD

GTO chart GUI

Tournament mode

Multi-GPU

Distributed cluster
```

---

# 36. 第二階段：Neural Search

等 league 穩定後加入：

```text
Search Agent
```

流程：

```text
Current State
↓
Generate Subgame
↓
CFR iterations
↓
Leaf states
↓
Neural Value Network
↓
Resolve Strategy
↓
Action
```

此時形成：

```text
Search + CFR + NN
```

Agent。

這個 Agent 也可以加入 league。

---

# 37. Neural Value Function

未來 network 預測：

```text
V(state, range)
```

而不是單純：

```text
V(state)
```

原因：

Poker 是 imperfect-information game。

相同 board：

```text
Ah 8s 2c
```

如果雙方 range 不同，value 完全不同。

因此 future architecture 可輸入：

```text
Public State

Hero Range

Opponent Range

Pot

Stack

Action History
```

輸出：

```text
Expected Value Vector
```

---

# 38. Range Representation

未來 Heads-up 可以使用：

```text
1326 hand combos
```

或壓成：

```text
169 canonical hands
```

第一版 neural agent 不強制完整 range input。

但 architecture 應預留：

```text
range tensor
```

---

# 39. Reproducibility

每次 experiment 保存：

```text
git commit

config

random seed

model checkpoint

optimizer state

league population

training step

hands played
```

例如：

```text
experiments/
└── exp_00041/
    ├── config.yaml
    ├── population.json
    ├── metrics.csv
    └── checkpoints/
```

---

# 40. Critical Tests

Poker engine 必須測：

```text
hand ranking

split pot

side pot

all-in

minimum raise

fold

check

call

button rotation

blind posting

heads-up blind rules

odd chips
```

任何 game logic bug 都會污染：

```text
數千萬 hands
```

因此 poker engine correctness 優先於 neural network。

---

# 41. 預期問題

### Non-stationarity

所有 Agent 都在變。

解法：

```text
Historical Population
```

---

### Cyclic Strategies

例如：

```text
A beats B

B beats C

C beats A
```

因此不能只存在：

```text
單一 rating
```

必須保留：

```text
matchup matrix
```

---

### Variance

Poker variance 極高。

不能：

```text
1000 hands
```

就判定模型強弱。

---

### GPU starvation

Environment 太慢：

```text
GPU 等 CPU
```

解法：

```text
parallel workers
+
batch inference
```

---

### CPU bottleneck

Deep CFR traversal 可能吃大量 CPU。

需要 profiler 判斷。

---

# 42. 第一版推薦設定

如果硬體為：

```text
1 × 32GB GPU
64GB RAM
16-core CPU
```

建議：

```text
16 environment workers

NFSP:
5–10M parameter network

PPO:
5–10M parameter network

DeepCFR:
10–30M parameter network

Batch:
1024–8192

Replay:
CPU RAM

Inference:
batched GPU

Training:
mixed precision
```

---

# 43. MVP

真正第一個 MVP 不需要 Deep CFR。

只需要：

```text
Poker Engine

+

RandomBot

+

TightBot

+

AggroBot

+

NFSP

+

League Manager
```

完成後能做到：

```text
NFSP
vs
不同 style bot
vs
舊版 NFSP
```

且產生：

```text
bb/100 matrix
```

就已經證明整個 architecture 可行。

之後加入：

```text
Deep CFR

PPO

Search Agent
```

不用重寫底層。

---

# 44. 最終架構

最終希望形成：

```text
                   ┌────────────────┐
                   │ League Manager │
                   └───────┬────────┘
                           │
                  Matchmaking
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
       ↓                   ↓                   ↓
     NFSP               Deep CFR              PPO
       │                   │                   │
       ├─────────┐         │         ┌─────────┤
       │         ↓         ↓         ↓         │
       │      Historical Models               │
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ↓
                     Poker Tables
                           ↓
                    Hand Database
                           ↓
        ┌──────────────────┼──────────────────┐
        ↓                  ↓                  ↓
     Training          Evaluation        Analysis
        ↓                  ↓                  ↓
   New Models      Matchup Matrix     Strategy Stats
        │
        └───────────────────────→ Population
```

---

# 45. 專案核心原則

本專案最重要的設計原則：

> Poker Environment 與 AI Algorithm 必須完全解耦。

其次：

> 不要求所有 AI 使用相同的資料或 training objective。

第三：

> 所有 AI 必須在相同遊戲規則與資訊限制下競爭。

第四：

> 不將單一 winrate 當作唯一強度指標。

第五：

> 永遠保留歷史模型，避免 population 遺忘舊策略。

最終目標不是只訓練出一個 Poker Bot，而是建立：

> 一個可以觀察多種 AI 策略互相演化、互相 exploit、互相適應的 Poker AI 生態系統。

這個架構在單張 32GB GPU 上，可以先以中小型 neural network、CPU simulation、GPU batched inference 與分離式 replay buffer 實現，再依硬體資源逐步擴張。
