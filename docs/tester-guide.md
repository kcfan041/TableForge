# TableForge 測試者指南（Windows + NVIDIA GPU）

本指南分兩部分：

- **測試者**：把自己的電腦加入叢集、提供 GPU、遠端建立並查看實驗。
- **管理者**（架設 Coordinator 的人）：開放連線並產生邀請碼。

兩台電腦不需要在同一個網路，透過 [Tailscale](https://tailscale.com/) 連線。

---

## 測試者

### 1. 事前準備

需要：

- Windows 10/11，64 位元
- NVIDIA 顯示卡與最新驅動程式（支援 CUDA 13）；沒有 GPU 也可以加入，只是只能跑 CPU 工作
- 約 10 GB 硬碟空間（PyTorch 與 CUDA 函式庫）

安裝以下軟體：

1. **Git**：<https://git-scm.com/download/win>
2. **uv**：在 PowerShell 執行
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   裝完後重新開啟 PowerShell。
3. **Tailscale**：<https://tailscale.com/download/windows>。登入後，接受管理者分享給你的機器，或加入管理者的 tailnet。在 PowerShell 執行 `tailscale status`，應該能看到管理者的電腦。

### 2. 取得 TableForge

你會收到 GitHub 的 repository 邀請，請先接受。

```powershell
git clone https://github.com/kcfan041/TableForge.git
cd TableForge
uv sync --extra learning --extra gpu --extra s3
```

第一次會下載約 3 GB 的套件，需要一些時間。

確認硬體偵測正確：

```powershell
uv run tableforge doctor
```

應該看到你的 GPU，例如 `cuda:0 NVIDIA GeForce RTX ...`，以及 `hu-nfsp-learner-gpu  SUPPORTED`。

### 3. 用邀請碼加入

管理者會傳給你一串 `tfinv1_` 開頭的邀請碼。它**只能使用一次**，預設 24 小時內有效，請不要轉給別人。

```powershell
uv run tableforge --data-dir .tableforge-node join tfinv1_...
```

成功時會顯示 `cluster_id`、`node_id`、使用的 storage 位址，以及下一步要執行的指令。

加入時會自動完成：

- 確認連到的是邀請碼指定的 Coordinator（比對憑證指紋與叢集 ID）；不符合就停止，不會送出任何資料。
- 取得這台電腦的節點憑證，存在 `.tableforge-node\coordinator.json`。
- 如果邀請碼包含操作權限，取得遠端操作憑證，存在 `%APPDATA%\TableForge\clusters\<cluster_id>\operator.json`。
- 逐一測試管理者提供的 storage 位址，選用第一個連得到的。

加入完成後，邀請碼就失效了，之後都使用上面兩個憑證檔。**請不要把這兩個檔案傳給別人。**

### 4. 檢查連線

```powershell
uv run tableforge --data-dir .tableforge-node doctor --coordinator
```

會依序檢查 dns、tcp、tls、cluster、session、storage，全部顯示 `OK` 就代表連線正常。任何一項失敗時會停在那一項，並提示可能的原因（見下方「常見問題」）。

### 5. 提供 GPU（啟動 Worker）

```powershell
uv run tableforge --data-dir .tableforge-node worker start --remote
```

這個視窗要保持開著，按 `Ctrl+C` 停止。Worker 會自動領取工作；GPU 訓練工作只會派給有 GPU 的電腦。

同一台電腦可以用同一個 `--data-dir` 開多個 Worker（例如開兩個視窗），每個 Worker 會有自己的編號。目前還不會自動把 Worker 綁定到不同 GPU，只有一張 GPU 時建議只開一個 GPU Worker。

### 6. 遠端建立與查看實驗

如果邀請碼包含 `operator` 權限：

```powershell
uv run tableforge remote whoami
uv run tableforge remote presets
uv run tableforge remote experiment create --preset hu-nfsp-gpu-iterative
uv run tableforge remote experiment start <experiment-id>
uv run tableforge remote experiment status <experiment-id>
uv run tableforge remote experiment list
uv run tableforge remote experiment stop <experiment-id>
uv run tableforge remote checkpoints
uv run tableforge remote matrix <experiment-id>
uv run tableforge remote nodes
```

`remote` 指令不需要 `--data-dir`，它使用 `%APPDATA%` 裡的操作憑證。只有 `viewer` 權限時只能查看，建立、啟動、停止會顯示 `ROLE_REQUIRED`。

推薦的 preset：

| preset | 說明 | 大約時間 |
|---|---|---|
| `hu-rule-smoke` | Rule Bot 對戰 1,000 手，確認流程 | 數秒 |
| `hu-nfsp-gpu-iterative` | NFSP 3 輪，Learner 用 GPU | 約 6 分鐘 |
| `hu-nfsp-cpu-league` | League 4 輪，最後產生 matchup matrix | 約 1 分鐘 |

目前模型仍在改進中，小型 preset 只能確認流程正常，不能用來判斷模型強弱。

### 7. 更新

```powershell
git pull
uv sync --extra learning --extra gpu --extra s3
```

更新後重新啟動 Worker 即可，不需要重新加入。

更新前可以先到 GitHub 的 Actions 頁面，確認最新一次的 CI 是綠勾。你和管理者的版本不相容時，Worker 和 `doctor --coordinator` 會直接提示需要更新哪一邊；只是版本不同但相容時，只會顯示警告。

### 8. Worker 會自己處理的事

- **斷線**：管理者的電腦重開機、Coordinator 重啟、Tailscale 暫時中斷時，Worker 不會結束，會每隔 1、2、4……最多 60 秒重試，恢復後自動繼續。log 只會各出現一次 `coordinator_unreachable` 和 `coordinator_reconnected`。Worker 也可以在 Coordinator 還沒啟動前先開，它會等到連得上為止。
- **斷線期間手上的工作**：如果斷線太久，這個工作會被交給別的 Worker，恢復後 log 會出現 `lease_lost`，Worker 直接繼續領下一個，不影響結果。
- **憑證續期**：節點憑證預設 7 天有效，剩下不到 1/4 時 Worker 會自動換新（log 出現 `session_renewed`），不需要重新加入。同一台電腦開多個 Worker 時只會換一次。
- **會停止的情況**：節點被管理者撤銷、或版本不相容時，Worker 會停止並說明要怎麼做，不會一直重試。

### 常見問題

| 狀況 | 原因與處理 |
|---|---|
| `join failed: cannot reach the coordinator` | Tailscale 沒有連線，或位址錯誤。執行 `tailscale status` 確認看得到管理者的電腦；請管理者確認 Coordinator 正在執行 |
| `certificate does not match the invite` | 管理者重新產生了憑證，或連到了別台電腦。請管理者產生新的邀請碼 |
| `invite was already used` / `invite expired` | 邀請碼只能用一次、有有效期限。請管理者產生新的邀請碼 |
| `already belongs to a cluster` | 這個 `--data-dir` 已經加入過叢集。換一個新的目錄名稱，或刪除舊目錄後重新加入 |
| `none of the coordinator's storage addresses are reachable` | 管理者提供的 storage 位址你連不到。把錯誤訊息傳給管理者 |
| doctor 的 `session` 失敗，或 Worker 顯示 `worker stopped: ... new invite` | 節點憑證被撤銷，或 Worker 超過 7 天沒有執行、來不及自動續期。請管理者產生新的邀請碼，用新的 `--data-dir` 重新加入 |
| doctor 的 `version` 失敗 | 你和 Coordinator 的版本不相容。依提示更新你這邊（`git pull` 後 `uv sync`），或請管理者更新 Coordinator |
| doctor 的 `storage` 失敗 | 可以連 Coordinator，但連不到 storage。把 doctor 的完整輸出傳給管理者 |
| `remote` 顯示 `UNAUTHENTICATED` | 操作憑證被撤銷或過期（預設 30 天）。請管理者產生新的邀請碼 |
| `remote` 顯示 `ROLE_REQUIRED` | 你的權限是 `viewer`，只能查看 |
| GPU 工作沒有被領取 | 執行 `uv run tableforge doctor`，確認 `hu-nfsp-learner-gpu` 為 `SUPPORTED`，並確認沒有用 `--no-gpu` 啟動 |

### 回報問題時請附上

1. `uv run tableforge --data-dir .tableforge-node doctor --coordinator --json` 的輸出
2. `uv run tableforge doctor --json` 的輸出
3. Worker 視窗中出錯前後的幾行 log
4. `git rev-parse --short HEAD` 的結果（你使用的版本）

請不要附上 `coordinator.json`、`operator.json` 或邀請碼。

---

## 管理者

以下假設 Coordinator 以 `docker compose` 執行（見 README「Docker」）。

### 1. 開放給 Tailscale

在 Coordinator 那台電腦上安裝並登入 Tailscale，查出它的 Tailscale IP：

```powershell
tailscale ip -4
```

在 `.env` 加入（`100.x.y.z` 換成上面的 IP）：

```text
BIND_ADDRESS=100.x.y.z
EXTRA_TLS_HOSTS=100.x.y.z
EXTRA_STORAGE_ENDPOINTS=tailnet=http://100.x.y.z:59000
```

然後重新啟動：

```powershell
docker compose up -d --wait
```

說明：

- `BIND_ADDRESS`：Coordinator 與 storage 只在這個 IP 上接受連線。不要用 `0.0.0.0`，那會開放給所有網路。
- `EXTRA_TLS_HOSTS`：憑證加入這個 IP。**修改後憑證會重新產生**，之前加入的節點必須用新的邀請碼重新加入。
- `EXTRA_STORAGE_ENDPOINTS`：提供給遠端節點的 storage 位址。

Windows 防火牆若擋住連線，允許 TCP 7443 與 59000 從 Tailscale 網路介面連入。

分享機器：在 Tailscale 後台把這台電腦分享給測試者，或邀請他加入 tailnet。建議用 ACL 限制測試者只能連 7443 與 59000。

### 2. 產生邀請碼

```powershell
docker compose exec coordinator tableforge --data-dir /data coordinator invite create --address 100.x.y.z:7443 --node --role operator --label tester
```

- `--node`：讓對方的電腦成為 Worker 節點
- `--role viewer` 或 `--role operator`：讓對方可以遠端查看，或同時可以建立、啟動、停止實驗
- 兩者可以只選一個，也可以都選
- `--ttl-hours`：有效時間，預設 24 小時
- 每個邀請碼只能使用一次，一次代表一台電腦

輸出中的 `invite` 欄位就是要傳給測試者的邀請碼，請用私訊傳送。

如果 `--address` 不在憑證內，指令會直接拒絕，並提示如何重新產生憑證。

### 3. 管理

```powershell
docker compose exec coordinator tableforge --data-dir /data coordinator invite list
docker compose exec coordinator tableforge --data-dir /data coordinator invite revoke <invite-id>
docker compose exec coordinator tableforge --data-dir /data coordinator operator list
docker compose exec coordinator tableforge --data-dir /data coordinator operator revoke <operator-id>
docker compose exec coordinator tableforge --data-dir /data node status
docker compose exec coordinator tableforge --data-dir /data node revoke <node-id>
```

撤銷節點後，它的 Worker 會立即無法領取工作；撤銷操作憑證後，`remote` 指令會立即失效。產生邀請碼與撤銷只能在 Coordinator 那台電腦上執行，不開放遠端。

### 已知限制

- 目前所有持有 `operator` 權限的人權限相同，可以停止彼此的實驗；使用者、專案與隔離屬於完整版（`docs/architecture/07-multi-party-and-trust.md` S1–S5）。
- 同一台電腦的多個 Worker 還不會自動分配到不同的 GPU。
- 透過 Tailscale 的兩台實體電腦驗收尚未完成；目前以本機的區域網路 IP 模擬遠端電腦驗證。
