# 配置参考

本文档列出服务端与客户端**每一个**配置字段的说明。字段清单以两个模板文件为准：

- 服务端：`server/config_example.json`（复制为 `server/config.json` 使用）
- 客户端：`client/config/config_example.json`（复制为 `client/config/config.json` 使用）

两个 `config.json` 都在 `.gitignore` 中，不会被提交。

> "代码兜底值"指配置缺失或读取失败时代码里实际使用的默认值（可能与模板示例值不同）；类型与含义以代码读取位置为准。

---

## 服务端 `server/config.json`

读取位置：`server/server.py` 顶部的配置加载段。Docker 部署时通过 `CONFIG_PATH` 指定（compose 中为 `/app/config.json`）。

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 / 注意事项 |
|---|---|---|---|---|
| `host` | string | `"0.0.0.0"` | `"0.0.0.0"` | 监听地址。本地模式改为 `"127.0.0.1"`。**仅对 `python server.py` 生效**；Docker 镜像由 gunicorn 固定绑定 `0.0.0.0:8000` |
| `port` | int | `8000` | `8000` | 监听端口。同样仅对直接运行生效；Docker 端口映射在 `docker-compose.yml`（默认宿主机 7292 → 容器 8000） |
| `database` | string | `"../data/flowtrace.db"` | 仓库根 `data/flowtrace.db` | SQLite 数据库路径。相对路径**相对 `server/` 目录解析**，所以模板值正好指向仓库根 `data/`。被环境变量 `DATABASE_PATH` 覆盖 |
| `api_key` | string | `"CHANGE_ME"` | `""` | 写接口（checkin / activity / activity/batch / rating）的鉴权密钥，客户端通过 `X-API-Key` 请求头携带。**留空 = 不鉴权**（dashboard 读接口始终不鉴权）。被环境变量 `API_KEY` 覆盖。云端部署务必改成强随机串；模板占位符 `CHANGE_ME` 也必须改掉 |
| `checkin_dedupe_seconds` | int | `60` | `60` | 打卡入库去重窗口（秒）：同一天同方向、与上一条间隔小于该值的打卡被忽略，保留先到的 |
| `auto_merge_gap_minutes` | int | `2` | `2` | 自动活动片段的合并间隙（分钟）：间隙 ≤ 此值的相邻 auto 段在调和时合并为一段 |
| `auto_min_fragment_minutes` | int | `2` | `2` | 调和时，被手动段裁切后小于该时长（分钟）的 auto 碎片丢弃 |
| `open_manual_clip_days` | int | `7` | `7` | 未闭合手动段（有 start 无 end）在调和时按"从 start 起 N 天"的占位区间处理 |
| `manage_default_days` | int | `30` | `30` | `manage_cli.py` 数据核查的默认扫描天数（扁平字段，不是嵌套对象） |
| `inference.enable_no_manual_day` | bool | `false` | `false` | 当天**完全无打卡**时的在岗口径：`false` = 全天离岗（默认，推荐）；`true` = 把当天活跃段也计为在岗 |
| `inference.merge_gap_minutes` | int | `40` | `40` | 活跃段合并的"摸鱼容忍"（分钟）：段间空隙 ≤ 此值视为同一活跃段。同时是检测引擎槽位搜索的正常距离上限 |
| `inference.daypart_thresholds.morning` | int | `40` | `40`（回退到 `merge_gap_minutes`） | 分时段摸鱼阈值：6:00–12:00 |
| `inference.daypart_thresholds.afternoon` | int | `40` | 同上 | 12:00–18:00 |
| `inference.daypart_thresholds.evening` | int | `40` | 同上 | 18:00–24:00 |
| `inference.daypart_thresholds.night` | int | `40` | 同上 | 0:00–6:00 |

### 环境变量覆盖

优先级：环境变量 > `config.json` > 代码兜底值。

| 环境变量 | 作用 |
|---|---|
| `CONFIG_PATH` | 指定服务端配置文件路径（默认 `server/config.json`） |
| `DATABASE_PATH` | 覆盖数据库路径（docker-compose 中固定注入 `/app/data/flowtrace.db`） |
| `API_KEY` | 覆盖 `api_key`。注意代码用 `or` 语义：**空字符串的环境变量会回退到 config.json 的值**，不会遮蔽它（docker-compose 总会注入 `API_KEY=${API_KEY:-}`） |

### 安全提示

