# 打卡检测框架架构设计（最终版）

> **状态**：设计定稿 rev.4（活跃段锚定 + 槽位约束）  
> **范围**：`server/detection.py`（检测）、`server/server.py`（显示）、`server/manage_cli.py`（CLI）  
> **核心原则**：活跃轴绝对可信；在岗轴由打卡声明；槽位约束判定异常  
> **日期**：2026-08-28

---

## 1. 设计前提与不变式

### 1.1 两轴四象限模型

用户的工作状态有两个正交维度：

| | 活跃（电脑有键鼠输入） | 不活跃（无输入） |
|---|---|---|
| **在岗**（已打上班卡） | 正常工作 | 开会/离线工作/摸鱼 |
| **不在岗**（未打卡或已打下班） | 远程/私人活跃 | 离开 |

**绝对可信轴**：活跃轴。`activity_slices` 由客户端 `A_TimeIdlePhysical`（物理键鼠空闲时长）驱动，挂机不产生活跃，远程会话不产生活跃。活跃 = 真人在本机操作，这是 ground truth。

**声明轴**：在岗轴。由手动打卡声明，可能漏打、误打、打错方向。

**四个象限都合法**。检测的目标不是用活跃去证伪打卡（那会把"在岗但不活跃"判成错误），而是检查**打卡意图的完整性**。

### 1.2 核心不变式（槽位约束）

**活跃段 = 由活动记录按摸鱼阈值合并出的连续工作区间。** 每个活跃段有两个槽位：

- **段首槽**：期待一张上班卡
- **段尾槽**：期待一张下班卡

**槽位约束**：一个活跃段的两个槽位，要么都空（你没打算记录这段 → 远程/私人，合法），要么都满且方向正确（记录完整，合法），**只填一半或方向错 → 打卡意图不完整，触发检测**。

这是整个检测框架的充要触发条件。

### 1.3 关键配置

| 配置键 | 当前生产值 | 用途 |
|---|---|---|
| `inference.merge_gap_minutes` | 40 | 活跃段合并的摸鱼容忍（分钟），段间空隙 ≤ 此值视为同一段 |
| `inference.daypart_thresholds.*` | 全 40 | 分时段摸鱼阈值（morning/afternoon/evening/night），当前未启用分段 |
| `manage_default_days` | 30 | CLI 默认扫描天数（扁平字段，非嵌套） |
| `checkin_dedupe_seconds` | 60 | API 入库去重阈值（检测侧不使用） |

**废除配置**（新设计不再需要）：
- `manage_short_repeat_seconds`（600s 同向分流阈值）

**仍在使用的配置**：
- `inference.enable_no_manual_day`（默认 `false`）：当天完全无打卡时，`false` = 全天离岗；`true` = 把当天活跃段也计为在岗。显示侧 `_build_day_metrics` 仍在读取，并非无效字段

**槽位搜索参数**（硬编码或新增配置）：
- 正常距离上限：摸鱼阈值（40 分钟）
- 保留推荐距离上限：1.5 × 摸鱼阈值（60 分钟）
- 段内搜索范围：`min(段长/2, 阈值)`

---

## 2. 检测算法

### 2.1 输入准备

1. **构建活跃段**
   - 查询 `activity_slices`，扫描区间 ±1 天（缓冲，避免跨边界段被截断）
   - 按摸鱼阈值 `merge_gap_minutes` 合并相邻活动片段 → `活跃段列表 [(start_dt, end_dt), ...]`
   - 活跃段构造完全无视日期边界，前后两天的活动可以合并为一个段

2. **提取打卡事件**
   - 查询 `checkins`，同样 ±1 天
   - 排序：`(date, time, 同秒上班先于下班)`

3. **标记进行中的段**
   - 活跃段列表最后一个段，若 `end_dt == now` 且 `date == 今天` → 标记为 `in_progress`
   - 进行中的段尾槽合法为空，不报缺卡

### 2.2 槽位填充

对每个活跃段：

#### 段首槽（要上班卡）

**搜索范围**：`[本段 start_dt − 1.5×阈值, 本段 start_dt + min(段长/2, 阈值)]`

- 向外最多搜索 `1.5×阈值`，向内最多搜索 `min(段长/2, 阈值)`；不使用相邻活跃段起止点放大窗口。
- 窗口内先排除反向卡，在同向卡中按距离最近采信；没有同向卡时才采信最近反向卡，并按槽位方向默认翻转。

