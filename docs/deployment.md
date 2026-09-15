# 部署指南

FlowTrace 支持两种部署模式。所有数据（SQLite 数据库、截图）都在你自己的机器上，没有第三方依赖。

| | 本地模式（推荐个人使用） | 云端模式（部署到自己的服务器） |
|---|---|---|
| 适用场景 | 客户端和服务端在同一台 Windows 机器 | 服务端在云主机，客户端在任何 Windows 机器 |
| 服务端 `host` | `127.0.0.1` | `0.0.0.0` |
| 服务端 `api_key` | 留空 `""`（不鉴权） | **必须**设置强随机串 |
| 客户端 `server_base_url` | 留空（自动回退 `http://localhost:8000`） | `http://<服务器地址>:7292`（Docker 映射端口） |
| 客户端 `api_key` | 留空 | 与服务端相同 |

> 安全提醒：`api_key` 为空时写接口不鉴权，且 dashboard 读接口**始终不鉴权**。服务端绑定非回环地址又没设 `api_key` 时，直接运行 `python server.py` 会在启动时打印醒目警告。云端模式请务必设置 `api_key`。

---

## 服务端部署

### 方式一：Docker Compose（推荐）

前置：Docker + Docker Compose 插件。

```bash
cd server
cp config_example.json config.json   # 然后编辑 config.json
docker compose up -d --build
```

要点：

- **必须先创建 `config.json` 再构建**：`Dockerfile` 里有 `COPY config.json .`，缺文件会构建失败。
- 端口映射：宿主机 `7292` → 容器 `8000`（见 `docker-compose.yml`）。仪表盘地址 `http://<主机>:7292`。
- 数据持久化：仓库根 `../data` 挂载到容器 `/app/data`，数据库即仓库根 `data/flowtrace.db`。
- `server/static/`、`detection.py`、`manage_cli.py` 以只读卷挂载进容器，改这几个文件只需 `docker compose restart`；改 `server.py` / `shifts.py` / `requirements.txt` / `config.json` 需要 `docker compose up -d --build` 重建镜像（注意 config.json 是 COPY 进镜像的）。
- `API_KEY` 环境变量可覆盖 `config.json` 的 `api_key`（如 `API_KEY=xxx docker compose up -d`）；compose 文件默认注入 `API_KEY=${API_KEY:-}`，空值会回退到 config.json 的值，不会遮蔽。
- 容器内 gunicorn 固定 **单 worker**（`-w 1`），这是日指标内存缓存一致性的前提，请勿调高（背景见 [incident-2026-05-01-cache-inconsistency.md](incident-2026-05-01-cache-inconsistency.md)）。

常用命令：

```bash
docker compose logs -f        # 看日志
docker compose ps             # 看状态
docker compose restart        # 重启（清日指标缓存）
docker compose down           # 停止并删除容器（数据在宿主机 data/，不受影响）
curl http://localhost:7292/api/ping   # 健康检查 → {"status":"ok"}
```

### 方式二：直接 Python 运行

前置：Python 3.11。

```bash
cd server
pip install -r requirements.txt    # flask + gunicorn
cp config_example.json config.json # 然后编辑
python server.py
```

- 默认监听 `config.json` 的 `host:port`（模板为 `0.0.0.0:8000`；本地模式改成 `127.0.0.1`）。
- 数据库路径默认仓库根 `data/flowtrace.db`（`database` 字段相对 `server/` 解析），可用环境变量 `DATABASE_PATH` 覆盖。
- 首次启动自动建库建表。

## 客户端安装（Windows）

前置：

1. 安装 [AutoHotkey v2](https://www.autohotkey.com/)（运行时是 `client/scripts/flowtrace.ahk`）
2. 安装 Python 3.11（AHK 通过调用 `client/src/sync.py` 与服务端通信）

步骤：

```bash
cd client
pip install -r requirements.txt                     # requests / Pillow / dashscope
cp config/config_example.json config/config.json    # 然后编辑：server_base_url、api_key
```

可选依赖按需安装：摄像头快照要 `opencv-python`；AI 备用 provider 分别要 `zhipuai` / `openai` / `mistralai`（见 [configuration.md](configuration.md)）。

启动 / 停止：

- 日常使用：双击 `client/scripts/flowtrace.ahk`，或运行 `client/scripts/start.bat`；停止用 `client/scripts/stop.bat`。
- 打卡热键：默认 `Ctrl+Shift+I` 上班 / `Ctrl+Shift+O` 下班（硬编码在 `flowtrace.ahk` 顶部，改热键直接编辑该文件）。

### 开机自启 + 计划任务（可选）

```bat
client\setup_all.bat      :: 一键：注册计划任务 + 设置开机自启
client\remove_all.bat     :: 一键反注册（卸载时用）
```

`setup_all.bat` 做的事：

1. `scripts/register_notification_tasks.bat`（内部调 ps1）：注册名为 **Flowtrace Screenshot Summary** 的计划任务，按 `screenshot_summary_schedule_*` 配置定时跑 AI 截图日报。会从 `config.json` 读 `automation_python_executable`，未设置则要求 `python` 在 PATH 上。
2. `scripts/setup_autostart.bat`：在 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` 写入 `Flowtrace.cmd`，开机自动启动 `flowtrace.ahk`。

## 数据位置与备份

| 数据 | 位置 | 说明 |
|---|---|---|
| 数据库 | 仓库根 `data/flowtrace.db` | 全部打卡 / 活动 / 评分。**备份 = 直接复制该文件** |
| 客户端截图 | `client/screenshots/<日期>/` | 仅本机，按 `screenshot_retention_days` 自动清理 |
| AI 日报产物 | `client/runtime/summaries/` | HTML + JSON |
| 离线队列 | `client/backup/sync_queue.jsonl` | 服务端不可达时的暂存事件，恢复后自动补传 |

备份示例：

```bash
cp data/flowtrace.db data/flowtrace-$(date +%F).db.bak
```

数据库以 WAL 模式运行，目录下可能同时存在 `flowtrace.db-wal` / `flowtrace.db-shm`；最稳妥的做法是先停服务再复制，或把它们一起复制。

## 升级

```bash
git pull

# Docker 部署：改了 server.py / shifts.py / 依赖 / config.json → 重建镜像
cd server && docker compose up -d --build

# 只改了 detection.py / manage_cli.py / static/（卷挂载）→ 重启即可
docker compose restart

# 直接 Python 运行：重启进程
```

客户端升级：`git pull` 后重新 `pip install -r client/requirements.txt`（有依赖变化时），并重启 `flowtrace.ahk`。

## 数据核查（修复混乱打卡）

历史打卡出现漏打 / 打反 / 孤立时，用交互式 CLI 修复（详见 [manage-cli-guide.md](manage-cli-guide.md)）：

```bash
# Docker 部署（manage.sh 是 docker exec 的包装）
sh manage.sh
# 或
docker exec -it flowtrace-server python manage_cli.py

# 直接 Python 运行（仓库根目录）
python -m server.manage_cli
```

## 卸载

1. 客户端：运行 `client\remove_all.bat` 移除计划任务与开机自启，再 `client\scripts\stop.bat` 停止运行时；
2. 服务端：`cd server && docker compose down`（Docker 模式）或停止 Python 进程；
3. 数据文件（`data/`、`client/screenshots/`、`client/runtime/`）按需保留或手动删除。

## 相关文档

- 配置字段逐项说明：[configuration.md](configuration.md)
- 系统架构：[architecture.md](architecture.md)
- 缓存一致性事故复盘（为何单 worker）：[incident-2026-05-01-cache-inconsistency.md](incident-2026-05-01-cache-inconsistency.md)