- `api_key` 为空且 `host` 绑定非回环地址时，`python server.py` 启动会打印醒目警告（任何能访问该地址的机器都可未鉴权调用全部接口，包括读取仪表盘）。
- dashboard 读接口（日/周/统计等）**不鉴权**，这是当前设计。云端部署请把 `api_key` 设好，并自行评估是否在网络层（防火墙/反向代理）加保护。

---

## 客户端 `client/config/config.json`

读取方分两层：

- **AHK 运行时**（`client/scripts/flowtrace.ahk`）：读取阈值/间隔/评分/提示位置/截图开关与间隔等顶层字段；
- **Python 业务层**（`client/src/sync.py` / `screenshotter.py` / `screenshot_summary.py` / `ai_provider.py` / `run_scheduled_job.py` 等）：读取服务器地址、截图细节、AI provider、webhook 等。

> 注意：**上下班热键不在 config 里**，硬编码在 `flowtrace.ahk` 顶部（默认 `^+i` 上班 / `^+o` 下班），改热键请直接编辑该文件。

### 连接

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 / 注意事项 |
|---|---|---|---|---|
| `server_base_url` | string | `""` | `"http://localhost:8000"` | 服务端地址。**留空自动回退 localhost:8000**（本地模式可以不填）；云端模式填 `http://<服务器地址>:7292`（Docker 端口映射） |
| `api_key` | string | `""` | `""` | 与服务端 `api_key` 一致；服务端留空时这里也留空 |

### 活动检测与评分（AHK 运行时读取）

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 |
|---|---|---|---|---|
| `idle_threshold_seconds` | int | `300` | `300` | 无键鼠输入超过该秒数判为空闲，结束当前活跃段 |
| `start_threshold_seconds` | int | `60` | `60` | 从空闲进入活跃前的"预热"时长（秒），防止短暂碰键盘误触发 |
| `monitor_interval_seconds` | int | `10` | `10` | 状态机轮询间隔（秒） |
| `sync_interval_seconds` | int | `60` | `60` | 活跃期间向服务端更新 `end_time` 的间隔（秒） |
| `rating_trigger_minutes` | int | `90` | `90` | 活跃累计该分钟数后弹出自评分窗口（1–5 分） |
| `rating_timeout_seconds` | int | `10` | `10` | 评分窗口超时自动关闭时间（秒） |
| `toast_duration_ms` | int | `2000` | `2000` | 打卡结果 toast 提示的显示时长（毫秒） |
| `toast_offset_right` | int | `36` | `36` | toast 距屏幕右边缘的像素偏移 |
| `toast_offset_bottom` | int | `100` | `100` | toast 距屏幕底边缘的像素偏移 |

### 截图采集

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 / 注意事项 |
|---|---|---|---|---|
| `screenshot_enabled` | bool | `true` | `true` | 截图总开关。`false` 时 AHK 不启动截图定时器，完全不采集 |
| `screenshot_capture_webcam` | bool | `false` | `false` | 截屏时同时拍一张摄像头快照。**需要额外安装 `opencv-python`**，摄像头不可用时只告警不中断 |
| `screenshot_interval_minutes` | int | `5` | `5` | 截图间隔（分钟）。在岗时总是截图；不在岗时仅活跃状态下截图 |
| `screenshot_quality` | int | `60` | `60` | JPEG 质量（1–95）。代码兜底就是 60 |
| `screenshot_max_dimension` | int | `1280` | `1280` | 截图最长边像素，超出等比缩小（多屏合并截取） |
| `screenshot_retention_days` | int | `7` | `7` | 本地截图保留天数，超期自动清理（每天最多清理一次）；`<=0` 不清理 |

截图保存在 `client/screenshots/<日期>/` 下，只存在本机。

### AI 截图日报

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 / 注意事项 |
|---|---|---|---|---|
| `screenshot_summary_max_images` | int | `20` | `20` | 一次日报最多抽取的截图张数（必须 > 0，超过则均匀抽样） |
| `screenshot_summary_batch_size` | int | `6` | `6` | 每批发给视觉模型的图片数（必须 > 0） |
| `screenshot_summary_schedule_enabled` | bool | `true` | `true` | 注册计划任务时是否启用"截图日报"任务（由 `register_notification_tasks.ps1` 读取） |
| `screenshot_summary_schedule_days` | string | `"MON,TUE,WED,THU,FRI,SAT,SUN"` | 同左 | 日报任务运行的星期，逗号分隔，取值 `MON`–`SUN` |
| `screenshot_summary_schedule_time` | string | `"23:30"` | `"23:30"` | 日报任务触发时间，`HH:mm` 格式 |
| `screenshot_summary_webhook_enabled` | bool | `false` | 见备注 | 日报是否推送 webhook。**若配置了 `screenshot_summary_webhook_url`，代码兜底为 `true`**；否则回退看 `webhook_enabled` |
| `screenshot_summary_webhook_url` | string | `""` | `""` | 日报专用 webhook 地址；非空时优先于通用 `webhook_url` |
| `screenshot_summary_webhook_secret` | string | `""` | 回退到 `webhook_secret` | 日报 webhook 签名密钥（飞书机器人加签用） |

