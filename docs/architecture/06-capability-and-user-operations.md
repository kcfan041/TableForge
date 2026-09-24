# 節點能力、工作負載相容與使用者操作

## 1. 目標與非目標

本文件定義 TableForge 如何偵測使用者電腦能力、判斷可執行工作、處理資源不足，以及提供第一版 CLI 操作流程。此能力建立在既有 Worker pull model、Job `ResourceRequest`、Scheduler 與 immutable JobSpec 上，不修改 Job lifecycle。

目標：

- 自動偵測 CPU、RAM、GPU、VRAM、CUDA runtime、磁碟與容器能力。
- 將硬體事實、工作負載需求與排程決策分離。
- 在 Job claim 前判定 `SUPPORTED`、`DEGRADED` 或 `UNSUPPORTED`。
- 允許沒有 GPU 或 VRAM 不足的節點執行 simulation、evaluation 或 CPU smoke test。
- 將 GPU 測試分級，避免開發機硬體限制阻塞整體驗證。
- 先提供穩定 CLI；後續 UI 使用相同 Application API，不複製判斷邏輯。

非目標：

- 不依硬體偵測結果自動修改 Experiment 語義。
- 不保證理論 VRAM 估算等於實際峰值。
- 不在第一版提供跨雲供應商自動租用 GPU。
- 不讓 UI 成為 capability 或 compatibility 的權威來源。
- 不因本機缺少 GPU 而略過與 GPU 無關的 correctness tests。

## 2. 三層模型

```text
NodeCapability
    節點實際可提供的資源與 runtime

WorkloadProfile
    某類 Job 的最低、建議與可調整需求

CompatibilityAssessment
    特定 Node × 特定 Workload × 特定 JobSpec 的判定
```

三者必須獨立版本化。NodeCapability 是觀測結果；WorkloadProfile 是程式/模型作者的宣告；CompatibilityAssessment 是 Scheduler/Application service 的輸出。

## 3. Node Capability Discovery

Worker 啟動時執行本機 discovery，產生 `NodeCapability`。Coordinator 不接受 Worker 自行宣稱「可執行 NFSP」作為唯一依據，而是根據結構化資源欄位與已註冊 WorkloadProfile 判斷。

### 3.1 必要欄位

```text
Node identity
├── node_id
├── runtime_version
├── protocol_versions
└── operating_system / architecture

CPU
├── logical_cores
├── physical_cores（可取得時）
└── supported instruction features（後期）

Memory
├── total_bytes
└── available_bytes

GPU[]
├── stable local device id
├── vendor
├── model
├── total_vram_bytes
├── available_vram_bytes
├── compute capability（如適用）
├── driver version
└── supported runtimes

Storage[]
├── path/volume id
├── available_bytes
└── storage class（local SSD / network / unknown）

Runtime
├── python version
├── torch version
├── CUDA runtime version
├── container runtime
└── enabled executor roles
```

`available_*` 是短期動態值，`total_*` 是較穩定值。Scheduler 配對使用 total capacity與已保留資源；Doctor 顯示時可同時呈現 available capacity。不能只以目前空閒 VRAM 推導模型永久需求。

### 3.2 Discovery Adapter

Application port：

```python
class CapabilityDiscovery(Protocol):
    def discover(self) -> NodeCapability: ...
```

第一版 adapters：

- Standard library：OS、architecture、logical CPU、磁碟。
- `psutil`：physical CPU、RAM與動態使用量。
- NVIDIA adapter：優先使用 NVML；不可用時以 PyTorch CUDA API降級。
- Container adapter：Docker/container runtime與GPU passthrough probe。

缺少可選 library 不得使 Worker 無法啟動。未知欄位明確標記 `UNKNOWN`，不能填 0 後當成不支援。

### 3.3 Probe 安全性

Discovery 不執行長時間 benchmark，不下載模型，不分配大塊 VRAM。GPU runtime probe只建立小型 context/張量並立即釋放。所有 probe具有 timeout與獨立 error code。

