# 進階：指令與部署

一般使用者只需要 `tableforge ui`，見 [使用指南](tester-guide.md)。本文件給需要腳本化、長期部署或開發的人。

`tableforge ui` 的底層就是以下指令：建立叢集等於 `coordinator init` + `coordinator admin` + 在背景執行 `coordinator serve` + 以一次性邀請碼 `join` 這台電腦；啟動 Worker 等於 `worker start --remote`。

## 單機執行（不需要叢集）

```
uv run tableforge doctor
uv run tableforge experiment create --preset hu-rule-smoke
uv run tableforge experiment start <experiment-id>
uv run tableforge worker start --max-jobs 10 --idle-seconds 0
uv run tableforge experiment status <experiment-id>
```

資料預設存在目前目錄的 `.tableforge/`，可用 `--data-dir <路徑>` 指定（放在子指令之前）。這和 `tableforge ui` 的資料位置無關。

NFSP 與 League：

```
uv run tableforge experiment create --preset hu-nfsp-cpu-iterative
uv run tableforge experiment start <experiment-id>
uv run tableforge worker start --max-jobs 100 --idle-seconds 0
uv run tableforge checkpoint list
uv run tableforge checkpoint show <checkpoint-id>

uv run tableforge experiment create --preset hu-nfsp-cpu-league
uv run tableforge experiment start <experiment-id>
uv run tableforge worker start --max-jobs 200 --idle-seconds 0
uv run tableforge league matrix <experiment-id>
```

- Worker 閒置時會自動推進實驗到下一階段。`checkpoint show` 顯示升級或拒絕的原因：只有 candidate 確定比目前 active 差（95% 信賴區間上限 < 0）時才拒絕。
- Matrix 每格是「列」對「欄」的 bb/100，`*` 表示 95% 信賴區間不含 0。
- `hu-nfsp-gpu-iterative` 使用較大的網路並在 GPU 上訓練，GPU 記憶體不夠時自動改用較小的 batch。只想用 CPU 的 Worker 加上 `--no-gpu`。
- 6 人桌 Rule Bot simulation：`6max-rule-smoke`（1,200 手）、`6max-rule-soak`（100 萬手）。

## 用指令建立與加入叢集

在建立者的電腦：

```
uv run tableforge coordinator init --host <其他電腦用來連線的 IP 或名稱>
uv run tableforge coordinator admin --label <你的名字>
uv run tableforge coordinator serve --listen <該 IP>:7443
uv run tableforge coordinator invite create --address <該 IP>:7443 --node --role operator --label <名稱>
```

在加入者的電腦：

```
uv run tableforge --data-dir .tableforge-node join tfinv1_...
uv run tableforge --data-dir .tableforge-node doctor --coordinator
uv run tableforge --data-dir .tableforge-node worker start --remote
uv run tableforge remote experiment create --preset hu-rule-smoke
```

- 邀請碼只能使用一次（一台電腦），預設 24 小時內有效，包含叢集 ID 與憑證指紋；加入時會確認沒有連錯。
- `--node` 讓對方成為 Worker；`--role viewer|operator|admin` 給遠端操作權限。`--role admin` 需要加 `--confirm-admin`，且 `--ttl-hours` 最多 1。
- 節點憑證存在 `--data-dir` 的 `coordinator.json`；操作憑證存在 `%APPDATA%\TableForge\clusters\<cluster_id>\operator.json`（Linux 為 `~/.config/tableforge/clusters/`）。`tableforge ui` 則都放在它自己的資料位置。
- `coordinator serve` 會在背景推進實驗，並在 `127.0.0.1:8080` 提供給只用瀏覽器的人的網頁；`--http-listen ""` 可以關閉。`tableforge ui` 建立的叢集則預設關閉這個網頁，由 UI 裡的開關控制。
- 舊的方式（`coordinator enrollment-token` 與 `node enroll --token ... --ca-file ...`）仍可使用。

遠端操作：

```
uv run tableforge remote whoami
uv run tableforge remote presets
uv run tableforge remote experiment list|create|start|stop|status
uv run tableforge remote checkpoints
uv run tableforge remote matrix <experiment-id>
uv run tableforge remote nodes
```

管理（在建立者的電腦）：

```
uv run tableforge coordinator invite list
uv run tableforge coordinator invite revoke <invite-id>
uv run tableforge coordinator operator list
uv run tableforge coordinator operator revoke <operator-id>
uv run tableforge node status
uv run tableforge node revoke <node-id>
```

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
http_listen = "127.0.0.1:8080"      # 瀏覽器網頁；空字串則關閉
http_certificate = ""               # 讓網頁直接使用 TLS（一般改用 tailscale serve）
http_private_key = ""
reconcile_seconds = 2
metrics_listen = "127.0.0.1:9464"   # Prometheus /metrics，留空則不啟用
```

設定名稱或數值錯誤時，指令會直接報錯。

### PostgreSQL

預設使用 SQLite。要讓多個 Coordinator 程序共用資料，或資料量較大時，改用 PostgreSQL：

```
uv sync --extra learning --extra postgres
```

```toml
[metadata]
backend = "postgres"
url = "postgresql://tableforge:<密碼>@<主機>:5432/tableforge"
```

密碼也可以改用環境變數 `TABLEFORGE_METADATA__URL`。第一次連線時會自動建立資料表。把現有 SQLite 資料搬過去（目標資料庫必須是空的）：

```
uv run tableforge metadata copy-from-sqlite .tableforge/tableforge.db
```

Artifact 檔案仍在 `<data-dir>/artifacts`，換資料目錄時要一起複製。

### S3 相容 Object Storage

預設 artifact 存在 Coordinator 的 `<data-dir>/artifacts`，Worker 經由 Coordinator 上傳下載。改用 S3 相容 storage（AWS S3、MinIO、RustFS、Cloudflare R2 等）後，Worker 直接連 storage，Coordinator 只簽發有時效的網址：

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
# public_endpoint_url = "http://<Worker 看得到的位址>:9000"
# endpoints = "host=http://127.0.0.1:59000"  # 額外的具名位址，Worker 以 endpoint = "host" 選用
```