**判定**：

| 情况 | 距离 | 判定 | 诊断 | 推荐操作 |
|---|---|---|---|---|
| 找到上班卡 | ≤ 阈值 | 正常 | — | — |
| 找到上班卡 | 阈值 < d ≤ 1.5×阈值 | 异常，推荐保留 | `slot_mismatch` / `head_far` / low | keep（推荐）/ delete / edit_time |
| 找到下班卡 | 任意 | 方向错 | `slot_mismatch` / `head_wrong_dir` / medium | flip（推荐）/ delete / edit_time |
| 未找到 | — | 槽位空 | `missing_checkin` / `head_empty` / low | add @start_dt（推荐）/ add 手动 |

#### 段尾槽（要下班卡）

**搜索范围**：`[本段 end_dt − min(段长/2, 阈值), 本段 end_dt + 1.5×阈值]`

- 向外最多搜索 `1.5×阈值`，向内最多搜索 `min(段长/2, 阈值)`；不跨越长空档或跨日吸收下一段打卡。
- 窗口内先排除反向卡，在同向卡中按距离最近采信；没有同向卡时才采信最近反向卡，并按槽位方向默认翻转。
- 若本段标记为 `in_progress`，跳过尾槽判定

**判定**（完全对称）：

| 情况 | 距离 | 判定 | 诊断 | 推荐操作 |
|---|---|---|---|---|
| 找到下班卡 | ≤ 阈值 | 正常 | — | — |
| 找到下班卡 | 阈值 < d ≤ 1.5×阈值 | 异常，推荐保留 | `slot_mismatch` / `tail_far` / low | keep（推荐）/ delete / edit_time |
| 找到上班卡 | 任意 | 方向错 | `slot_mismatch` / `tail_wrong_dir` / medium | flip（推荐）/ delete / edit_time |
| 未找到 | — | 槽位空 | `missing_checkin` / `tail_empty` / low | add @end_dt（推荐）/ add 手动 |

#### 中点重合处理

若一张卡恰好落在 `start_dt + min(段长/2, 阈值)` 或 `end_dt − min(段长/2, 阈值)` 上：
- 上班卡 → 归段首槽搜索
- 下班卡 → 归段尾槽搜索

### 2.3 孤立卡判定

槽位填充结束后，扫描所有打卡：

- 未被任何段的槽位收纳 → `isolated` / medium
- 推荐操作：delete（推荐）/ add 手动配对卡 / edit_time / keep

**特殊情况**：今天有打卡但无活跃段 → 所有打卡都判孤立 → 这正是"打完卡去开会一整天"的情形，推荐 keep + 提供 delete/edit 选项。

### 2.4 输出过滤

- 丢弃 `anchor_date ∉ [from, to]` 的 issue（缓冲区的 ±1 天只参与段构造和搜索，不输出）
- 按 `(anchor_date desc, primary.time desc)` 排序

---

## 3. 诊断分类

| 类别 | 子类 | 严重度 | 触发条件 | 推荐操作 | 其他选项 |
|---|---|---|---|---|---|
| **slot_mismatch** | `head_far` | low | 段首槽找到上班卡，距离 > 阈值 ≤ 1.5×阈值 | keep | delete / edit_time |
| | `tail_far` | low | 段尾槽找到下班卡，距离 > 阈值 ≤ 1.5×阈值 | keep | delete / edit_time |
| | `head_wrong_dir` | medium | 段首槽找到的是下班卡 | flip | delete / edit_time |
| | `tail_wrong_dir` | medium | 段尾槽找到的是上班卡 | flip | delete / edit_time |
| **missing_checkin** | `head_empty` | low | 段首槽空 | add @start_dt | add 手动 |
| | `tail_empty` | low | 段尾槽空（段非 in_progress） | add @end_dt | add 手动 |
| **isolated** | — | medium | 打卡未被任何槽位收纳 | delete | add 手动 / edit_time / keep |

**与旧设计的对应关系**（理解用）：

- 旧 `dup` → 段首槽找到两张卡，留最近的，另一张变孤立
- 旧 `reversed` → `*_wrong_dir`
- 旧 `missing_out` / `missing_in` → `*_empty`
- 旧 `chaos` → 极短间隔的两张卡，至少有一张变孤立（另一张可能被槽位收纳）
- 旧 `consecutive` → 废除

**修复动作只有四种**：delete / add / flip / edit_time，无新增写路径。

---