## 4. Capability Snapshot 與 Heartbeat

Node 註冊時提交完整 immutable capability snapshot；heartbeat只提交動態使用量與 health摘要。硬體、driver、runtime或enabled roles改變時建立新 snapshot revision。

Coordinator 保存：

```text
node_id
capability_revision
capability_schema_version
captured_at
static capability
latest dynamic availability
probe warnings
```

Job lease記錄 claim當時使用的 capability revision，便於分析 OOM、效能與錯誤排程。

## 5. Workload Profile

每個可排程 workload註冊 versioned profile：

```text
workload_key
workload_version
job_type / payload_type
required role
minimum resources
recommended resources
supported runtimes
adjustable parameters
fallback policies
validation level
```

範例：

```yaml
workload_key: nfsp-train-small
workload_version: "1"
payload_type: tableforge.v1.NfspTrainingJobPayload
required_role: nfsp_learner
minimum:
  cpu_cores: 4
  memory_bytes: 17179869184
  gpu_count: 1
  gpu_memory_bytes: 8589934592
  runtime: cuda
recommended:
  gpu_memory_bytes: 12884901888
adjustments:
  batch_size:
    min: 256
    preferred: 2048
  mixed_precision: [bf16, fp16, disabled]
fallbacks:
  - reduce_batch
  - move_to_other_node
```

Resource profile由程式版本與模型 architecture共同決定。同一 NFSP演算法可有 `smoke-cpu`、`small-gpu`、`standard-gpu` 等不同 profile，但不能把縮小模型的測試結果宣稱為標準模型驗證。

## 6. Resource Requirement 類型

既有 `ResourceRequest` 保持 Job配對的最低硬限制，後續以向後相容欄位擴充：

- minimum CPU cores
- minimum system memory
- GPU count
- minimum VRAM per device
- vendor/runtime requirement
- minimum compute capability
- required executor role
- minimum local spool bytes
- expected artifact download bytes
- exclusive GPU requirement

WorkloadProfile另保存 recommended需求與可調參數，不應把所有建議值塞入 JobSpec。

## 7. Compatibility Assessment

輸出狀態：

```text
SUPPORTED
    不修改 JobSpec 即符合最低需求

DEGRADED
    使用 Experiment 已允許的預先定義 fallback 後可執行

UNSUPPORTED
    不符合硬限制或缺少必要 runtime

UNKNOWN
    必要 capability 無法偵測，預設不得派發高風險 Job
```

Assessment至少包含：

```text
status
reason codes
human-readable summary
required vs available resources
selected device
selected workload profile
allowed adjustments
estimated confidence
capability revision
```

Reason code範例：

```text
GPU_NOT_FOUND
INSUFFICIENT_VRAM
CUDA_RUNTIME_MISMATCH
INSUFFICIENT_SYSTEM_MEMORY
INSUFFICIENT_DISK
ROLE_DISABLED
RUNTIME_VERSION_UNSUPPORTED
CAPABILITY_UNKNOWN
```

UI/CLI使用 reason code轉譯，不解析自由文字。

## 8. Preflight 與 Doctor

`tableforge doctor` 執行：

1. 硬體與 runtime discovery。
2. Coordinator、Artifact Store與DNS/TLS connectivity（若已設定）。
3. 本機 artifact cache讀寫與hash驗證。
4. 已安裝 executor self-test。
5. 對內建 workload profiles產生相容矩陣。

輸出範例：

```text
Simulation / Rule Bots     SUPPORTED
Evaluation CPU             SUPPORTED
NFSP CPU Smoke             SUPPORTED
NFSP Small GPU             DEGRADED: batch 2048 → 512
NFSP Standard GPU          UNSUPPORTED: requires 16 GiB VRAM, available 8 GiB
Deep CFR Traversal CPU     SUPPORTED
Deep CFR NN Standard       UNSUPPORTED: GPU_NOT_FOUND
```

