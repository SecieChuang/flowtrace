# FlowTrace 架构

> 本文描述当前代码的实际架构。检测框架的设计论证与槽位规则细节见 [clock-detection-architecture-final.md](clock-detection-architecture-final.md)，本文与之一致处只做引用，不重复展开。文中一律用函数名引用代码（不带行号，避免漂移）。

## 1. 系统总览

```
┌────────────────────────── Windows 工作站 ──────────────────────────┐
│  flowtrace.ahk (AutoHotkey v2) —— 唯一的采集运行时                  │
│    · 状态机 idle → warmup → active（物理键鼠空闲检测）               │
│    · 全局热键打卡（硬编码在脚本顶部）· 周期评分弹窗 · 定时截图        │
│    · 通过子进程调用 python sync.py <cmd> 与服务端通信                │
│           ↓                                                        │
│  client/src/sync.py —— 业务桥                                       │
│    · checkin / activity / rating / screenshot / flush               │
│    · 失败时写入离线队列 backup/sync_queue.jsonl，下次同步前 flush     │
└───────────────────────────────┬────────────────────────────────────┘
                                │ HTTP POST（X-API-Key 鉴权）
                                ▼
┌──────────────────────────── Docker / 直接运行 ─────────────────────┐
│  server/server.py (Flask)                                          │
│    写接口：/api/checkin /api/activity /api/activity/batch /api/rating│
│    读接口：/api/dashboard/{daily,weekly,history,table,stats,       │
│             current-duty}  /api/ping                               │
│      ↓                                                             │
│  SQLite：仓库根 data/flowtrace.db                                   │
│      checkins / activity_slices / ratings                          │
│      ↑                                                             │
│  server/shifts.py（活跃段+槽位引擎）· detection.py（检测）           │
│  manage_cli.py（交互式核查 CLI，容器内可 docker exec）               │
└───────────────────────────────┬────────────────────────────────────┘
                                │ GET /（静态前端）
                                ▼
                 server/static/：index.html + app.js + ECharts(CDN)
                 日视图 / 周视图 / 统计视图
```

关键事实：

- **服务端只有一份进程**：Docker 镜像内 gunicorn 固定 `-w 1`。这是日指标内存缓存（见 §5）一致性的前提。历史上多 worker 导致的缓存不一致事故见 [incident-2026-05-01-cache-inconsistency.md](incident-2026-05-01-cache-inconsistency.md)。
- **鉴权范围**：只有 4 个写接口挂 `@require_api_key`；`api_key` 为空时连写接口也不鉴权。dashboard 读接口始终不鉴权（见 [configuration.md](configuration.md) 的安全提示）。
- **双模式导入**：`server.py` / `detection.py` / `manage_cli.py` 都按 `try: from server import shifts / except ImportError: import shifts` 的方式兼容"包模式（本地开发）"与"平铺模式（Docker 镜像内所有 .py 同目录）"。manage_cli 通过 `_from_server` / `_from_detection` 动态取 server 模块内部函数，避免循环导入。
- **配置分层**：环境变量（`CONFIG_PATH` / `DATABASE_PATH` / `API_KEY`）> `server/config.json` > 代码兜底值。`shifts.py` 的配置是**惰性读取**的（`_server_inference_config()` 在运行时才取 server 模块的常量），避免 import 顺序导致配置静默失效。

## 2. 数据模型（SQLite）

`server/server.py` 的 `init_db()` 在模块加载时执行，建三张表：

### `checkins` — 打卡事件

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `action` | TEXT | `clock_in` / `clock_out` |
| `date` | TEXT | `YYYY-MM-DD` |
| `time` | TEXT | `HH:MM:SS` |
| `created_at` | TEXT | 默认 `datetime('now')` |

索引 `idx_checkins_date(date)`。

### `activity_slices` — 活动片段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | |
| `start_time` | TEXT | |
| `end_time` | TEXT NULL | NULL 表示进行中 |
| `duration_minutes` | REAL | |
| `tags` | TEXT | 逗号分隔字符串，默认 `auto`；含 `manual` 表示手动段 |
| `updated_at` | TEXT | |

唯一键 `UNIQUE(date, start_time)`，索引 `idx_activity_date(date)`。

### `ratings` — 自评分

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `date` / `time` | TEXT | |
| `value` | INTEGER | `CHECK(value BETWEEN 1 AND 5)` |
| `created_at` | TEXT | |

索引 `idx_ratings_date(date)`。

## 3. 写入路径