## 4. 修复选项充分性

| 真相 | delete | add | flip | edit_time | keep |
|---|---|---|---|---|---|
| 多打了 | ✓ | — | — | — | — |
| 漏打了 | — | ✓ | — | — | — |
| 方向打反 | — | — | ✓ | — | — |
| 时间打早/打晚（但方向对、距离可接受） | — | — | — | ✓ | ✓ |
| 时间严重偏离 | ✓ 删掉重打 | ✓ 补正确的 | — | ✓ | — |
| 开会一整天（无活跃但打卡正确） | — | — | — | — | ✓ |

**充分性论证**：每条诊断的选项列表覆盖该诊断所有可能的真相。`keep` 只出现在"距离可疑但不致命"的诊断中，防止破坏合法的"在岗但不活跃"场景。

---

## 5. 前端显示逻辑

### 5.1 在岗区间的构造（替代现行的 `_build_shift_intervals`）

1. 按 §2.1–2.3 构建活跃段、填槽、标记异常
2. 对每个段：
   - 段首槽填满 + 段尾槽填满（含 in_progress） → 区间 = `[首卡时间, 尾卡时间]`
   - 段首空 + 段尾满 → 区间 = `[start_dt, 尾卡时间]`，首卡标记 `(推断)`
   - 段首满 + 段尾空（非 in_progress） → 区间 = `[首卡时间, end_dt]`，尾卡标记 `(推断)`
   - 双边都空 → 不产出区间（远程/私人活跃）
   - in_progress 且只有首卡 → 区间 = `[首卡时间, now]`，不标记推断
3. 异常卡（距离超阈值 / 方向错 / 孤立）在 marks 上挂 `anomaly: true`
4. 前端 `groupCheckinMarks` 改为：`mark.anomaly` → 画混乱emoji；`mark.inferred` → 画推断标记

### 5.2 与现行显示的区别

| 场景 | 现行行为 | 新行为 |
|---|---|---|
| `上09 / 上14`，活动 09–18 | 强制闭合 09–14，14:00 推断下班+手动上班同秒 → 混乱emoji | 段 [09,18]，14:00 上班卡孤立 → 混乱emoji，区间 09–18 不断开 |
| `上09`，活动 11–18，开会两小时 | 09–18 连续区间（推断 18:00 下班） | 段 [11,18]，09:00 卡距段首 2h > 阈值 → `head_far` / 推荐 keep，区间仍算 09–18 |
| 今天上班卡未闭合 | 区间延伸到 now，不报错 | 段尾 in_progress，尾槽合法空，区间到 now |
| `上09 / 下18`，活动 09–18，正常 | 区间 09–18 | 同左，槽位正常填满 |

**关键变化**：
- 不再有"强制闭合导致的同秒推断+手动对"
- 混乱emoji 由服务端打 `anomaly` 标记驱动，前端的 ±10 分钟启发式删掉
- 开会等"在岗但不活跃"场景的区间正确显示，不会被截断到活动开始时刻

### 5.3 统计数字变化

所有依赖 `_build_shift_intervals` 的统计（`_build_day_metrics` / `current-duty` / 导出）都会重算。需要对最近 N 天跑新旧口径对照报告，人工确认差异可接受后再切换。

---

## 6. 实施路径

### Phase 1：引擎重构（显示侧）

1. 抽取 `server/shifts.py`
   - 迁入活跃段构造：`_extract_passive_windows_by_date` 及其依赖闭包
   - 迁入摸鱼阈值：`_gap_allowance_for_minute` / `_merge_ranges_by_gap` / `INFER_*` 常量
   - 新增 `build_active_segments(db, dates, merge_gap_minutes)` → 返回段列表
   - 新增 `fill_segment_slots(segments, checkins, threshold_minutes)` → 返回槽位填充结果 + 异常列表

2. 重写 `_build_shift_intervals`
   - 调用 `build_active_segments` + `fill_segment_slots`
   - 按 §5.1 构造区间和 marks
   - 保持签名与返回值格式不变（调用方无感）

3. 兼容 Docker 平铺部署
   - `shifts.py` 加入 `server/Dockerfile` 的 COPY 列表
   - 双模式导入：`try: from server import shifts except: import shifts`

### Phase 2：验证显示零回归 + 对照报告

