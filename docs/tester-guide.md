# TableForge 使用指南

TableForge 只有一個入口：

```powershell
uv run tableforge ui
```

它會在瀏覽器開啟 `http://127.0.0.1:8765`（只有這台電腦連得到）。第一次開啟時選：

- **建立新的叢集**：你是第一個人。這台電腦負責協調工作，也可以同時提供運算。
- **加入現有叢集**：別人已經建立叢集，並給你一個邀請碼（`tfinv1_` 開頭）。

之後的所有操作（提供運算、邀請別人、建立實驗、看結果）都在同一個頁面完成。

兩台電腦不需要在同一個網路，透過 [Tailscale](https://tailscale.com/) 連線。指令版本的做法見 [進階：指令與部署](advanced.md)。

---

## 1. 安裝（每台電腦都一樣）

需要：

- Windows 10/11（64 位元）、macOS 或 Linux
- 要提供 GPU 運算時：NVIDIA 顯示卡與最新驅動程式（支援 CUDA 13）。沒有 GPU 也可以加入，只是只能跑 CPU 工作
- 約 10 GB 硬碟空間（PyTorch 與 CUDA 函式庫）

安裝以下軟體：

1. **Git**：<https://git-scm.com/download/win>
2. **uv**：在 PowerShell 執行
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   裝完後重新開啟 PowerShell。
3. **Tailscale**：<https://tailscale.com/download/windows>，安裝後登入。所有人需要在同一個 tailnet，或由建立者把自己的電腦分享給其他人。

取得 TableForge（你會先收到 GitHub repository 的邀請，請接受）：

```powershell
git clone https://github.com/kcfan041/TableForge.git
cd TableForge
uv sync --extra learning --extra gpu
```

第一次會下載約 3 GB 的套件，需要一些時間。沒有 NVIDIA GPU 的電腦可以省略 `--extra gpu`。

## 2. 建立叢集（第一個人）

```powershell
uv run tableforge ui
```

1. 選「建立新的叢集」。
2. 選誰可以連進來：
   - **Tailscale 上的其他人（建議）**：TableForge 會自動偵測這台電腦的 Tailscale 位址。沒有偵測到時，先確認 Tailscale 已登入，再重新整理頁面。
   - **只有這台電腦**：自己試用。之後要讓別人加入需要重新建立。
3. 按「建立」。TableForge 會自動產生憑證、把你設為管理者、在背景啟動叢集服務，並讓這台電腦也能提供運算。

完成後會進入「這台電腦」頁：

- **叢集服務**：顯示是否執行中。關掉 TableForge 頁面或 PowerShell 視窗後，叢集服務仍會在背景執行，其他人的 Worker 不受影響；下次開啟 `tableforge ui` 時會自動接上，如果它已停止（例如重開機）會自動重新啟動。
- **Worker**：按「啟動 Worker」讓這台電腦也提供運算。
- 側邊欄的其他頁面是叢集管理：總覽、節點、實驗、Checkpoint、事件、邀請與憑證、診斷。

Windows 防火牆若詢問，允許 TableForge（Python）在 Tailscale 網路上接受連線（TCP 7443）。

## 3. 邀請別人

在「邀請與憑證」頁：

1. 勾選「讓對方的電腦提供運算（Worker）」—— 對方要提供 GPU 或 CPU 時。
2. 選「叢集管理權限」：
   - **不給**：只提供運算。
   - **檢視者**：可以查看節點、實驗與結果。
   - **操作者**：另外可以建立、開始、停止實驗。
   - **管理者**：和你一樣可以管理整個叢集。需要勾選確認，最多 1 小時有效，使用時會記錄在「事件」頁。
3. 填備註（例如對方名字），按「產生」，把邀請碼私下傳給對方。

邀請碼只能使用一次，預設 24 小時內有效。一個邀請碼可以同時給「提供運算」和管理權限。連線位址會自動帶入，通常不需要修改。

## 4. 加入叢集（其他人）

```powershell
uv run tableforge ui
```

1. 選「加入現有叢集」，貼上邀請碼，按「加入」。
2. 在「這台電腦」頁按「開始檢查」，確認連線的每一項都是「成功」。
3. 按「啟動 Worker」。下方會顯示 Worker 的狀態、完成的工作數量與紀錄；按「停止」會讓 Worker 做完目前的步驟後結束。

邀請碼帶有管理權限時，側邊欄會多出叢集管理的頁面（依權限顯示）。

注意：

- PowerShell 視窗要保持開著；按 `Ctrl+C` 關閉 TableForge 時，由它啟動的 Worker 也會一起停止。之後每次要提供運算，再執行同一行指令、按「啟動 Worker」即可，不需要重新加入。
- 不想用 GPU 時，改用 `uv run tableforge ui --no-gpu`。
- 同一台電腦可以啟動多個 Worker。目前還不會自動把 Worker 分配到不同 GPU，只有一張 GPU 時建議只開一個。

## 5. 建立與查看實驗

有操作者（或管理者）權限時，在「實驗」頁選擇 preset，按「建立並開始」。實驗明細會顯示 Job 狀態、模擬結果（各座位輸贏）、評估結果，League 實驗還有對戰矩陣熱圖。頁面會自動更新。

推薦的 preset：

| preset | 說明 | 大約時間 |
|---|---|---|
| `hu-rule-smoke` | Rule Bot 對戰 1,000 手，確認流程 | 數秒 |
| `6max-rule-smoke` | 6 人桌 Rule Bot 對戰 1,200 手 | 數秒 |
| `6max-sng-10bb` | 6 人錦標賽 benchmark，起始 10 BB，獎金 65/35；3,000 場 | 約 30 秒 |
| `6max-sng-10bb-top3` | 同上，獎金 50/30/20 | 約 30 秒 |
| `hu-nfsp-gpu-iterative` | NFSP 3 輪，Learner 用 GPU | 約 6 分鐘 |
| `hu-nfsp-cpu-league` | League 4 輪，最後產生對戰矩陣 | 約 1 分鐘 |
| `hu-nfsp-v2-train` | NFSP 訓練 10 輪，每輪對 4 種 Rule Bot 測一次並畫出訓練曲線 | 數十分鐘（依 Worker 數量） |

目前模型仍在改進中，小型 preset 只能確認流程正常，不能用來判斷模型強弱。

**調整訓練設定**：選 NFSP 多輪訓練的 preset（例如 `hu-nfsp-v2-train`）時，可以勾選「自訂訓練參數」修改學習率、輪數、隱藏層大小等；改過的欄位會標示出來。「用多個 seed 各建立一個」填入例如 `1, 2, 3`，會以相同設定建立 3 個實驗，只差隨機種子。訓練結果受 seed 影響很大，比較兩組設定時請各用幾個 seed。實驗明細的「訓練曲線」會畫出每一輪對 4 種 Rule Bot 的 bb/100（色帶是 95% 信賴區間，0 線以上代表贏），同一組的其他 seed 以虛線並排。

錦標賽 benchmark 的結果以 buy-in 為單位：ROI、Total EV（總盈虧）、Prize EV（名次獎金）、Bounty EV（賞金，PKO 才有）、ITM（拿到獎金的比例）、平均名次與各名次比例，都附 95% 信賴區間。另有 25 BB、50 BB 版本（`6max-sng-25bb`、`6max-sng-50bb` 與各自的 `-top3`）。

## 6. 給只用瀏覽器的人（選用）

一般不需要。只有對方不想安裝 TableForge、只想用瀏覽器看結果時：

1. 建立者在「這台電腦」→「叢集服務」→「進階：給只用瀏覽器的人」按「開啟瀏覽器網頁」。
2. 依畫面上的指令執行 `tailscale serve`，以 HTTPS 分享給 tailnet 上的人。
3. 對方開啟 `https://<建立者電腦的 Tailscale 名稱>`，貼上一個**只含檢視者或操作者權限**（不含 Worker）的邀請碼登入。

## 7. 更新

```powershell
git pull
uv sync --extra learning --extra gpu
```

更新後重新執行 `uv run tableforge ui`，不需要重新加入。建立者更新時，先在「叢集服務」按「停止叢集服務」，更新後再開啟 TableForge，服務會自動用新版本重新啟動。

你和建立者的版本不相容時，連線檢查與 Worker 會直接提示需要更新哪一邊；只是版本不同但相容時，只會顯示警告。

## 8. Worker 會自己處理的事

- **斷線**：建立者的電腦重開機、叢集服務重啟、Tailscale 暫時中斷時，Worker 不會結束，會每隔 1、2、4……最多 60 秒重試，恢復後自動繼續。
- **斷線期間手上的工作**：斷線太久時這個工作會交給別的 Worker，恢復後 Worker 直接繼續領下一個，不影響結果。
- **憑證續期**：這台電腦的憑證預設 7 天有效，剩下不到 1/4 時 Worker 會自動換新，不需要重新加入。
- **會停止的情況**：這台電腦被管理者撤銷、或版本不相容時，Worker 會停止並說明要怎麼做，不會一直重試。

## 常見問題

| 狀況 | 原因與處理 |
|---|---|
| 建立叢集時沒有偵測到 Tailscale | Tailscale 沒有安裝、沒有登入或沒有連線。確認系統匣的 Tailscale 圖示是已連線，再重新整理頁面 |
| 加入時顯示 `cannot reach the coordinator` | Tailscale 沒有連線、建立者的叢集服務沒有執行，或建立者的電腦沒有分享給你。執行 `tailscale status` 確認看得到建立者的電腦，並請建立者確認「叢集服務」是執行中 |
| `certificate does not match the invite` | 建立者重新建立了叢集，或連到了別台電腦。請建立者產生新的邀請碼 |
| `invite was already used` / `invite expired` | 邀請碼只能用一次、有有效期限。請建立者產生新的邀請碼 |
| 連線檢查的 `session` 失敗，或 Worker 顯示需要新的邀請碼 | 這台電腦被撤銷，或超過 7 天沒有啟動 Worker、來不及自動續期。請建立者產生新的邀請碼，並用新的資料位置重新加入（`uv run tableforge ui --home <新的資料夾>`） |
| 連線檢查的 `version` 失敗 | 版本不相容。依提示更新你這邊，或請建立者更新 |
| 連線檢查的 `storage` 失敗 | 連得到叢集，但無法傳送資料。把連線檢查的畫面截圖給建立者 |
| 叢集管理頁面顯示權限不足 | 你的權限只能查看，或已被撤銷 |
| 頁面顯示「無法讀取狀態」或按鈕沒反應 | TableForge 已經關閉（PowerShell 視窗被關掉）。重新執行 `uv run tableforge ui` 後重新整理頁面 |
| Worker 顯示「異常結束」 | 看「最後的問題」與下方紀錄；通常和連線檢查的項目對應 |
| 叢集服務顯示「已停止」且無法啟動 | 畫面上的錯誤會附上服務紀錄最後幾行；常見原因是 7443 port 被其他程式佔用 |
| GPU 工作沒有被領取 | 執行 `uv run tableforge doctor`，確認 `hu-nfsp-learner-gpu` 為 `SUPPORTED`，並確認沒有用 `--no-gpu` 啟動 |

## 回報問題時請附上

1. TableForge 頁面的截圖（「這台電腦」頁，包含連線檢查結果與 Worker 紀錄）
2. `uv run tableforge doctor --json` 的輸出
3. `git rev-parse --short HEAD` 的結果（你使用的版本）

請不要附上邀請碼，也不要傳送資料夾裡的 `coordinator.json`、`operator.json`、`admin` 相關檔案。

## 資料放在哪裡

預設放在每個使用者的應用程式資料夾，不在 TableForge 的原始碼目錄裡：

- Windows：`%LOCALAPPDATA%\TableForge`
- macOS：`~/Library/Application Support/TableForge`
- Linux：`~/.local/share/tableforge`

可用 `uv run tableforge ui --home <資料夾>` 或環境變數 `TABLEFORGE_HOME` 改到其他位置。同一台電腦要加入第二個叢集時，用不同的 `--home`。

## 已知限制

- 目前所有持有操作者權限的人權限相同，可以停止彼此的實驗；使用者、專案與隔離屬於完整版（`docs/architecture/07-multi-party-and-trust.md` S1–S5）。
- 同一台電腦的多個 Worker 還不會自動分配到不同的 GPU。
- 透過 Tailscale 的兩台實體電腦驗收尚未完成；目前在一台電腦上以多個 TableForge 驗證建立、加入與管理流程。
