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

## 本機模型庫

訓練好的模型放在 TableForge 資料位置的 `models\`（Windows 為 `%LOCALAPPDATA%\TableForge\models`），不進 repo。每個模型一個資料夾：`checkpoint.zip` 與 `model.json`（演算法、遊戲、訓練設定、評估結果、sha256）；讀取時會檢查 sha256。

```
uv run tableforge models list
uv run tableforge models add <模型 ID> <checkpoint.zip> --game 6max-pko-10bb --info info.json
uv run tableforge models remove <模型 ID>
```

`--info` 是含 `training` 與 `evaluation` 兩個物件的 JSON（選填）。`tableforge ui` 的重播、策略表與遠端對打會從這裡選模型。

ICM Push/Fold Nash baseline 用到的起手牌全下勝率表放在 `src/tableforge/poker/data/preflop_equity.json`，可以重新產生（約 8 分鐘，16 個程序）：

```
uv run python scripts/preflop_equity.py --samples 100000 --workers 16
```

程式內直接求解一個情境：`tableforge.learning.pushfold_nash.solve(PushFoldGame(stacks, sb, bb, ante, prizes, heads=bounties))`，座位以按鈕為 0；結果的 `gap` 是每個人單獨改打法最多還能多拿的錢（buy-in）。

量模型的可剝削程度（S4）也可以用研究腳本在本機跑：

```
uv run python scripts/tournament_probe.py --exploit-target pko-10bb-nfsp-c1 --init-model pko-10bb-nfsp-c1 --tournaments 40000 --checkpoints 4 --pool-eval-seeds 200 --reward-scale 0.3 --double-q --hand-horizon --rl-capacity 200000 --sl-capacity 1000000
```

每個檢查點印出 `exploit.best_response.roi`：學習者最佳回應對「其他座位全部是目標」的 ROI。`--init-model` 讓學習者從目標開始（建議，平台的 exploit preset 也這樣做）；`--exploit-target rule:<bot>` 則以 Rule Bot 為目標。

新格式（v3 輸入、v2 下注尺度、多結構）的研究腳本：

```
uv run python scripts/tournament_probe.py --v3 --sampler "{\"players\":[2,3,4,5,6,7,8,9]}" --stack-bb 25 --tournaments 20000 --checkpoints 2 --greedy --reward-scale 0.3 --double-q --hand-horizon --rl-capacity 200000 --sl-capacity 1000000 --self-play 0.75
```

PPO（A1）的研究腳本（同樣的錦標賽、對手與 benchmark）：

```
uv run python scripts/tournament_probe.py --algorithm ppo --stack-bb 10 --tournaments 60000 --checkpoints 4 --hidden 128 --batch 1024 --lr 0.0003 --reward-scale 0.3 --hand-horizon --self-play 0.75 --greedy --ppo-tournaments 250 --ppo-epochs 4
```

`--ppo-tournaments` 是每次更新前打幾場（on-policy），`--ppo-epochs` 是每批經驗訓練幾遍，`--batch` 是 minibatch 大小；`--entropy`、`--gae-lambda` 調整熵獎勵與 GAE λ。MMD 正則化用 `--magnet-coef 0.05 --magnet-every 10`（拉回參考策略的強度，參考策略每幾次更新換成目前策略；0 就是一般 PPO）。有 `--save` 時每個檢查點都另存一份（`<名稱>-<場數>.zip`）。`benchmark` 是依機率出手（預設打法），`greedy` 是永遠選最可能的動作。也可以加 `--v3 --sampler ...`。平台上用 preset `6max-pko-10bb-ppo-train`（或加 MMD 的 `6max-pko-10bb-mmd-train`），設定 `entropy_coef`、`gae_lambda`、`clip`、`magnet_coef`、`magnet_every`；`train_steps_per_round` 是每輪 epoch 數。

`--sampler` 是 `TournamentSampler` 的 JSON（`players`、`starting_stack_bb`、`hands_per_level`、`blind_growth`、`ante_share`、`payouts`、`pko_share`、`bounty_share`、`cash_share`），只用在訓練；benchmark 仍是 `--stack-bb`／`--tournament` 指定的錦標賽。平台上建立實驗時用 `settings.sampler` 傳同樣的物件。

從叢集下載、用於本機對打的模型另外放在 `downloaded-models\`，格式相同。下載透過控制面的 `download_model` 指令（需要 `model:download`），每塊 2 MB，完成後檢查大小與 sha256。對打的指令（`play_start`、`play_act` 等，需要 `play:remote`）也可以用 `/api/command/<指令>` 或 gRPC `Command` 呼叫；格式見 `src/tableforge/coordinator/play.py`。

## 研究用：參數掃描

`scripts/sweep.py` 在背景同時跑多組訓練設定與 seed，用於調參；每組是一個 `scripts/nfsp_probe.py`（HU 現金桌）或 `scripts/tournament_probe.py`（錦標賽）程序。

```
uv run python scripts/sweep.py start runs/p1b sweep.json --seeds 1,2,3 --size 1000000
uv run python scripts/sweep.py start runs/t3 t3.json --probe tournament --size 60000 --evaluation 1000
uv run python scripts/sweep.py status runs/p1b
uv run python scripts/sweep.py summary runs/p1b
uv run python scripts/sweep.py stop runs/p1b
```

- `sweep.json` 是「名稱 → 額外的 probe 參數」，例如 `{"bigbuf": ["--reward-scale", "100", "--rl-capacity", "500000"]}`；參數中的 `{tag}` 會換成該次執行的路徑（例如 `--save {tag}.zip`）。
- 每個程序單執行緒、不開視窗、以「低於一般」優先權執行。
- 有多個 CCD 的 CPU（例如 Ryzen 9 9950X3D）上，程序會輪流綁到各 CCD，避免熱集中在同一個 CCD。
- 預設最多同時 16 個程序（`--max-runs`）。

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
| `models list/add/remove [--home DIR]` | 本機模型庫：列出、匯入、刪除訓練好的模型 |
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
