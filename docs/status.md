# TableForge 開發進度

最後更新：commit `5abd094` 之後。每完成一個階段，本文件與程式碼在同一個 commit 更新。

狀態定義：

- **已完成**：功能實作完成，並有自動測試或實際執行驗證。
- **部分完成**：主要流程可運作，但缺少規格要求的部分，缺漏列於備註。
- **延後**：程式已就緒，但驗收需要目前沒有的環境。
- **未開始**：尚未實作。

## 總覽

| 里程碑 | 內容 | 狀態 | 主要 commit |
|---|---|---|---|
| M0 | 契約骨架、ID、Job/Artifact、SQLite、本機 artifact store | 已完成 | `e6b9178` |
| M1 | Heads-Up Poker Engine、Rule Bots、資金守恆 | 已完成 | `f3b1679`–`df4b40d` |
| M2 | 單機工作流：lease、heartbeat、失敗/取消、experiment、worker loop | 已完成 | `5b27e50`–`de6318d` |
| C0/C1 | 能力模型、CPU/RAM/磁碟偵測、`doctor` | 已完成 | `ab79ff0`、`0e95ed3` |
| C2 | NVIDIA GPU 偵測、GPU 測試分級 | 未開始 | |
| C3 | Scheduler 資源過濾、reservation、capability revision | 已完成 | `179933e` |
| C4 | 降級執行計畫（fallback） | 未開始 | |
| C5 | UI 呈現能力資訊 | 未開始 | 平台化階段 |
| M3a | gRPC 傳輸、enrollment/session、TLS、artifact 傳輸 | 已完成 | `61cf545`、`b5c4c51` |
| M3a 第 2 階段 | Mac 實機驗證、跨平台結果一致性 | 延後 | 需在 Mac 上執行 |
| M3a 實機 | 兩台電腦實際連線驗收 | 延後 | 需要 Tailscale |
| M3b | PostgreSQL、MinIO、Docker | 未開始 | |
| M4 | NFSP 訓練與評估（CPU） | 部分完成 | `5abd094` |
| M5 | League、歷史模型、matchup matrix、1,000 萬手 soak | 未開始 | |
| O0–O7 | 對手建模研究線 | 未開始 | M5 之後 |

目前測試：156 個測試全部通過，Ruff、mypy strict 通過。

## 已完成項目與驗收方式

### M0 契約骨架
- UUIDv7 ID、Job/Artifact 領域模型、repository ports。
- SQLite Job repository：冪等建立、衝突偵測。
- 本機 content-addressed artifact store：hash 驗證、去重、原子提交。
- 驗收：contract tests。

### M1 Poker Engine
- 牌、洗牌（固定 seed 可重現）、5–7 張牌型評估。
- Heads-Up 下注狀態機：盲注、行動順序、min-raise、短碼 all-in 不重開加注權。
- 結算：showdown、平分、未跟注籌碼退還。
- 每個座位的 Observation，看不到對手手牌與未發公牌。
- Rule Bots：Random、Call、Tight、Aggro。
- 驗收：Hypothesis property tests；**100 萬手資金守恆 soak，零錯誤，約 5,000 手/秒**。

### M2 單機工作流
- Job lease、heartbeat 續租、過期重排、fencing、冪等完成。
- 失敗立即重試或終止、合作式取消、結構化錯誤紀錄。
- Node registry、capability revision、TTL、attempt provenance、資源 reservation。
- Experiment revision、preset、`start/status/stop`、持續 worker loop、JSON log。
- 驗收：整合測試；CLI 端到端實跑。

### C0/C1/C3 能力判斷
- 偵測 CPU、RAM（Windows、Linux、macOS）、磁碟。
- 每個資料目錄有固定的 NodeId。
- Workload 相容判斷；不相容或能力未知時不派工；高優先 GPU Job 不會擋住 CPU Job。
- 驗收：單元測試；本機 `doctor` 實跑正確顯示 GPU workload 為 UNSUPPORTED。

