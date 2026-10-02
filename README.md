# TableForge

[![CI](https://github.com/kcfan041/TableForge/actions/workflows/ci.yml/badge.svg)](https://github.com/kcfan041/TableForge/actions/workflows/ci.yml)

分散式多代理德州撲克 AI 訓練與評估框架。目前支援 No-Limit Hold'em（Heads-Up 與 2–9 人桌，含錦標賽與 PKO）、Rule Bots、NFSP（CPU 與 NVIDIA GPU）與 PPO，以及多台電腦組成叢集一起運算；並提供模型庫、模型比較、和模型對打與練習、牌譜分析。

## 開始使用

```
git clone https://github.com/kcfan041/TableForge.git
cd TableForge
uv sync --extra learning --extra gpu
uv run tableforge ui
```

需要 [uv](https://docs.astral.sh/uv/)（會自動下載 Python 3.12）。沒有 NVIDIA GPU 的電腦可以省略 `--extra gpu`。

`tableforge ui` 會在瀏覽器開啟 TableForge，第一次選「建立新的叢集」或「加入現有叢集」，之後提供運算、邀請別人、建立實驗、看結果都在同一個頁面。完整步驟見 **[使用指南](docs/tester-guide.md)**。

## 文件

- [使用指南](docs/tester-guide.md)：安裝、建立或加入叢集、邀請別人、常見問題
- [進階：指令與部署](docs/advanced.md)：指令版操作、設定、PostgreSQL、S3、Docker、指令一覽
- [開發進度](docs/status.md)
- [架構規格](docs/architecture/README.md)
- [長期願景](docs/vision.md)

## 開發

```
uv sync --extra learning --extra gpu --extra postgres --extra s3
uv run pytest
uv run ruff check .
uv run mypy
```

> 注意：之後若只執行 `uv sync` 而沒有加上 extras，PyTorch 等套件會被移除，相關測試會顯示為略過。

- 修改 `proto/` 之後執行 `uv run python scripts/generate_protos.py`；產生的程式碼在 `src/tableforge/_generated/`，已 commit 進 repo。
- 網頁前端的原始碼在 `web/`（React + TypeScript）。修改後在 `web/` 執行 `npm ci && npm run build`，把 `src/tableforge/web/static/` 的建置結果一起 commit；使用者不需要 Node.js。
- 端對端檢查（使用真實程序，每一步印一行，失敗或逾時會先停止啟動過的程序）：

  | 腳本 | 內容 | 時間 |
  |---|---|---|
  | `scripts/create_cluster_e2e.py` | 用 `tableforge ui` 建立叢集、背景服務、瀏覽器網頁開關 | 約 30 秒 |
  | `scripts/cluster_ui_e2e.py` | 四個 `tableforge ui` 依權限管理同一個叢集 | 約 20 秒 |
  | `scripts/local_ui_e2e.py` | 加入、連線檢查、Worker 啟停 | 約 10 秒 |
  | `scripts/web_e2e.py` | 瀏覽器網頁的登入與 API | 約 5 秒 |

  前兩個使用 7443 port，執行前請確認這台電腦沒有正在執行的叢集服務。

- 測試用 PostgreSQL 與 S3（沒有時相關測試自動略過）：

  ```
  docker run -d --name tableforge-pg-test --memory 1g -p 127.0.0.1:55432:5432 -e POSTGRES_USER=tableforge -e POSTGRES_PASSWORD=tableforge -e POSTGRES_DB=tableforge postgres:17-alpine
  docker run -d --name tableforge-s3-test --memory 512m -p 127.0.0.1:59000:9000 -e RUSTFS_ACCESS_KEY=tableforge -e RUSTFS_SECRET_KEY=tableforge-secret rustfs/rustfs:latest
  ```

  其他位置用 `TABLEFORGE_TEST_POSTGRES_URL`、`TABLEFORGE_TEST_S3_ENDPOINT` 指定；`TABLEFORGE_DISABLE_POSTGRES_TESTS=1`、`TABLEFORGE_DISABLE_S3_TESTS=1` 則略過。

每次 push 到 `main` 或開 pull request，GitHub Actions 會執行（`.github/workflows/ci.yml`）：Ruff、mypy 與 proto 一致性、前端建置結果一致性、Linux 完整測試（PostgreSQL 17 與 RustFS）、Windows CPU 測試、Docker 映像建置。CI 沒有 GPU，GPU 測試需要在有 NVIDIA GPU 的電腦上執行。

### 版本相容

叢集服務和節點之間有「協定版本」（`src/tableforge/version.py`）。只有不相容的協定修改才提高 `PROTOCOL_VERSION`；叢集服務不再接受舊節點時提高 `MIN_PROTOCOL_VERSION`。只新增 protobuf 欄位或 RPC 不需要提高。版本不相容時，加入、Worker 與連線檢查會停止並提示更新哪一邊。

## 已知限制

- 訓練演算法目前有 NFSP 與 PPO（含 MMD 正則化；PPO 只用於錦標賽，小型實驗會先升後跌，尚未正式訓練）；PSRO、Deep CFR 等尚未實作。舊的 League 對戰矩陣只支援 Heads-Up 現金局，錦標賽改用對手池與模型比較。
- 新格式（9 人桌、細分下注尺寸、多結構訓練）已正式訓練，驗收比較尚未完成；目前模型庫裡的模型都是 6 人以下的舊格式。
- 大型 MTT 只以「單桌 + 模擬其他桌」的階段模擬（泡沫期、剛進錢圈、最後三桌，賽場上限 1,000 人），沒有補位、併桌與 late registration；看得到賽場資訊的 v4 模型尚未訓練。
- 牌譜匯入（GGPoker／Natural8）依公開格式撰寫，尚未用真實匯出檔驗證。
- GPU 只支援 NVIDIA CUDA；Apple Silicon 的 MPS 尚未支援。
- Windows 上 GPU 記憶體也會佔用系統記憶體配額，page file 太小時可用的 batch 會變小（`doctor` 會顯示警告）。
- 使用本機 artifact store 時，artifact 經由叢集服務傳送，單檔上限 1 GiB；使用 S3 則沒有這個限制。
- CUDA Worker 容器尚未在實機驗證；Windows 上請在主機執行 GPU Worker。
- 完整清單見 [docs/status.md](docs/status.md)。