只有 Coordinator 需要金鑰；Worker 不需要 storage 設定。Bucket 不存在時會自動建立。

## Docker

需要 Docker Desktop（或 Linux 上的 Docker Engine 與 Compose v2）。

### 完整部署（PostgreSQL + S3 storage + Coordinator + CPU Workers）

```
copy docker\.env.example .env      # Linux/macOS 用 cp；然後把所有密碼改掉
docker compose up -d --build --wait
```

- 預設啟動 2 個 CPU Worker，可在 `.env` 調整 `CPU_WORKERS`，或執行 `docker compose up -d --scale worker=4`。
- 服務預設只對本機開放：Coordinator `127.0.0.1:7443`、網頁 `127.0.0.1:8080`、metrics `127.0.0.1:9464`、storage `127.0.0.1:59000`。
- 管理者憑證：`docker compose exec coordinator tableforge --data-dir /data coordinator admin --label <你的名字>`。
- 操作實驗：`docker compose exec coordinator tableforge --data-dir /data experiment create --preset hu-nfsp-cpu-league`。
- 停止：`docker compose down`。加上 `-v` 會連資料一起刪除。

開放給 Tailscale 上的其他人：在 `.env` 加入（`100.x.y.z` 換成 `tailscale ip -4` 的結果）後重新 `docker compose up -d --wait`：

```text
BIND_ADDRESS=100.x.y.z
EXTRA_TLS_HOSTS=100.x.y.z
EXTRA_STORAGE_ENDPOINTS=tailnet=http://100.x.y.z:59000
```

- `BIND_ADDRESS`：Coordinator 與 storage 只在這個 IP 上接受連線。不要用 `0.0.0.0`。
- `EXTRA_TLS_HOSTS`：憑證加入這個 IP。**修改後憑證會重新產生**，之前加入的節點需要新的邀請碼。
- `EXTRA_STORAGE_ENDPOINTS`：提供給遠端節點的 storage 位址。
- Windows 防火牆允許 TCP 7443 與 59000 從 Tailscale 網路介面連入。網頁用 `tailscale serve --bg --https=443 http://127.0.0.1:8080` 分享。
- 產生邀請碼：`docker compose exec coordinator tableforge --data-dir /data coordinator invite create --address 100.x.y.z:7443 --node --role operator --label tester`。加入者一樣用 `tableforge ui` 貼上即可。

### 最小部署（SQLite，不需要資料庫與 storage）

```
docker compose -f compose.minimal.yaml up -d --build --wait
```

### 讓 Windows 主機上的 GPU 加入

Docker Desktop 的 Hyper-V 後端無法把 GPU 交給容器，GPU Worker 直接在主機上執行：

```
docker compose cp coordinator:/shared/. .tableforge-host\bundle
uv run tableforge --data-dir .tableforge-host\gpu node enroll --coordinator localhost:7443 --bundle .tableforge-host\bundle
```

在 `.tableforge-host\gpu\tableforge.toml` 寫入：

```toml
[artifacts]
endpoint = "host"
```

然後 `uv run tableforge --data-dir .tableforge-host\gpu worker start --remote`。`.tableforge-host\` 內含註冊碼，已被 git 忽略。

`Dockerfile` 另外提供 `worker-cuda` 映像，給支援容器 GPU 的 Linux 主機或 WSL2 後端使用（尚未實機驗證）：`docker build --target worker-cuda -t tableforge/worker-cuda .`，執行時加上 `--gpus all`。

## 指令一覽

| 指令 | 說明 |
|---|---|
| `ui [--home DIR] [--port N] [--no-gpu] [--no-browser]` | 本機介面：建立或加入叢集、Worker、叢集管理 |
| `doctor [--json] [--coordinator]` | 偵測硬體並列出可執行的 workload；`--coordinator` 另外逐層檢查到叢集的連線 |
| `join <邀請碼>` | 用邀請碼加入叢集 |
| `remote whoami/presets/nodes/checkpoints/matrix` | 用操作憑證遠端查看叢集 |
| `remote experiment list/create/start/stop/status` | 遠端管理實驗 |
| `coordinator init/admin/serve` | 建立憑證、產生管理者憑證、執行叢集服務 |
| `coordinator invite create/list/revoke` | 產生、列出、作廢邀請碼 |
| `coordinator operator list/revoke` | 列出、撤銷操作憑證 |
| `experiment create/start/status/stop/list` | 管理實驗（本機資料） |
| `worker start [--remote] [--max-jobs N] [--no-gpu]` | 持續領取並執行 Job，`Ctrl+C` 停止 |
| `worker run-once` | 只執行一個 Job |
| `job submit-simulation/list/status/cancel` | 直接操作 Job |
| `node init/status/enroll/revoke/rotate-session` | 節點註冊、狀態、撤銷與換 session |
| `checkpoint list/show` | 查看 NFSP checkpoint 與升級紀錄 |
| `league matrix <id> [--json]` | 查看 League 的 matchup matrix |
| `events tail [--after N]` | 查看狀態變更事件 |
| `metadata migrate-schema/copy-from-sqlite` | 資料表升級、SQLite 搬到 PostgreSQL |
| `artifact gc [--dry-run] [--grace-hours H]` | 刪除沒有被引用的舊 artifact |
