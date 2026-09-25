# 多人共用與信任模型

本文件定義 TableForge 從「單一管理者」擴展到「多人共用」的範圍與路線。第一版與目前實作的信任前提見 `03-runtime-and-correctness.md` 第 9 節；federation 的技術演進見 `04-deployment-and-evolution.md` 第 10 節；多使用者控制面見同文件第 11 節。本文件把這些內容整理成一條可執行的路線，並確定下一階段的目標。

## 1. 決策

**下一階段目標為模式 B：信任的小團體共用一個叢集。**

- 一位管理者維運 Coordinator、PostgreSQL 與 object storage。
- 成員（例如朋友、實驗室）把自己的電腦加入成為 Worker，也可以建立與查看自己的實驗。
- 成員之間互相信任，不會蓄意送回假結果；但每個動作都必須能追溯到是哪位成員、哪台電腦做的，權限也要依角色限制。

模式 C（公開志願計算）與模式 D（完全去中心化）不在近期範圍內。第 5、6 節記錄它們與現有架構的差距，作為日後決策依據。

## 2. 現在的信任前提

目前實作的保護只針對兩種威脅：

1. **外人闖入**：TLS、一次性或限次的註冊碼、有期限且可撤銷的 session、短效簽名網址。
2. **資料在傳輸或儲存中損壞**：artifact 以 sha256 定址，上傳時由 storage 驗證 checksum，下載後由 Worker 再驗證。

它**不防已加入的節點作惡**：

- Worker 回傳的結果只驗證 hash 是否與內容一致，不驗證內容是否正確。
- 任何已註冊的 Worker 都可以下載任何 artifact（知道 artifact ID 即可）。
- 所有 Worker 權限相同，可以領取任何相容的 Job。
- CLI 操作（建立實驗、撤銷節點、GC）只要能存取 Coordinator 的資料目錄或資料庫即可，沒有使用者身分。

以上對「同一個管理者的多台電腦」（模式 A）是合理的。模式 B 必須補上身分、權限與稽核，但仍可沿用「成員不作惡」的前提。

## 3. 四種共用模式

| 模式 | 說明 | 例子 | 與目前架構的差距 |
|---|---|---|---|
| A. 自己的多台電腦 | 同一人的 Windows、Mac、雲端 GPU | 目前的兩節點目標 | 幾乎沒有，只差網路與憑證主機名稱 |
| **B. 信任的小團體** | 一人架 Coordinator，成員貢獻電腦並使用叢集 | 研究小組、朋友 | **小：需要使用者身分、權限、配額、稽核** |
| C. 公開志願計算 | 任何人都能安裝 Worker 貢獻算力 | BOINC、Folding@home | 大：必須假設有人作弊，需要結果驗證與信譽 |
| D. 完全去中心化 | 沒有中央 Coordinator，節點地位對等 | 點對點網路 | 非常大：需重新設計一致性模型 |

A、B、C 都建立在同一個基礎上：**單一 Coordinator、Worker 主動拉取 Job**。因此往 B、C 前進時，不需要推翻現有設計，只需在控制面加功能。D 則會取代這個基礎。

### 3.1 為什麼「Worker 主動拉取」是關鍵

Worker 永遠主動連出到 Coordinator 與 storage，Coordinator 從不連入 Worker。因此：

- 成員的電腦可以在任何網路、任何 NAT 後面，不需要公網 IP 或開 port。
- 只有 Coordinator 那一端需要讓成員連得到。
- 網路拓撲不影響正確性：lease、fencing、冪等完成都只依賴 Coordinator 的狀態。

## 4. 模式 B 的設計

### 4.1 網路

成員的 Worker 必須連得到兩個服務：

| 服務 | 預設 port | 說明 |
|---|---|---|
| Coordinator gRPC | 7443 | TLS 加密 |
| Object storage（S3） | 9000 | 使用 S3 時，Worker 直接上傳下載 artifact |

兩種做法：

1. **私有網路（建議，成員少於約 50 人時）**：以 Tailscale、Headscale 或 ZeroTier 建立私有網路，Coordinator 與 storage 只對這個網路開放。
   - 不需要公網 IP，也不對網際網路開放任何 port。
   - 可用存取規則限制成員只能連 Coordinator 與 storage，不能連彼此的電腦。
   - TLS 憑證需包含私有網路中的名稱或 IP。
