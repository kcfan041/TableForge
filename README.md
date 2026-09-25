# TableForge

分散式多代理德州撲克 AI 訓練與評估框架。目前支援 Heads-Up No-Limit Hold'em、Rule Bots、NFSP（CPU 與 NVIDIA GPU），以及單一 Coordinator 搭配多個 Worker 的遠端執行。

- 開發進度：[docs/status.md](docs/status.md)
- 架構規格：[docs/architecture/README.md](docs/architecture/README.md)

## 安裝

需要 [uv](https://docs.astral.sh/uv/)，它會自動下載 Python 3.12。

```
git clone https://github.com/kcfan041/TableForge.git
cd TableForge
uv sync --extra learning
```

`--extra learning` 會安裝 PyTorch，用來訓練和評估 NFSP。只執行 simulation 的節點（例如一台筆電）可以省略，改用 `uv sync`。

> 注意：之後若只執行 `uv sync` 而沒有加 `--extra learning`，PyTorch 會被移除，NFSP 相關測試會顯示為略過而不是失敗。

有 NVIDIA GPU 的 Windows／Linux 電腦：

```
uv sync --extra learning --extra gpu
```

Windows 和 Linux 會自動安裝 CUDA 版 PyTorch（需要支援 CUDA 13 的驅動程式）；`--extra gpu` 另外安裝 NVML，用來讀取 GPU 資訊。`doctor` 會列出偵測到的 GPU 與 GPU workload 會使用的 batch 大小。

## 單機快速開始

檢查這台電腦能執行哪些工作：

```
uv run tableforge doctor
```

跑一個 Rule Bot simulation 實驗（1,000 手，分成 4 個 Job）：

```
uv run tableforge experiment create --preset hu-rule-smoke
uv run tableforge experiment start <experiment-id>
uv run tableforge worker start --max-jobs 10 --idle-seconds 0
uv run tableforge experiment status <experiment-id>
```

資料預設存在目前目錄的 `.tableforge/`，可用 `--data-dir <路徑>` 指定，這個選項要放在子指令之前。

## NFSP

多輪訓練（每輪：Actor 對打產生資料 → Learner 訓練 → candidate 與目前 active 對打 → 決定是否升級）：

```
uv run tableforge experiment create --preset hu-nfsp-cpu-iterative
uv run tableforge experiment start <experiment-id>
uv run tableforge worker start --max-jobs 100 --idle-seconds 0
uv run tableforge experiment status <experiment-id>
uv run tableforge checkpoint list
uv run tableforge checkpoint show <checkpoint-id>
```

Worker 閒置時會自動推進實驗到下一階段，一次 `worker start` 就能跑完 3 輪。`checkpoint show` 會顯示升級或拒絕的原因。規則是只有 candidate 確定比目前 active 差（95% 信賴區間上限 < 0）時才拒絕。

League 模式會讓 Actor 同時對上自己、歷史 checkpoint 和 Rule Bots，最後產生 matchup matrix：

```
uv run tableforge experiment create --preset hu-nfsp-cpu-league
uv run tableforge experiment start <experiment-id>
uv run tableforge worker start --max-jobs 200 --idle-seconds 0
uv run tableforge league matrix <experiment-id>
```

Matrix 每格是「列」對「欄」的 bb/100，`*` 表示 95% 信賴區間不含 0。

快速檢查用的 `hu-nfsp-cpu-smoke` 只有一次訓練加一次對 Rule Bots 的評估，結果會顯示在 `experiment status` 的 `evaluation` 欄位。

`hu-nfsp-gpu-iterative` 和 `hu-nfsp-cpu-iterative` 流程相同，但使用較大的網路，Learner 在 GPU 上訓練。GPU 記憶體不夠時會自動改用較小的 batch。只想用 CPU 的 Worker 可以加上 `--no-gpu`。

以上 preset 都是小規模設定，信賴區間很寬，只能確認流程正常，**不能用來判斷模型強弱**。

## 設定

設定來源的優先順序，由低到高：

1. 預設值
2. `<data-dir>/tableforge.toml`，或用 `--config <檔案>` 指定
3. 環境變數 `TABLEFORGE_<區段>__<名稱>`，例如 `TABLEFORGE_WORKER__LEASE_SECONDS=60`
4. 指令參數

```toml
[worker]
lease_seconds = 300
idle_seconds = 1
cache_max_bytes = 10737418240   # Worker artifact cache 上限
use_gpu = true

[coordinator]
listen = "0.0.0.0:7443"
reconcile_seconds = 2
metrics_listen = "127.0.0.1:9464"   # Prometheus /metrics，留空則不啟用
```

設定名稱或數值錯誤時，指令會直接報錯。

### PostgreSQL

預設使用 SQLite，適合單機或一個 Coordinator。要讓多個 Coordinator 程序共用資料，或資料量較大時，改用 PostgreSQL：

```
uv sync --extra learning --extra postgres
```

```toml
[metadata]
backend = "postgres"
url = "postgresql://tableforge:<密碼>@<主機>:5432/tableforge"
```

密碼也可以改用環境變數 `TABLEFORGE_METADATA__URL`，避免寫進檔案。第一次連線時會自動建立資料表。

把現有 SQLite 資料搬過去（目標資料庫必須是空的）：

```
uv run tableforge metadata copy-from-sqlite .tableforge/tableforge.db
```

Artifact 檔案仍在 `<data-dir>/artifacts`，換資料目錄時要一起複製。

開發時可用 Docker 啟動測試用 PostgreSQL，contract tests 會自動偵測並在 SQLite 與 PostgreSQL 各跑一次：

```
docker run -d --name tableforge-pg-test --memory 1g -p 127.0.0.1:55432:5432 -e POSTGRES_USER=tableforge -e POSTGRES_PASSWORD=tableforge -e POSTGRES_DB=tableforge postgres:17-alpine
```

其他位置可用 `TABLEFORGE_TEST_POSTGRES_URL` 指定；設定 `TABLEFORGE_DISABLE_POSTGRES_TESTS=1` 則略過。

### S3 相容 Object Storage

預設 artifact 存在 Coordinator 的 `<data-dir>/artifacts`，Worker 經由 Coordinator 上傳下載。改用 S3 相容 storage（AWS S3、MinIO、RustFS、Cloudflare R2 等）後，Worker 會直接連 storage，Coordinator 只負責簽發有時效的網址：

```
uv sync --extra learning --extra s3
```

```toml
[artifacts]
backend = "s3"
bucket = "tableforge"
endpoint_url = "http://127.0.0.1:9000"   # AWS S3 留空
access_key = "..."
secret_key = "..."                        # 建議改用 TABLEFORGE_ARTIFACTS__SECRET_KEY
presign_seconds = 900                     # 簽名網址有效時間，60–3600 秒
# public_endpoint_url = "http://<Worker 看得到的位址>:9000"  # Coordinator 與 Worker 看到的位址不同時
```

只有 Coordinator 需要金鑰；Worker 不需要任何 storage 設定。Bucket 不存在時會自動建立。

開發時可用 Docker 啟動測試用 storage（MinIO 已不提供社群版映像，這裡用 RustFS），相關測試會自動偵測：

```
docker run -d --name tableforge-s3-test --memory 512m -p 127.0.0.1:59000:9000 -e RUSTFS_ACCESS_KEY=tableforge -e RUSTFS_SECRET_KEY=tableforge-secret rustfs/rustfs:latest
```

其他位置可用 `TABLEFORGE_TEST_S3_ENDPOINT` 指定；設定 `TABLEFORGE_DISABLE_S3_TESTS=1` 則略過。

## 兩台電腦

在 Coordinator 電腦上：

```
uv run tableforge coordinator init --host <這台電腦的 IP 或主機名稱>
uv run tableforge coordinator serve --listen 0.0.0.0:7443
```

在另一個終端機產生一次性註冊碼，預設 60 分鐘內有效：

```
uv run tableforge coordinator enrollment-token
```

把 `.tableforge/tls/server.crt` 複製到 Worker 電腦，然後在 Worker 電腦上：

```
uv run tableforge node enroll --coordinator <IP>:7443 --token <註冊碼> --ca-file server.crt
uv run tableforge worker start --remote
```

`coordinator serve` 會在背景自動推進實驗，多台 Worker 可平行執行同一輪的 Actor Job。Worker 不需要資料庫，也不會讀寫 Coordinator 的檔案。兩台電腦必須能互相連線；不在同一個網路時，建議使用 [Tailscale](https://tailscale.com/) 之類的私有網路，不要把 port 直接開放到網際網路。

## 常用指令

| 指令 | 說明 |
|---|---|
| `doctor [--json]` | 偵測硬體並列出可執行的 workload |
| `experiment create/start/status/stop/list` | 管理實驗 |
| `worker start [--remote] [--max-jobs N] [--no-gpu]` | 持續領取並執行 Job，`Ctrl+C` 停止 |
| `worker run-once` | 只執行一個 Job |
| `job submit-simulation/list/status/cancel` | 直接操作 Job |
| `node init/status/enroll` | 節點註冊與狀態 |
| `node revoke <node-id>` | 在 Coordinator 上立即撤銷某個節點的所有 session |
| `node rotate-session` | 在 Worker 上換一個新的 session，舊的立即失效 |
| `checkpoint list/show` | 查看 NFSP checkpoint 與升級紀錄 |
| `league matrix <id> [--json]` | 查看 League 的 matchup matrix |
| `events tail [--after N]` | 查看狀態變更事件 |
| `metadata migrate-schema` | 建立或升級資料表，顯示 schema 版本 |
| `metadata copy-from-sqlite <db>` | 把 SQLite 資料搬到 PostgreSQL |
| `artifact gc [--dry-run] [--grace-hours H]` | 刪除沒有被引用的舊 artifact |
| `coordinator init/serve/enrollment-token` | 遠端 Coordinator；`serve --metrics-listen` 啟用 metrics |

## 開發

```
uv run pytest
uv run ruff check .
uv run mypy
```

修改 `proto/` 之後重新產生 gRPC 程式碼：

```
uv run python scripts/generate_protos.py
```

產生的程式碼放在 `src/tableforge/_generated/`，已 commit 進 repo。

## 已知限制

- 只支援 Heads-Up；6-Max、Deep CFR、PPO 尚未實作。
- GPU 只支援 NVIDIA CUDA；Apple Silicon 的 MPS 尚未支援。
- Windows 上 GPU 記憶體也會佔用系統記憶體配額，page file 太小時可用的 batch 會變小（`doctor` 會顯示警告）。
- 使用本機 artifact store 時，artifact 經由 Coordinator 傳送，單檔上限 1 GiB；使用 S3 則沒有這個限制。
- Docker 映像與 Compose 部署尚未提供。
- 完整清單見 [docs/status.md](docs/status.md)。