日报产物保存在 `client/runtime/summaries/screenshot-summary-<日期>.html/.json`。计划任务名为 `Flowtrace Screenshot Summary`，实际执行 `run_scheduled_job.py --job screenshot-summary`。

### AI provider

AI 调用全部走 **OpenAI 兼容 HTTP 协议**，不需要任何厂商 SDK。provider 名称完全自定义（`providers` 里的键名），每家自配 `base_url`；`wire_api` 支持 `"chat"`（`/chat/completions`，绝大多数厂商）和 `"responses"`（`/responses`，OpenAI 新协议）。

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 / 注意事项 |
|---|---|---|---|---|
| `ai_primary_provider` | string | `"dashscope"` | `""` | 主 provider 名，对应 `providers` 里的某个键 |
| `ai_fallback_providers` | array | `[]` | `[]` | 备用 provider 名列表，主 provider 失败时按序回退。兼容旧的单数字段 `ai_fallback_provider` |
| `ai_timeout_seconds` | int | `120` | `120` | 单次 AI 调用超时（秒），超时即视为失败并尝试下一个 provider |
| `providers.<名字>.base_url` | string | — | `""` | **必填**。OpenAI 兼容端点，如 DashScope 用 `https://dashscope.aliyuncs.com/compatible-mode/v1`，智谱用 `https://open.bigmodel.cn/api/paas/v4`，MiniMax 用 `https://api.minimax.chat/v1` |
| `providers.<名字>.api_key` | string | `""` | `""` | 该 provider 的密钥。**全部不填则 AI 日报完全不启用，无任何外发** |
| `providers.<名字>.wire_api` | string | `"chat"` | `"chat"` | 协议：`"chat"` = `/chat/completions`；`"responses"` = `/responses`（厂商支持才用） |
| `providers.<名字>.vision_model` | string | 视厂商 | `""` | 视觉模型（批量读图阶段）；**不配则该 provider 不能承担截图日报的读图环节** |
| `providers.<名字>.text_model` | string | 视厂商 | `""` | 文本模型（汇总阶段） |

### 通用 webhook（飞书等）

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 |
|---|---|---|---|---|
| `webhook_enabled` | bool | `false` | `false` | 通用 webhook 推送开关（日报未配置专用 URL 时生效） |
| `webhook_url` | string | `""` | `""` | 通用 webhook 地址；识别为飞书机器人地址（`open.feishu.cn`）时自动用卡片消息格式 |
| `webhook_secret` | string | `""` | `""` | webhook 签名密钥 |
| `webhook_timeout_seconds` | int | `15` | `15` | webhook 请求超时（秒） |
| `webhook_retry_count` | int | `2` | `2` | 失败重试次数 |
| `webhook_retry_delay_seconds` | number | `2` | `2.0` | 重试间隔（秒） |

### 计划任务运行器

| 字段 | 类型 | 模板示例值 | 代码兜底值 | 含义 / 注意事项 |
|---|---|---|---|---|
| `notification_log_retention_days` | int | `14` | `14` | 计划任务日志（`client/runtime/logs/`）保留天数；`<0` 会报错 |
| `job_lock_stale_seconds` | int | `43200` | `43200` | 任务锁文件超过该秒数视为陈旧锁，自动清除（防止上次异常退出卡死后续任务）；`<0` 会报错 |
| `automation_python_executable` | string | `""` | `""`（回退到 PATH 上的 `python`） | 注册计划任务时使用的 Python 解释器绝对路径。`python` 不在 PATH 时必须显式设置，否则注册失败 |

---

## 相关文档

- 部署模式与端口细节：[deployment.md](deployment.md)
- 配置背后的架构（推断/检测引擎如何使用这些参数）：[architecture.md](architecture.md)
- 数据核查 CLI（用到 `manage_default_days` 与 `inference.*`）：[manage-cli-guide.md](manage-cli-guide.md)