Doctor退出碼：

- `0`：核心與至少一個role可用。
- `1`：設定或必要依賴錯誤，Node不能啟動。
- `2`：Node可啟動，但有degraded/unsupported workload；CLI仍輸出完整報告。

## 9. Scheduler 配對

Worker pull request帶 NodeId、capability revision、enabled roles與當前reserved resources。Scheduler：

1. 過濾 payload type/role不支援的 Job。
2. 驗證 immutable Job ResourceRequest。
3. 計算 remaining capacity，扣除active leases reservation。
4. 套用 Experiment明確允許的fallback plan。
5. 產生 CompatibilityAssessment與resolved execution plan。
6. 將 execution plan與capability revision寫入lease/attempt metadata。

Worker收到 Job後再次執行本地 preflight，防止claim後資源被外部程序占用或driver狀態改變。若不符合，使用結構化 `CAPABILITY_CHANGED` 拒絕 lease，不啟動模型。

## 10. Fallback Policy

允許的 fallback必須在 WorkloadProfile定義，並由 Experiment revision明確允許。優先順序範例：

1. 降低 batch size至已驗證區間。
2. 啟用已允許的 mixed precision。
3. 降低同節點並行 Job數。
4. 轉派較大 GPU Node。
5. 若有等價 CPU implementation，執行 CPU smoke/功能測試。
6. 標記unsupported並等待合適Node。

不允許：

- 靜默縮小模型 architecture。
- 靜默降低hand count、training steps或改變seed。
- OOM後無限重試相同設定。
- 將CPU smoke test視為GPU效能或數值相容驗證。

每次 adjustment寫入Attempt execution plan與metrics，確保可重現。

## 11. OOM 與執行期資源錯誤

GPU OOM分類：

- `ESTIMATE_MISS`：preflight通過但實際峰值超過估算。
- `DYNAMIC_CONTENTION`：其他程序占用資源。
- `WORKLOAD_GROWTH`：資料shape或runtime path超出profile範圍。
- `UNKNOWN_OOM`。

Executor在安全可行時釋放model/optimizer/cache，保存diagnostic metrics，不記錄敏感資料。Scheduler只在profile允許且尚未嘗試過的execution plan下重試。每個attempt保存 peak VRAM、batch、precision、device與error code，供修正WorkloadProfile。

## 12. 測試分級

測試marker：

```text
unit
integration
cpu
requires_gpu
requires_cuda
vram_8gb
vram_16gb
vram_24gb
vram_32gb
external_node
soak
```

CI與本機預設必跑：

- unit、contract、CPU integration
- capability parser與compatibility matrix
- fake GPU capability scenarios
- CPU smoke model
- GPU缺失時的graceful skip與unsupported判斷

GPU測試：

- 有GPU時執行符合本機tier的tests。
- 不符合tier時使用pytest skip並輸出明確reason，不算failure。
- 至少一個外部GPU pipeline定期執行已支援的最高tier。
- Release manifest列出各tier最後通過的硬體、driver、runtime與commit。

功能 correctness與硬體相容性分離。例如 checkpoint serialization可在CPU測；CUDA kernel、mixed precision與peak VRAM必須在對應GPU tier測。

## 13. 使用者角色與操作模型

### 單機研究者

```text
tableforge doctor
tableforge node start --roles simulation,evaluation,nfsp_learner
tableforge experiment create --preset hu-rule-soak
tableforge experiment start <experiment-id>
tableforge experiment status <experiment-id>
tableforge experiment stop <experiment-id>
```

### 第二台 Worker 提供者

```text
tableforge doctor --coordinator https://node-a:7443
tableforge node enroll --token-file <path>
tableforge node start --roles simulation
```

### 只使用平台的使用者