2. **公開端點**：Coordinator 放在有網域的主機上，使用公開 CA 簽發的憑證（例如 Let's Encrypt）。storage 使用有公開端點的服務（Cloudflare R2、AWS S3），或同一主機上的 S3 服務。
   - 成員不需安裝任何網路軟體。
   - 管理者要負責主機安全、流量限制與費用。

不論哪一種，storage 位址都以既有的「具名 storage 位址」處理：Coordinator 設定 `artifacts.endpoints`，成員的 Worker 以 `artifacts.endpoint` 選擇自己連得到的位址。

### 4.2 身分

新增三種身分，彼此分開：

| 身分 | 代表 | 驗證方式 |
|---|---|---|
| **使用者（user）** | 一位成員 | 第一階段：管理者發給的個人 API token（只存 hash、可撤銷、有期限）；之後可接 OIDC |
| **節點（node）** | 一台電腦上的 Worker | 既有的註冊碼與 session，並記錄這台節點屬於哪位使用者 |
| **管理者（admin）** | 維運叢集的人 | 具 admin 角色的使用者 |

規則：

- 註冊碼由使用者建立，綁定建立者；以它註冊的節點屬於該使用者。
- 節點 session 只能執行 Worker 相關的 RPC（領取、續租、完成、失敗、artifact 傳輸），不能建立實驗或管理其他節點。
- 使用者 token 只能呼叫控制面 RPC（實驗、查詢、自己的節點管理），不能領取 Job。
- CLI 改為透過 Coordinator 的 gRPC 控制面操作，不再直接存取資料庫。只有管理者在 Coordinator 主機上的維運指令（schema 升級、資料搬移）例外。

### 4.3 專案與權限

新增 **project**（專案）作為實驗、checkpoint 與 artifact 的歸屬單位。每位使用者在每個專案有一個角色：

| 角色 | 權限 |
|---|---|
| viewer | 查看實驗、checkpoint、matchup matrix、metrics |
| member | viewer 的權限，加上建立、啟動、停止自己的實驗 |
| maintainer | member 的權限，加上停止任何人的實驗、管理專案成員 |
| admin（全域） | 所有專案；管理使用者、節點撤銷、GC、配額 |

節點貢獻的算力預設供所有專案使用。成員可將自己的節點限定只接某些專案的 Job（例如只跑自己的實驗）。Scheduler 在既有的相容性判斷之外，再加入「節點允許的專案」過濾，判斷位置不變。

### 4.4 配額與公平分配

小團體仍需要避免一個人的大型實驗佔滿所有 Worker：

- **每個專案的同時 Job 上限**，以及 GPU Job 上限。
- **排程順序**：同優先權的 Job 在專案之間輪流分配，而不是只依建立時間；實作放在既有的 claim 查詢中，仍維持單一 Coordinator 寫入。
- **儲存配額**：每個專案的 artifact 總量上限；超過時拒絕新的上傳並提示執行 GC。

### 4.5 稽核與追溯

既有的 outbox 事件與 attempt provenance 已記錄哪台節點執行了哪個 attempt。模式 B 另外記錄：

- 事件加上 `actor`（使用者或節點）欄位；控制面的每個變更都寫入事件。
- 每個 Job 結果可追溯到節點與節點擁有者。
- 撤銷節點或使用者時，可以列出它參與過的 Job，由管理者決定是否重跑。

### 4.6 artifact 存取範圍

目前任何已註冊 Worker 只要知道 artifact ID 就能下載。模式 B 改為：

- Worker 只能取得**目前持有 lease 的 Job** 所需的 artifact（Job 的輸入、payload 中引用的 checkpoint 與 snapshot）；其他下載請求拒絕。
- 使用者只能下載自己有權限的專案的 artifact。
- 簽名網址仍為短效；下載簽名時檢查上述權限。

這同時減少成員之間不必要的資料外流，例如某專案的模型權重。

### 4.7 不變的部分

以下契約在模式 B 中完全不變：

- JobSpec、lease、fencing、冪等完成。
- Artifact 以內容定址、不可變。
- Experiment revision 與 checkpoint 升級規則。
- Worker 主動拉取；Worker 只有 session，沒有資料庫或 storage 憑證。
- Worker 只執行 TableForge 內建的 executor，不從網路下載並執行程式碼。

## 5. 模式 C 的差距（記錄，不在近期範圍）