### 3.1 打卡 `api_checkin`

同一天同方向、与上一条间隔小于 `checkin_dedupe_seconds` 的打卡直接返回 `deduped: true` 并保留先到的那条（客户端连击防抖）。入库后调 `_invalidate_day_cache(date)`。

### 3.2 活动片段 `api_activity` / `api_activity_batch`

写入前由 `_prepare_activity_write` 统一处理：

- `_resolve_activity_date`：纠正"跨夜段误把次日 date 写进来"的常见错误（`end_time < start_time` 且前一天存在同 start_time 的未闭合段时，改写回前一天）；
- 跨零点拆分：`end_time < start_time` 的段拆成当天 `start → 24:00:00` 与次日 `00:00:00 → end` 两行；
- `_upsert_activity_slice`：按 `UNIQUE(date, start_time)` 幂等 upsert。

`update` 事件本质是带新 `end_time` 的 upsert，因此客户端断线重传不会制造重复行。

### 3.3 调和引擎 `reconcile_activity`

日视图展示前，把同一天的手动段（`tags` 含 `manual`）与自动段融合：

1. 手动闭合段直接合并；
2. 手动未闭合段（`end_time IS NULL`）视为从 start 起 `open_manual_clip_days` 天的占位区间；
3. 自动段按 `auto_merge_gap_minutes` 合并，被手动区间覆盖的部分裁掉；
4. 裁切后小于 `auto_min_fragment_minutes` 的碎片丢弃。

输出统一的片段序列（`tags` 标 `manual` / `auto`），供 `_extract_passive_ranges` 计算分钟域活跃区间。

## 4. 班次推断与槽位构造（V4）

引擎主体在 `server/shifts.py`，显示侧（server.py）与检测侧（detection.py）共用：

- `build_active_segments(db, dates, merge_gap_minutes)`：把活动片段按摸鱼阈值（`inference.merge_gap_minutes`，可按 `daypart_thresholds` 分时段细化）合并成**跨日连续**的活跃段；
- `fill_segment_slots(segments, checkins, threshold_minutes)`：每个活跃段有段首槽（期待上班卡）与段尾槽（期待下班卡）。槽位向外最多搜索 `SLOT_SEARCH_MULTIPLIER`（1.5）× 阈值、向内最多 `min(段长/2, 阈值)`；优先采信同向最近卡，没有同向卡时采信反向卡并标记方向错。

槽位规则、判定表与决策记录的完整描述见 [clock-detection-architecture-final.md](clock-detection-architecture-final.md) §1–§3。

显示侧入口是 `server.py` 的 `_build_shift_intervals(db, anchor_date)`：

- 段首段尾都有卡 → 在岗区间取两张卡的时间；
- 单边有卡 → 另一端用活跃段端点补推断卡（marks 上标 `inferred`）；
- 最后一个段当天进行中（段尾卡在实时窗口内未出现）→ 区间临时闭合到 `now`，只用于统计，不生成推断卡、不写库；
- 双边都空 → 该活跃段视为离岗活跃（远程/私人），不产出在岗区间；
- 槽位状态非正常的卡与孤立卡在 marks 上标 `anomaly`，前端据此画混乱标记。

`_build_manual_on_duty_ranges` 把区间 clip 到当天；当天**完全没有打卡**时按 `inference.enable_no_manual_day` 决定：默认（`false`）全天离岗，为 `true` 时把活跃段也计为在岗。

`_compose_timeline_segments` 把活跃区间与在岗区间的所有断点合成日视图的"状态河流"分段（`display_state` × `on_duty` × 透明度）。

## 5. 日指标缓存

`_build_day_metrics(db, date_str)` 计算单日全部指标（三口径分钟数、timeline、评分均值等），成本较高，因此有进程内缓存：

- `_day_metrics_cache`：普通字典，**只缓存历史日期**；`today` 每次实时计算，永不进缓存；
- 所有写接口与 manage_cli 的写助手在落库后调 `_invalidate_day_cache(date)` 精确失效对应日期；
- 缓存是**进程局部**的：只在 gunicorn 单 worker（当前 Dockerfile 固定 `-w 1`）下才一致。多 worker 会复活缓存不一致问题，禁止调高。

## 6. 检测引擎三类问题

`server/detection.py` 的 `detect_issues` 是纯函数式模块（无 HTTP 依赖），在同一套槽位引擎上做异常判定，输出三类问题：

