# TableForge 實作架構索引

目前實作進度與和規格的差異見 [../status.md](../status.md)；長期願景見 [../vision.md](../vision.md)。

這組文件是 TableForge 第一版的實作規格。若與 `test.md` 或 `test2.md` 的概念描述衝突，以本目錄文件作為第一版工程決策；概念文件保留長期願景。

## 文件

1. [系統架構](01-system.md)：部署拓撲、程式目錄、元件責任、Poker/Agent interface 與擴展邊界。
2. [契約與資料模型](02-contracts-and-data.md)：Protobuf 規則、Job/Lease/Artifact/Checkpoint、資料表、事件及版本相容。
3. [執行流程與正確性](03-runtime-and-correctness.md)：Node、Simulation、NFSP、Evaluation、故障語義、安全與測試門檻。
4. [部署與演進](04-deployment-and-evolution.md)：容器、單機/兩節點部署、監控、GC、里程碑與 federation 演進。
5. [對手建模](05-opponent-modeling.md)：Session/Participant identity、跨手牌記憶、context snapshot、自適應策略與分散式正確性。
6. [節點能力與使用者操作](06-capability-and-user-operations.md)：硬體偵測、workload 相容判斷、測試分級、CLI 與 UI 責任。
7. [多人共用與信任模型](07-multi-party-and-trust.md)：四種共用模式的差距分析、信任的小團體（模式 B）的身分、權限、配額與路線。

## 第一版架構決策

- 單一 Coordinator writer，不直接實作 multi-master。
- Worker 採 pull model，使用 lease、heartbeat、retry 與 fencing。
- Metadata 與大型 artifact 分離。
- Job input、dataset snapshot、checkpoint 與 MatchSpec 均不可變。
- 單機使用 SQLite/LocalArtifactStore；兩節點使用 PostgreSQL/MinIO。
- 跨程序契約使用 versioned Protobuf/gRPC。
- 第一版只做 Heads-Up、Rule Bots、NFSP；其餘演算法使用同一 Job/Artifact/Agent 邊界擴充。
- 硬體能力與 workload 相容判斷位於 Scheduler/Application 層；CLI 與後續 UI 只呈現相同判定結果。
- 多人共用的下一階段目標為信任的小團體（模式 B）：單一 Coordinator 不變，在控制面加入使用者、專案權限、配額與稽核；公開志願計算與去中心化不在近期範圍。

## 實作起點

依 `04-deployment-and-evolution.md` 的 M0 至 M5 實作。不得在 M3 以前加入 federation，也不得在 Poker Engine 通過 1M-hand invariant test 前使用產生資料訓練模型。Opponent modeling 為 M5 後的 O0 至 O7 擴充研究線，不得阻塞核心 MVP。