公開志願計算必須假設部分 Worker 會作弊、偷看資料或濫用資源。在模式 B 之上還需要：

1. **結果驗證**
   - Simulation 與評估具決定性，可以派給兩個不同擁有者的節點各執行一次並比對輸出 hash，不一致時隔離並加派第三次。規格已有「決定性違規時隔離」的設計（`03-runtime-and-correctness.md` 第 8 節）。
   - NFSP Learner 在 GPU 上沒有位元級決定性，無法以重算比對驗證。訓練 Job 只派給可信節點；不可信節點只執行 Actor 與評估。
2. **資料污染防護**：惡意 Actor 可能送出有偏差的對局資料。需要抽樣重算、統計異常偵測，以及依節點追蹤信譽並自動隔離。
3. **志願者端安全**：Worker 已經只執行內建程式碼，且 artifact 載入使用 safetensors 與 `allow_pickle=False` 的 NumPy，不會因載入檔案而執行任意程式碼。另外需要容器沙箱、資源上限，以及更新檔的簽章驗證。
4. **防濫用**：開放註冊但可封鎖、流量限制、簽名網址的下載次數與流量限制、公開憑證與正式網域。
5. **規模**：大量節點下的 heartbeat 頻率、claim 查詢、metrics 聚合需要重新調整。

主要取捨：「任何節點都能執行任何相容 Job」不再成立，Job 需依節點信任等級分派。

## 6. 模式 D 與 federation（記錄，不在近期範圍）

完全去中心化會移除目前正確性所依賴的單一權威：Job 只交給一個 Worker、實驗依 revision 推進、checkpoint 升級只在一處決定。這需要共識機制（在不互信的節點之間還需要拜占庭容錯），會使可重現性與「每個 Job 只完成一次」的保證變弱，工作量也遠大於目前整個專案。`04-deployment-and-evolution.md` 第 10 節已明確不建議以最終一致性處理全域 Job 所有權。

若目標是「不同團體互相交流」，較可行的是 **federation**：每個團體各自運行完整的 TableForge，只交換已完成且帶有來源資訊的 checkpoint 與對戰結果，例如把對方的模型加入自己的 League 對手池。每個實驗仍只有一個擁有者，既有的正確性保證不變。

## 7. 路線

| 階段 | 內容 | 前置條件 |
|---|---|---|
| **S0：兩台電腦** | 私有網路、TLS 主機名稱、具名 storage 位址；Mac 與 Windows 實機驗收 | 兩台電腦加入同一私有網路 |
| **S1：使用者與控制面 API** | 使用者與個人 token；CLI 改走 gRPC 控制面；節點綁定擁有者；節點 session 與使用者 token 權限分離 | S0 |
| **S2：專案與權限** | project、角色、節點允許的專案；事件加上 actor | S1 |
| **S3：配額與公平分配** | 專案 Job 與 GPU 上限、專案間輪流排程、儲存配額 | S2 |
| **S4：artifact 存取範圍** | Worker 只能取得持有 lease 的 Job 所需 artifact；使用者依專案權限下載 | S2 |
| **S5：成員加入流程** | 管理者邀請、成員一個指令加入節點、節點與使用者撤銷流程、操作文件 | S1–S4 |

每個階段沿用既有做法：先定義契約與 contract tests（SQLite 與 PostgreSQL 都要通過），再實作，最後以多程序實機驗收；並在同一個 commit 更新 `docs/status.md`。

模式 C 的工作（第 5 節）與 federation（第 6 節）在 S5 完成、且確定有需求後才重新評估。

## 8. 驗收重點

模式 B 完成時必須能示範：

1. 兩位成員各自以自己的電腦加入叢集，節點在 Coordinator 上顯示正確的擁有者。
2. 成員 A 無法停止成員 B 的實驗，也無法下載 B 專案的 artifact；maintainer 可以停止。
3. 節點 session 無法呼叫控制面 RPC；使用者 token 無法領取 Job。
4. 成員將節點限定只接自己的專案後，不會領到其他專案的 Job。
5. 一個專案達到 Job 上限後，其他專案的 Job 仍能被分配。
6. 撤銷一位成員後，其 token 與所有節點 session 立即失效，並能列出他的節點執行過的 Job。
7. 以上在 SQLite 與 PostgreSQL 上都通過 contract tests，並以至少兩台實體電腦完成實機驗收。