後期透過Web UI建立與查看Experiment，不直接安裝Python。若自行提供Node，仍需安裝Docker、TableForge Node package及GPU driver/toolkit（GPU角色才需要）。

## 14. CLI Commands

第一版CLI最小集合：

```text
tableforge doctor [--json]
tableforge node init
tableforge node start
tableforge node status
tableforge workload list
tableforge workload inspect <key>
tableforge experiment create --preset <name>
tableforge experiment start <id>
tableforge experiment status <id>
tableforge experiment stop <id>
tableforge job list [--experiment <id>]
tableforge artifact verify <id>
```

所有可供UI使用的輸出支援versioned JSON。人類輸出可改版，但JSON schema須遵守相容策略。CLI是Application service client，不直接存取Coordinator DB。

## 15. Preset

Preset是versioned Experiment config template，不是隱藏程式邏輯：

```text
hu-rule-smoke
hu-rule-soak
hu-nfsp-cpu-smoke
hu-nfsp-small-gpu
hu-nfsp-standard-gpu
```

`experiment create`先執行preflight preview，顯示本機或cluster是否有可執行Node。即使目前沒有合適Node，使用者可選擇建立PAUSED Experiment等待資源；不得建立後立即進入無限排程失敗。

## 16. UI 責任

UI後期顯示：

- Node inventory與capability revision
- workload compatibility matrix
- preset可用性
- required/recommended/available資源
- fallback execution plan
- unsupported reason與修復建議
- Job實際使用的device、batch、precision及peak memory

UI不負責：

- 自行判斷VRAM是否足夠。
- 修改Job ResourceRequest。
- 決定fallback是否合法。
- 將unknown capability當成supported。

UI只呼叫與CLI相同的 Capability/Workload/Assessment API。

## 17. Protobuf 草案

```proto
message NodeCapability {
  string schema_version = 1;
  string node_id = 2;
  uint64 revision = 3;
  string operating_system = 4;
  string architecture = 5;
  uint32 logical_cpu_cores = 6;
  uint32 physical_cpu_cores = 7;
  uint64 total_memory_bytes = 8;
  uint64 available_memory_bytes = 9;
  repeated GpuCapability gpus = 10;
  repeated StorageCapability storage = 11;
  RuntimeCapability runtime = 12;
  repeated string enabled_roles = 13;
  google.protobuf.Timestamp captured_at = 14;
  repeated ProbeWarning warnings = 15;
}

message GpuCapability {
  string device_id = 1;
  string vendor = 2;
  string model = 3;
  uint64 total_memory_bytes = 4;
  uint64 available_memory_bytes = 5;
  string driver_version = 6;
  string compute_capability = 7;
  repeated string runtimes = 8;
}

message WorkloadProfile {
  string schema_version = 1;
  string workload_key = 2;
  string workload_version = 3;
  string payload_type = 4;
  ResourceRequest minimum = 5;
  ResourceRequest recommended = 6;
  repeated string supported_runtimes = 7;
  repeated AdjustmentPolicy adjustments = 8;
}

message CompatibilityAssessment {
  string schema_version = 1;
  CompatibilityStatus status = 2;
  string node_id = 3;
  uint64 capability_revision = 4;
  string workload_key = 5;
  string workload_version = 6;
  repeated string reason_codes = 7;
  ResourceComparison resources = 8;
  ResolvedExecutionPlan plan = 9;
}
```

正式欄位只能append，不重用field number。動態availability可另用Heartbeat message，避免每次heartbeat複製完整static capability。

## 18. Domain 與 Storage

新增domain objects：

```text
NodeCapability
GpuCapability
StorageCapability
RuntimeCapability
WorkloadProfile
CompatibilityAssessment
ResolvedExecutionPlan
```

metadata新增或擴充：

```text
node_capability_snapshots
workload_profiles
job_attempt.execution_plan
job_attempt.capability_revision
job_attempt.resource_metrics
```