1. 新增 `server/tests/test_display_parity.py`
   - 对 5 个典型场景（正常配对、开会两小时、双上班卡、今天未闭合、无打卡远程日）固化旧 `_build_shift_intervals` 输出
   - 跑新实现，对比区间列表 + marks
   - 预期：除"强制闭合导致的同秒对"外其他一致

2. 新增 `scripts/compare_metrics.py`
   - 连生产库，查最近 30 天的 checkins + activity_slices
   - 新旧实现各跑一遍，输出每天的在岗小时数差异
   - 人工审查差异，确认符合预期（主要是开会场景的工时从活动起点变成打卡时间）

### Phase 3：检测侧重写

1. `detection.py` 重写 `detect_issues`
   - 调用 `shifts.build_active_segments` + `shifts.fill_segment_slots`
   - 按 §2.2–2.4 实现槽位判定 + 孤立卡扫描
   - 输出格式：保留 `id` / `category` / `sub_kind` / `severity` / `primary` / `secondary` / `anchor_date` / `gap_seconds` / `kind` / `target` / `suggested_action`
   - 删除 `_pre_dedupe` / `_is_dup_pair` / `_check_reversed` / `_infer_pair_dt` / `consecutive` 类别

2. 按 §7 改写测试用例（见下）

### Phase 4：CLI 与文档同步

1. `manage_cli.py`
   - 查询区间改为 `date BETWEEN from-1 AND to+1`，过滤 `anchor_date ∈ [from, to]`
   - category 分发：删除 `consecutive` / `isolated` 分支，接入 `slot_mismatch` / `missing_checkin` / 新 `isolated`
   - 动作构建器：
     - `build_actions_for_slot_mismatch`：按 sub_kind 分发（far → keep★ / wrong_dir → flip★）
     - `build_actions_for_missing_checkin`：add★ / add 手动
     - `build_actions_for_isolated`：delete★ / add 手动 / edit_time / keep
   - 修复后自动重扫

2. 文档更新
   - `docs/manage-cli-guide.md`：类别表、配置段
   - `server/config_example.json`：删除 `manage_short_repeat_seconds`（`enable_no_manual_day` 仍在使用，不删除）

### Phase 5：前端适配

1. `server/static/app.js`
   - 删除 `groupCheckinMarks` 的 ±10 分钟配对逻辑
   - 改为：`mark.anomaly` → 画混乱emoji；`mark.inferred` → 画推断标记（灰色或虚线）
   - 可选：dashboard 顶部显示 `GET /api/checkins/issues` 返回的异常总数

2. API 兼容
   - `/api/dashboard/table` 的 shifts 字段格式不变，前端无需改动
   - `/api/dashboard/current-duty` 同理

### Phase 6：生产部署

1. 本地跑完整测试套件（**仅跑测试，不在本地构建或重启服务**）
2. 只读干跑 `python manage_cli.py --list --days 30` 连生产库，人工抽查输出无批量误报
3. 合并主分支，生产侧部署
4. 部署后人工检查 dashboard 的区间显示 + 混乱emoji + 推断标记

---

## 7. 测试影响

### 7.1 保留（行为完全不变）

- 正常配对：`上09 / 下18`，活动 09–18 → 无 issue
- 跨天夜班：`上23 / 下01`，活动 23–01 → 无 issue
- 空数据库返回空
- 今天未闭合上班卡 → 不报（in_progress）

### 7.2 改写（预期变化，骨架可复用）

| 旧用例 | 旧预期 | 新预期 |
|---|---|---|
| `test_double_tap`（`上09:00 / 上09:00`） | `dup` / delete 较晚 | 段首槽留第一张，第二张 `isolated` / delete |
| `test_within_dedupe_window_ignored` | 静默去重 | 两张都判，至少一张孤立 |
| `test_reversed_*` | `reversed` / flip（限跨天） | `*_wrong_dir` / flip（不限跨天） |
| `test_consecutive_*` | `consecutive` / delete 第二条 | 按活动分：有断点 → `*_empty` / add；无断点 → 第二张孤立 / delete |
| `test_isolated_*` | `isolated` / 按推断分 low/medium | 同 `isolated`，但判定改为"未被槽位收纳" |

### 7.3 新增

1. **槽位填充专项**
   - `test_slot_head_far`：`上07 / 下18`，活动 09–18 → `head_far` / 推荐 keep
   - `test_slot_head_wrong_dir`：`下09 / 下18`，活动 09–18 → `head_wrong_dir` / 推荐 flip
   - `test_slot_tail_far` / `test_slot_tail_wrong_dir`：对称场景
   - `test_slot_both_empty`：活动 09–18，无打卡 → 不报（远程/私人）
   - `test_slot_midpoint_collision`：卡恰好在 `start + min(len/2, 1.5×阈值)` → 按方向归属