| category | sub_kind | 界面文案 | 严重度 | 推荐修复 |
|---|---|---|---|---|
| `slot_mismatch` | `head_far` | 打卡距段首较远 | low | 保留（keep） |
| `slot_mismatch` | `tail_far` | 打卡距段尾较远 | low | 保留（keep） |
| `slot_mismatch` | `head_wrong_dir` | 段首方向错误 | medium | 翻转方向（flip） |
| `slot_mismatch` | `tail_wrong_dir` | 段尾方向错误 | medium | 翻转方向（flip） |
| `missing_checkin` | `head_empty` | 缺少上班打卡 | low | 按推断值补卡（add） |
| `missing_checkin` | `tail_empty` | 缺少下班打卡 | low | 按推断值补卡（add） |
| `isolated` | — | 孤立打卡 | medium | 删除（delete） |

- 实际执行的数据库写操作只有四种：`delete` / `add` / `edit_time` / `flip_action`（`keep` 是"不修复"标记，无写路径），由 `manage_cli.py` 的 `execute_op` 分发；
- 扫描区间为查询区间 ±1 天缓冲，缓冲区只参与活跃段构造与槽位搜索，不输出 issue；
- 设计铁律：**打卡是事实信号，算法只建议不否决**——所有修改都经用户在 CLI 中确认后才批量执行。

交互流程与操作选项详见 [manage-cli-guide.md](manage-cli-guide.md)。

## 7. 仪表盘数据流

前端是 `server/static/` 下的原生 JS + ECharts（CDN 加载），三个视图：

- **日视图**：`GET /api/dashboard/daily?date=…` → `_build_day_metrics` → 状态河流图（timeline_segments）+ 打卡标记（含推断/异常标记）+ 三口径工时 + 评分；
- **周视图**：`GET /api/dashboard/weekly?…` → 一周 7 天的日指标 + 汇总。周固定**周一起始**（服务端常量 `WEEK_START_DAY`，前后端一致，不可配置）；
- **统计视图**：`GET /api/dashboard/history?days=30`（N 天序列，clamp 到 [7, 365]）与 `GET /api/dashboard/stats`（早起冠军/深夜战神/专注巅峰/黄金小时等趣味统计）驱动 30 天趋势（柱色按专注率分档）、上下班画像、周目标达成（柱 = 周时长按专注率染色，线 = 周均自评分）、有效工作日历等图表。

性能特征：`weekly` / `history` / `stats` 等接口按天循环调 `_get_day_metrics`，历史日期命中缓存后是字典查找，首次冷读才逐日计算。

`add_cache_headers` 给所有 `/api/*` 与静态 HTML/JS/CSS 加强 no-cache 响应头，避免浏览器缓存旧前端。

## 8. 客户端结构

- `client/scripts/flowtrace.ahk`：采集运行时。状态机 `idle → warmup → active`；进 active 发 `activity start`，活跃期间按 `sync_interval_seconds` 发 `activity update`，转空闲发 `activity end`；热键触发 `checkin`；活跃累计 `rating_trigger_minutes` 弹评分窗。打卡通过 `sync.py --result-file` 拿回执并弹 toast。
- `client/src/sync.py`：唯一的服务端通信入口。所有 POST 失败时先入离线队列再返回，保证热键操作不丢。
- `client/src/offline_queue.py`：JSONL 队列，append 单行天然原子；flush 时先把队列文件原子改名再重放，期间新事件写入新主文件，失败事件追加回主文件——无锁并发安全。
- `client/src/screenshotter.py`：定时截屏（可选摄像头）到 `client/screenshots/<日期>/`，按保留期清理。
- `client/src/screenshot_summary.py`：日报三段式——视觉模型分批读图 → 文本模型汇总 → Python 模板渲染报纸式 HTML；结果存 `client/runtime/summaries/`，可选 webhook 推送（飞书机器人自动识别卡片格式）。
- `client/src/ai_provider.py`：多 provider 统一调用层（dashscope / zhipu / minimax / mistralai），主备回退 + 超时控制；**任何 provider 的 api_key 不配置，AI 功能整体不启用**。
- `client/src/run_scheduled_job.py`：计划任务包装器，带锁文件（防重入 + 陈旧锁回收）、历史记录与日志保留。

## 相关文档

- 配置字段与默认值：[configuration.md](configuration.md)
- 部署与运维：[deployment.md](deployment.md)
- 检测框架设计定稿：[clock-detection-architecture-final.md](clock-detection-architecture-final.md)
- 数据核查 CLI：[manage-cli-guide.md](manage-cli-guide.md)