Capability snapshot與WorkloadProfile immutable；Node current capability pointer與WorkloadProfile active version是mutable metadata。大型doctor diagnostic可使用既有DIAGNOSTIC artifact，不新增專用artifact kind。

## 19. Security 與 Privacy

Capability資料可能暴露主機名稱、硬體序號與內部路徑。第一版：

- 不收集主機名稱、MAC、GPU serial或使用者名稱，除非功能必要。
- device id僅需Node本地穩定，不作跨Node全域身份。
- Storage只提交容量與class，不提交任意完整host path給其他Worker。
- Doctor `--json`輸出標記可分享與敏感欄位。
- Enrollment後才可向Coordinator上傳capability。

## 20. Failure Semantics

- Discovery adapter失敗：記錄warning與UNKNOWN，不偽造0。
- Capability過期：超過TTL的Node不可claim新Job，既有lease依heartbeat policy處理。
- Claim後資源變化：Worker拒絕啟動並回報`CAPABILITY_CHANGED`。
- Driver/runtime mismatch：UNSUPPORTED，不嘗試GPU Job。
- OOM：保存結構化diagnostic，僅按有限fallback plan重試。
- Fake/錯誤 capability：本地preflight為第二道防線；重複錯報可隔離Node。
- Workload profile版本缺失：Job保持QUEUED並產生operator-visible error，不猜測需求。
- UI/CLI版本較舊：未知reason code仍顯示原始code，不把狀態降為SUPPORTED。

## 21. Testing

### Discovery

- 無GPU、單GPU、多GPU fixtures。
- NVML不存在、driver錯誤、CUDA不可初始化。
- RAM/磁碟未知與permission error。
- Windows/Linux adapter normalization。

### Compatibility

- boundary equal、低1 byte/1 core與多GPU條件。
- required role與runtime mismatch。
- UNKNOWN欄位保守判斷。
- reserved resources扣除。
- degraded adjustment必須來自profile與Experiment允許集合。

### Scheduler

- 不派GPU Job給CPU-only Node。
- 不派16GB workload給8GB GPU。
- 同一GPU exclusive lease不可重疊。
- capability revision改變後重新assessment。
- stale Node不claim新Job。

### CLI/API

- `doctor --json` schema golden test。
- unsupported reason與退出碼。
- preset preview與PAUSED建立。
- CLI不直接開啟metadata DB。

### GPU Tier

- CPU-only環境所有required tests通過。
- GPU tests依marker與capability選擇，不因缺GPU報failure。
- 外部GPU pipeline驗證checkpoint、mixed precision與VRAM profile。

## 22. Development Milestones

能力線插入M2至M4，不改M0–M5目標：

- `C0`：純Python NodeCapability domain、fake discovery與compatibility evaluator。
- `C1`：`tableforge doctor`、CPU/RAM/storage discovery與JSON schema。
- `C2`：NVIDIA discovery、GPU tier markers與optional tests。
- `C3`：Scheduler resource filtering、reservation與capability revision記錄。
- `C4`：Workload profiles、preset preflight與degraded execution plan。
- `C5`：UI capability API與Dashboard呈現；不阻塞CLI MVP。

M2 Worker啟動前完成C0–C1；第一個GPU learner前完成C2–C4。C5屬平台化階段。

## 23. Existing Specs Impact

```text
README.md
新增本文件索引，以及CLI-first與capability判斷位於核心服務的決策。

01-system.md
在Worker Runtime與Scheduler責任新增capability discovery、compatibility assessment與resource reservation。

02-contracts-and-data.md
新增NodeCapability、WorkloadProfile、CompatibilityAssessment與Attempt capability revision/execution plan擴展點。

03-runtime-and-correctness.md
新增claim前/執行前雙重preflight、OOM分類與GPU optional測試原則。

04-deployment-and-evolution.md
新增doctor/CLI操作、測試tier與C0–C5里程碑。

05-opponent-modeling.md
No change。
```
