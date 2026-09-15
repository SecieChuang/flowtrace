# FlowTrace

> 本地优先（local-first）、可完全自托管的个人工时与专注度追踪系统。
> Windows 客户端自动采集 + Flask/SQLite 服务端分析，数据全部保存在你自己的机器上。

**English**: FlowTrace is a local-first, self-hosted personal time-tracking and focus-analytics system.
A Windows client (AutoHotkey runtime) records your active/idle periods, clock-ins and self-ratings;
a Flask + SQLite server turns them into daily/weekly dashboards, focus trends and optional AI-powered daily summaries.
No cloud account, no third-party analytics — your data never leaves your own infrastructure.

## 功能特性

- **自动活动追踪**：检测键鼠活跃/空闲状态，自动生成时间线片段，无需手动记时
- **手动打卡**：全局热键上下班打卡（默认 `Ctrl+Shift+I` / `Ctrl+Shift+O`，在 `client/scripts/flowtrace.ahk` 顶部修改），支持跨零点班次推断
- **三口径工时**：在岗 / 活跃 / 有效（在岗且活跃）分别统计，专注率 = 有效 / 在岗
- **自评分**：活跃累计一定时长后弹出 1–5 分即时评分，与产出数据交叉分析
- **统计面板**：30 天趋势（柱色按专注率分档）、上下班时间画像、周目标达成（柱 = 周时长按专注率染色，线 = 周均自评分）、有效工作日历、趣味统计
- **AI 截图日报**（可选）：定时截屏 + 多 provider（DashScope / 智谱 / MiniMax / Mistral）生成每日时间线总结，支持 webhook 推送到飞书等
- **离线队列**：服务端不可达时事件暂存本地，恢复后自动补传
- **Docker 部署**：服务端自带 Dockerfile 与 docker-compose.yml，一条命令起服务

![仪表盘](docs/images/dashboard.png)

![统计页](docs/images/stats.png)

## 架构

```
┌─────────────────────────────┐         HTTP (api_key 鉴权)        ┌─────────────────────────────┐
│  client/ (Windows)          │  ───────────────────────────────►  │  server/                    │
│  · AHK v2 运行时             │   活动片段 / 打卡 / 评分            │  · Flask + SQLite            │
│  · 空闲检测、热键、评分弹窗    │                                    │  · 打卡去重、跨零点推断        │
│  · 离线队列（断网暂存）       │  ◄───────────────────────────────  │  · 统计 API + 仪表盘页面      │
│  · 可选截图 + AI 日报        │     配置 / 状态                    │  · 检测引擎 + manage_cli     │
└─────────────────────────────┘                                    └─────────────────────────────┘
```

更深入的架构说明见 [docs/architecture.md](docs/architecture.md)。

## 快速开始

### 服务端

Docker（推荐）：

```bash
cd server
cp config_example.json config.json   # 编辑 config.json，务必修改 api_key
docker compose up -d --build
```

Docker 模式默认把宿主机 `7292` 端口映射到容器 `8000`，访问仪表盘：`http://localhost:7292`。

或直接运行（Python 3.11）：

```bash
cd server
pip install -r requirements.txt
cp config_example.json config.json
python server.py
```

直接运行默认监听 `8000` 端口，访问仪表盘：`http://localhost:8000`。

数据库文件默认在仓库根目录 `data/flowtrace.db`（首次启动自动创建），备份直接复制该文件即可。

### 客户端（Windows）

前置：安装 [AutoHotkey v2](https://www.autohotkey.com/) 和 Python 3.11。

```bash
cd client
pip install -r requirements.txt
cp config/config_example.json config/config.json   # 填入服务器地址和 api_key
```

然后运行采集运行时 `client/scripts/flowtrace.ahk`（双击即可，或用 `client/scripts/start.bat`）。
上下班热键硬编码在 `flowtrace.ahk` 顶部（默认 `^+i` 上班 / `^+o` 下班，即 Ctrl+Shift+I / Ctrl+Shift+O），改热键直接编辑该文件。

可选：运行 `client/setup_all.bat` 一键注册开机自启和"截图日报"计划任务；`client/remove_all.bat` 反注册。

## 部署模式

| 配置项 | 本地模式（推荐个人使用） | 云端模式（服务器部署） |
|---|---|---|
| 服务端 `host` | `127.0.0.1` | `0.0.0.0` |
| 服务端 `api_key` | 留空 `""`（不鉴权） | 设置为强随机串 |
| 客户端 `server_base_url` | 留空（自动回退 `http://localhost:8000`） | `http://<服务器地址>:7292`（Docker） |
| 客户端 `api_key` | 留空 | 与服务端相同 |

完整部署指南见 [docs/deployment.md](docs/deployment.md)。

## 测试

```bash
pip install pytest
python -m pytest tests
```

共 239 个用例，覆盖服务端 API、检测引擎、槽位填充和客户端同步/离线队列/截图等模块。

## 配置

- 服务端：`server/config_example.json` —— 监听地址、数据库路径、`api_key`、打卡去重、推断参数等
- 客户端：`client/config/config_example.json` —— 服务器地址、空闲阈值、评分频率、截图与 AI 日报开关等（热键在 `flowtrace.ahk` 顶部修改）

**每个字段的完整说明**见 [docs/configuration.md](docs/configuration.md)。

两个 `config.json` 都在 `.gitignore` 中，不会被提交。**请勿在任何公开位置分享你的 `config.json`。**

## 隐私说明

- 所有数据（活动片段、打卡、评分、截图）只存放在你自己机器上的 SQLite 数据库和本地目录中，没有任何外部回传
- 截图 / 摄像头采集为可选功能，可在客户端配置中完全关闭；本地保留期可配置
- AI 日报仅在主动配置 provider API key 后才会把截图发给对应服务商；不配置则完全不启用，没有任何数据外发

## 文档

- [docs/configuration.md](docs/configuration.md)：服务端与客户端全部配置字段说明
- [docs/deployment.md](docs/deployment.md)：本地 / 云端两种部署模式、备份、升级与卸载
- [docs/architecture.md](docs/architecture.md)：系统架构、数据模型与检测引擎
- [docs/manage-cli-guide.md](docs/manage-cli-guide.md)：数据核查 CLI 使用指南
- [docs/clock-detection-architecture-final.md](docs/clock-detection-architecture-final.md)：打卡检测框架设计定稿（V4）

## 目录结构

```
client/    Windows 客户端（AHK 采集运行时、热键、截图、AI 调用）
server/    服务端（Flask API、SQLite、检测/推断逻辑、仪表盘静态页、Docker）
docs/      架构设计文档与事故复盘
scripts/   数据迁移与修复辅助脚本
tests/     pytest 测试套件（239 个用例）
```

## License

[MIT](LICENSE) © 2026 SecieChuang