### M3a 遠端傳輸
- gRPC Coordinator server 與 Worker client，Worker 只依賴 `CoordinatorClient` 介面。
- 一次性 enrollment token、session token；節點不能冒用其他節點身分。
- 預設 TLS（自簽憑證）；只有明確 `--insecure` 才不加密。
- Artifact 分段上傳/下載、hash 驗證、Worker 本機 cache。
- 網路錯誤指數退避重試；lease 失效等錯誤轉成明確例外，不盲目重試。
- 驗收：TLS 與非 TLS 兩種模式的整合測試；**真實多程序測試**：Coordinator 與 Worker 分開執行、強制關閉 Worker 後由另一個 Worker 接手完成；README 兩節點流程在乾淨目錄實跑通過。

## M4 NFSP：部分完成

已完成：
- 固定長度狀態編碼 `poker-hunlhe-vector-v1`，測試確認不含對手手牌與未發公牌。
- 抽象動作 `nlhe-discrete-v1`（fold、check/call、半池、滿池、all-in），測試確認一律轉成合法下注。
- NFSP：Q 網路、平均策略網路、circular replay、reservoir buffer。
- Training Job 輸出 checkpoint；推論權重與訓練狀態分開存；固定 seed 產生位元相同的 checkpoint。
- Evaluation Job：交換座位、95% 信賴區間。
- `hu-nfsp-cpu-smoke` preset 自動依序訓練、評估，CLI 實跑約 10 秒。

尚缺：
- 訓練讀取 simulation 產生的 experience shard 與 dataset snapshot（見「與規格的差異」）。
- Checkpoint registry、candidate promotion。
- Evaluation 依信賴區間寬度決定停止條件。
- 可證明模型確實變強的長時間訓練與評估。Smoke 設定只有 2,000 手，信賴區間約 ±300 bb/100，只能證明流程正確。

## 延後項目

### Mac 實機驗證（M3a 第 2 階段）
在 Mac 上執行：

```
uv sync
uv run pytest
uv run tableforge doctor
```

確認記憶體偵測正確、GPU workload 顯示 UNSUPPORTED。另外用相同 seed 在兩台電腦各跑同一個 simulation Job，比對輸出 hash。

### 兩節點實機驗收
兩台電腦目前不在同一個網路，Windows 沒有 Wi-Fi，需要先安裝 Tailscale。驗收項目：Mac 領取並完成 Job；中途中斷後由另一個 Worker 接手；結果不重複計入；GPU Job 不派給 Mac。

## 與規格的差異

以下是刻意的簡化，之後需要補上或更新規格：

| 項目 | 規格 | 目前實作 |
|---|---|---|
| NFSP 訓練資料 | Simulation 產生 experience shard，訓練讀取固定的 dataset snapshot | 訓練 Job 自己對打並學習 |
| Artifact 傳輸 | Coordinator 不轉送大型 artifact，Worker 直接連 storage | 經由 Coordinator 的 gRPC 串流，單檔上限 1 GiB |
| 事件 | 狀態變更與 outbox 事件同一個 transaction | 尚無 outbox 表 |
| 兩節點 metadata | PostgreSQL | 仍為 SQLite（單一 Coordinator 可運作） |
| Experiment 狀態推進 | 由 Coordinator 背景處理 | 查詢 `experiment status` 時才推進 NFSP 管線下一階段 |

## 下一步候選

依建議優先順序：

1. **M4 補齊**：訓練改讀 simulation 資料、checkpoint registry、promotion。
2. **Experiment 背景推進**：不需查詢 status 也能自動進入下一階段。
3. **M3b**：PostgreSQL、MinIO、Docker。
4. **C2/C4**：NVIDIA 偵測與降級執行計畫。GPU 相關驗證需要有 GPU 的節點。
5. **M5 League**。
