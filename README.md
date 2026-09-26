# TableForge

[![CI](https://github.com/kcfan041/TableForge/actions/workflows/ci.yml/badge.svg)](https://github.com/kcfan041/TableForge/actions/workflows/ci.yml)

分散式多代理德州撲克 AI 訓練與評估框架。目前支援 No-Limit Hold'em（Heads-Up 與 2–6 人桌）、Rule Bots、NFSP（Heads-Up；CPU 與 NVIDIA GPU），以及單一 Coordinator 搭配多個 Worker 的遠端執行。

- 開發進度：[docs/status.md](docs/status.md)
- 架構規格：[docs/architecture/README.md](docs/architecture/README.md)
- 長期願景：[docs/vision.md](docs/vision.md)
- **要加入別人的叢集或邀請別人加入：[docs/tester-guide.md](docs/tester-guide.md)**

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

6 人桌的 Rule Bot simulation 改用 `--preset 6max-rule-smoke`（1,200 手）或 `6max-rule-soak`（100 萬手）。

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
# endpoints = "host=http://127.0.0.1:59000"  # 額外的具名位址，Worker 以 endpoint = "host" 選用
```

只有 Coordinator 需要金鑰；Worker 不需要任何 storage 設定。Bucket 不存在時會自動建立。

開發時可用 Docker 啟動測試用 storage（MinIO 已不提供社群版映像，這裡用 RustFS），相關測試會自動偵測：

```
docker run -d --name tableforge-s3-test --memory 512m -p 127.0.0.1:59000:9000 -e RUSTFS_ACCESS_KEY=tableforge -e RUSTFS_SECRET_KEY=tableforge-secret rustfs/rustfs:latest
```

其他位置可用 `TABLEFORGE_TEST_S3_ENDPOINT` 指定；設定 `TABLEFORGE_DISABLE_S3_TESTS=1` 則略過。

## 兩台以上的電腦

建議用邀請碼加入，完整步驟（包含 Tailscale 設定）見 [docs/tester-guide.md](docs/tester-guide.md)。簡要流程：

在 Coordinator 電腦上：

```
uv run tableforge coordinator init --host <其他電腦用來連線的 IP 或名稱>
uv run tableforge coordinator serve --listen <該 IP>:7443
uv run tableforge coordinator invite create --address <該 IP>:7443 --node --role operator --label <名稱>
```

把輸出中的 `tfinv1_...` 邀請碼私下傳給對方。在對方電腦上：

```
uv run tableforge --data-dir .tableforge-node join tfinv1_...
uv run tableforge --data-dir .tableforge-node doctor --coordinator
uv run tableforge --data-dir .tableforge-node worker start --remote
uv run tableforge remote experiment create --preset hu-rule-smoke
```

- 邀請碼只能使用一次（一台電腦），預設 24 小時內有效；包含叢集 ID 與憑證指紋，加入時會確認沒有連錯 Coordinator，不需要另外傳憑證檔。
- `--node` 讓對方成為 Worker；`--role viewer` 可遠端查看；`--role operator` 另外可以建立、啟動、停止實驗。節點憑證與操作憑證分開存放，互相不能代用。
- 產生邀請碼、撤銷節點與操作憑證只能在 Coordinator 電腦上執行。
- 同一台電腦可以用同一個 `--data-dir` 開多個 Worker，各自有 Worker 編號。

`coordinator serve` 會在背景自動推進實驗，多台 Worker 可平行執行同一輪的 Actor Job。Worker 不需要資料庫，也不會讀寫 Coordinator 的檔案。Worker 只需要能連到 Coordinator 與 storage；不在同一個網路時，建議使用 [Tailscale](https://tailscale.com/) 之類的私有網路，不要把 port 直接開放到網際網路。

舊的方式（`coordinator enrollment-token` 與 `node enroll --token ... --ca-file ...`）仍可使用。

## Docker

需要 Docker Desktop（或 Linux 上的 Docker Engine 與 Compose v2）。

### 完整部署（PostgreSQL + S3 storage + Coordinator + CPU Workers）

```
copy docker\.env.example .env      # Linux/macOS 用 cp；然後把所有密碼改掉
docker compose up -d --build --wait
```

- 預設啟動 2 個 CPU Worker，可在 `.env` 調整 `CPU_WORKERS`，或執行 `docker compose up -d --scale worker=4`。
- Worker 啟動時會自動向 Coordinator 註冊，不需要手動操作。
- 服務預設只對本機開放：Coordinator `127.0.0.1:7443`、metrics `127.0.0.1:9464`、storage `127.0.0.1:59000`。要讓其他電腦加入時，在 `.env` 設定 `BIND_ADDRESS`、`EXTRA_TLS_HOSTS`、`EXTRA_STORAGE_ENDPOINTS`，見 [docs/tester-guide.md](docs/tester-guide.md)。

操作實驗時在 Coordinator 容器裡執行指令：

```
docker compose exec coordinator tableforge --data-dir /data experiment create --preset hu-nfsp-cpu-league
docker compose exec coordinator tableforge --data-dir /data experiment start <experiment-id>
docker compose exec coordinator tableforge --data-dir /data experiment status <experiment-id>
```

停止：`docker compose down`。加上 `-v` 會連資料一起刪除。

### 最小部署（SQLite，不需要資料庫與 storage）

```
docker compose -f compose.minimal.yaml up -d --build --wait
```

### 讓 Windows 主機上的 GPU 加入

這台電腦的 Docker Desktop 無法把 GPU 交給容器，所以 GPU Worker 直接在主機上執行，連到 Compose 裡的 Coordinator：

```
docker compose cp coordinator:/shared/. .tableforge-host\bundle
uv run tableforge --data-dir .tableforge-host\gpu node enroll --coordinator localhost:7443 --bundle .tableforge-host\bundle
```

在 `.tableforge-host\gpu\tableforge.toml` 寫入：

```toml
[artifacts]
endpoint = "host"
```

然後啟動：

```
uv run tableforge --data-dir .tableforge-host\gpu worker start --remote
```

`endpoint = "host"` 讓 Coordinator 簽發主機連得到的 storage 位址（`127.0.0.1:59000`）；容器內的 Worker 則使用 `storage:9000`。`.tableforge-host\` 內含註冊碼，已被 git 忽略。

`Dockerfile` 另外提供 `worker-cuda` 映像，給支援容器 GPU 的 Linux 主機或 WSL2 後端使用：`docker build --target worker-cuda -t tableforge/worker-cuda .`，執行時加上 `--gpus all`。這台電腦尚未驗證。

## 常用指令

| 指令 | 說明 |
|---|---|
| `doctor [--json] [--coordinator]` | 偵測硬體並列出可執行的 workload；`--coordinator` 另外逐層檢查到叢集的連線 |
| `join <邀請碼>` | 用邀請碼加入叢集 |
| `remote whoami/presets/nodes/checkpoints/matrix` | 用操作憑證遠端查看叢集 |
| `remote experiment list/create/start/stop/status` | 遠端管理實驗（建立、啟動、停止需要 operator 權限） |
| `coordinator invite create/list/revoke` | 產生、列出、作廢邀請碼（只能在 Coordinator 電腦上執行） |
| `coordinator operator list/revoke` | 列出、撤銷遠端操作憑證（只能在 Coordinator 電腦上執行） |
| `experiment create/start/status/stop/list` | 管理實驗 |
| `worker start [--remote] [--max-jobs N] [--no-gpu]` | 持續領取並執行 Job，`Ctrl+C` 停止 |
| `worker run-once` | 只執行一個 Job |
| `job submit-simulation/list/status/cancel` | 直接操作 Job |
| `node init/status/enroll` | 節點註冊與狀態；`enroll` 可用 `--token`、`--token-file` 或 `--bundle` |
| `node revoke <node-id>` | 在 Coordinator 上立即撤銷某個節點的所有 session |
| `node rotate-session` | 在 Worker 上換一個新的 session，舊的立即失效 |
| `checkpoint list/show` | 查看 NFSP checkpoint 與升級紀錄 |
| `league matrix <id> [--json]` | 查看 League 的 matchup matrix |
| `events tail [--after N]` | 查看狀態變更事件 |
| `metadata migrate-schema` | 建立或升級資料表，顯示 schema 版本 |
| `metadata copy-from-sqlite <db>` | 把 SQLite 資料搬到 PostgreSQL |
| `artifact gc [--dry-run] [--grace-hours H]` | 刪除沒有被引用的舊 artifact |
| `coordinator init/serve/enrollment-token` | 遠端 Coordinator；`init --host <名稱> [--regenerate]` 讓憑證包含其他位址；`serve --metrics-listen` 啟用 metrics；`enrollment-token --max-uses N --output <檔案>` 產生可多次使用的註冊碼 |

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

每次 push 到 `main` 或開 pull request，GitHub Actions 會自動執行（`.github/workflows/ci.yml`）：

- Ruff、mypy，並確認產生的 gRPC 程式碼與 `proto/` 一致
- Linux：完整測試，搭配 PostgreSQL 17 與 RustFS 服務容器
- Windows：CPU 測試
- 建置 Coordinator 與 CPU Worker 的 Docker 映像

CI 沒有 GPU，GPU 測試會自動略過，需要在有 NVIDIA GPU 的電腦上執行 `uv run pytest`。

### 版本相容

Coordinator 和節點之間有「協定版本」（`src/tableforge/version.py`）。只有不相容的協定修改才提高 `PROTOCOL_VERSION`；Coordinator 不再接受舊節點時提高 `MIN_PROTOCOL_VERSION`。只是新增 protobuf 欄位不需要提高。版本不相容時，`join`、`worker start`、`doctor --coordinator` 會停止並提示更新；只是程式版本不同時則顯示警告。

## 已知限制

- 只支援 Heads-Up；6-Max、Deep CFR、PPO 尚未實作。
- GPU 只支援 NVIDIA CUDA；Apple Silicon 的 MPS 尚未支援。
- Windows 上 GPU 記憶體也會佔用系統記憶體配額，page file 太小時可用的 batch 會變小（`doctor` 會顯示警告）。
- 使用本機 artifact store 時，artifact 經由 Coordinator 傳送，單檔上限 1 GiB；使用 S3 則沒有這個限制。
- CUDA Worker 容器尚未在實機驗證；Windows 上請在主機執行 GPU Worker。
- 完整清單見 [docs/status.md](docs/status.md)。