2. **开会场景**
   - `test_meeting_morning`：`上09 / 下18`，活动 11–18 → `head_far` / keep，区间显示 09–18

3. **孤立卡**
   - `test_isolated_in_gap`：`上09 / 上14 / 下18`，活动 09–12 + 14–18 → 14:00 孤立

4. **显示对照**
   - `test_display_parity_no_force_close`：`上09 / 上14`，旧输出有同秒 14:00 推断+手动对，新输出 14:00 孤立

5. **扫描边界**
   - `test_buffer_zone`：首日 from−1 的打卡参与槽位搜索但不输出 issue

6. **无活动数据护栏**
   - `test_no_activity_no_detection`：某天有打卡但 activity_slices 完全空 → 所有打卡判孤立 / 推荐 keep（见 §2.3）

---

## 8. 关键决策记录

| 议题 | 决策 | 理由 |
|---|---|---|
| 检测触发条件 | 槽位约束（双边都空 or 都满 → 合法；只填一半或方向错 → 异常） | 尊重四象限都合法，不用活跃证伪打卡，只检查打卡意图完整性 |
| 锚定对象 | 活跃段（由绝对可信的活动数据构造） | 活跃轴是 ground truth，检验嫌疑数据必须锚在可信数据上 |
| 槽位搜索范围 | 向外 ≤ 1.5×阈值；向内 ≤ min(段长/2, 阈值)；同向先做最大匹配、再以反向卡兜底 | 不跨长空档；共享卡优先满足唯一可配槽位；反向卡默认翻转后仍参与显示和统计 |
| 开会场景处理 | 距离 > 阈值 ≤ 1.5×阈值 时推荐 keep，前端显示采纳，区间延伸到打卡时间 | 信打卡而非信活跃，保护真实工时 |
| 孤立卡多选项 | delete（推荐）/ add 手动 / edit_time / keep | 孤立是最可能出问题的情形，提供充分选项而非只给 delete |
| 段内切段 | 不支持 | 活跃段就是活跃段，由摸鱼阈值完全决定，不因打卡改变 |
| 跨天特殊逻辑 | 废除 | 活跃段构造完全无视日期边界，分日只发生在渲染和统计 |
| 强制闭合 | 废除 | 新模型按槽位构造区间，不存在"推断失败就强制闭合"的分支 |
| 混乱emoji 触发 | 服务端打 `anomaly` 标记，前端读标记 | 前端启发式删掉，检测与显示解耦 |
| 统计数字变化 | 人工审查后接受 | 新算法更尊重打卡声明（开会等场景工时从活动起点变为打卡时间），差异合理 |
| 旧类别映射 | dup→孤立 / reversed→wrong_dir / missing→empty / chaos→孤立 / consecutive 废除 | 新分类以槽位状态为唯一判据，旧类别是实现细节不是本质 |

---

## 9. 配置示例

```json
{
  "api_key": "...",
  "checkin_dedupe_seconds": 60,
  "manage_default_days": 30,
  "inference": {
    "merge_gap_minutes": 40,
    "daypart_thresholds": {
      "morning": 40,
      "afternoon": 40,
      "evening": 40,
      "night": 40
    },
    "enable_no_manual_day": false
  }
}
```

**变更**：删除 `manage_short_repeat_seconds`；`enable_no_manual_day` 保留且仍在使用（见 §1.3，控制无打卡日是否按活跃计在岗）。

---

## 10. 修复选项速查表

```
slot_mismatch / head_far       → [keep★] [delete] [edit_time]
slot_mismatch / tail_far       → [keep★] [delete] [edit_time]
slot_mismatch / head_wrong_dir → [flip★] [delete] [edit_time]
slot_mismatch / tail_wrong_dir → [flip★] [delete] [edit_time]
missing_checkin / head_empty   → [add @start_dt★] [add 手动]
missing_checkin / tail_empty   → [add @end_dt★] [add 手动]
isolated                       → [delete★] [add 手动] [edit_time] [keep]

（★ = 推荐操作）
```

执行 op 仍只有 `delete` / `add` / `flip` / `edit_time` 四种，`keep` 是"不修复"而非新写路径。

---

**设计定稿完成，可以开始实施。**
