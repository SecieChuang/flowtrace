"""Flowtrace · 数据核查 — 检测引擎与动作生成（V4：槽位约束）
纯模块，无 HTTP/会话依赖；供 server/manage_cli.py 使用。

检测流程（V4，槽位约束）：
  1. 构建活跃段（跨日连续）
  2. 为每个段填充槽位（段首要上班卡，段尾要下班卡）
  3. 槽位状态判定：
     - 距离 > 阈值 ≤ 1.5×阈值（SLOT_SEARCH_MULTIPLIER）→ slot_mismatch / *_far / low
     - 方向错 → slot_mismatch / *_wrong_dir / medium
     - 槽位空 → missing_checkin / *_empty / low
  4. 孤立卡判定：未被任何槽位收纳 → isolated / medium
"""
from datetime import datetime as _dt_cls, timedelta as _td_cls

# Import shifts engine
try:
    from server import shifts
except ImportError:
    import shifts


def _parse_dt(date_str, time_str):
    return _dt_cls.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")


def _gap_seconds(prev, curr):
    a = _parse_dt(prev['date'], prev['time'])
    b = _parse_dt(curr['date'], curr['time'])
    return int((b - a).total_seconds())


# ── 主检测函数 ─────────────────────────────────────────

def detect_issues(checkins, short_repeat_seconds=600, dedupe_seconds=60, db=None, threshold_minutes=None):
    """按槽位约束检测异常（新实现）

    Args:
        checkins: list[{id, action, date, time}] sorted by (date, time)
        short_repeat_seconds: DEPRECATED（兼容参数，不再使用）
        dedupe_seconds: DEPRECATED（兼容参数，不再使用）
        db: database connection
        threshold_minutes: 槽位搜索的正常距离上限（默认从配置读取）

    Returns:
        list of issue dicts with category: slot_mismatch / missing_checkin / isolated
    """
    if not checkins:
        return []

    if threshold_minutes is None:
        threshold_minutes = shifts.INFER_MERGE_GAP_MINUTES

    # 提取日期范围（±1 天缓冲）
    dates = sorted(set(c['date'] for c in checkins))
    if not dates:
        return []

    first_date = _dt_cls.strptime(dates[0], "%Y-%m-%d")
    last_date = _dt_cls.strptime(dates[-1], "%Y-%m-%d")
    date_range = [
        (first_date - _td_cls(days=1)).strftime("%Y-%m-%d"),
        *dates,
        (last_date + _td_cls(days=1)).strftime("%Y-%m-%d"),
    ]

    # 构建活跃段
    segments = shifts.build_active_segments(db, date_range, threshold_minutes)

    # 填充槽位
    slot_result = shifts.fill_segment_slots(segments, checkins, threshold_minutes)

    # 将 anomalies 转换成 manage_cli 期待的格式
    issues = []
    for anom in slot_result['anomalies']:
        issue = {
            'category': anom['category'],
            'severity': anom['severity'],
            'anchor_date': anom['anchor_date'],
        }

        if anom['category'] == 'slot_mismatch':
            # 双卡类
            primary = anom['primary']
            issue['id'] = f"{anom['category']}-{primary['id']}"
            issue['type_label'] = {
                'head_far': '打卡距段首较远',
                'tail_far': '打卡距段尾较远',
                'head_wrong_dir': '段首方向错误',
                'tail_wrong_dir': '段尾方向错误',
            }.get(anom['sub_kind'], '槽位不匹配')
            issue['primary'] = primary
            issue['secondary'] = None
            issue['kind'] = 'in' if 'head' in anom['sub_kind'] else 'out'
            issue['sub_kind'] = anom['sub_kind']
            issue['segment_index'] = anom.get('segment_index')

            if 'wrong_dir' in anom['sub_kind']:
                issue['target'] = primary
                issue['suggested_action'] = 'clock_out' if primary['action'] == 'clock_in' else 'clock_in'

        elif anom['category'] == 'missing_checkin':
            # 补卡类
            issue['id'] = f"{anom['category']}-{anom['anchor_date']}-{anom['sub_kind']}"
            issue['type_label'] = '缺少上班打卡' if 'head' in anom['sub_kind'] else '缺少下班打卡'
            issue['primary'] = None
            issue['kind'] = 'in' if 'head' in anom['sub_kind'] else 'out'
            issue['sub_kind'] = anom['sub_kind']
            issue['segment_index'] = anom.get('segment_index')
            issue['inferred_dt'] = anom.get('inferred_dt')

        elif anom['category'] == 'isolated':
            # 孤立卡
            primary = anom['primary']
            issue['id'] = f"isolated-{primary['id']}"
            issue['type_label'] = '孤立打卡'
            issue['primary'] = primary
            issue['kind'] = 'in' if primary['action'] == 'clock_in' else 'out'

        issues.append(issue)

    # 过滤缓冲区：只输出 anchor_date 在原始扫描区间内的 issue
    scan_start = dates[0]
    scan_end = dates[-1]
    filtered = [
        iss for iss in issues
        if scan_start <= iss['anchor_date'] <= scan_end
    ]

    # 按 (anchor_date desc, primary.time desc) 排序
    def sort_key(iss):
        date = iss['anchor_date']
        time = iss['primary']['time'] if iss.get('primary') else '00:00:00'
        return (date, time)

    filtered.sort(key=sort_key, reverse=True)
    return filtered


# ── 上下文构建 ─────────────────────────────────────────

def build_context(issue, db):
    """Return {day_checkins, day_activity} for the issue's anchor date."""
    date = issue["anchor_date"]
    highlight_ids = set()
    if issue.get("primary"):
        highlight_ids.add(issue["primary"]["id"])
    if issue.get("secondary"):
        highlight_ids.add(issue["secondary"]["id"])

    day_checkins = [
        {
            "id": r["id"],
            "date": r["date"],
            "time": r["time"],
            "action": r["action"],
            "highlight": r["id"] in highlight_ids,
        }
        for r in db.execute(
            "SELECT id, date, time, action FROM checkins WHERE date = ? ORDER BY time",
            (date,),
        ).fetchall()
    ]

    day_activity = [
        {"start_time": r["start_time"], "end_time": r["end_time"], "tags": r["tags"]}
        for r in db.execute(
            "SELECT start_time, end_time, tags FROM activity_slices WHERE date = ? ORDER BY start_time",
            (date,),
        ).fetchall()
    ]

    return {"day_checkins": day_checkins, "day_activity": day_activity}


# ── 动作生成 ───────────────────────────────────────────

def build_actions_for_slot_mismatch(issue, db):
    """slot_mismatch 的修复选项"""
    primary = issue['primary']
    sub_kind = issue.get('sub_kind', '')

    if 'wrong_dir' in sub_kind:
        # 方向错 → flip 推荐
        target = issue.get('target') or primary
        suggested = issue.get('suggested_action') or (
            'clock_out' if target['action'] == 'clock_in' else 'clock_in'
        )
        flip_label = '下班' if suggested == 'clock_out' else '上班'
        return [
            {
                'key': 'flip_direction',
                'label': f"将 {target['time']} 这条翻转为{flip_label}",
                'op': 'flip_action',
                'params': {'id': target['id'], 'action': suggested},
                'recommended': True,
            },
            {
                'key': 'delete',
                'label': f"删除 {target['time']} 这条",
                'op': 'delete',
                'params': {'id': target['id']},
            },
            {
                'key': 'edit_time',
                'label': '修改时间',
                'op': 'edit_time',
                'fixed_params': {'id': target['id']},
                'interactive_fields': [{'name': 'time', 'type': 'time'}],
            },
        ]
    else:
        # *_far → keep 推荐
        return [
            {
                'key': 'keep',
                'label': f"保留 {primary['time']} 这条（距离可接受）",
                'op': 'keep',
                'params': {},
                'recommended': True,
            },
            {
                'key': 'delete',
                'label': f"删除 {primary['time']} 这条",
                'op': 'delete',
                'params': {'id': primary['id']},
            },
            {
                'key': 'edit_time',
                'label': '修改时间',
                'op': 'edit_time',
                'fixed_params': {'id': primary['id']},
                'interactive_fields': [{'name': 'time', 'type': 'time'}],
            },
        ]


def build_actions_for_missing_checkin(issue, db):
    """missing_checkin 的修复选项"""
    sub_kind = issue.get('sub_kind', '')
    anchor_date = issue['anchor_date']
    inferred_dt = issue.get('inferred_dt')

    # kind 表示缺失槽位本身：head_empty/in 缺上班，tail_empty/out 缺下班。
    missing_action = 'clock_in' if issue.get('kind', 'in') == 'in' else 'clock_out'
    missing_label = '上班' if missing_action == 'clock_in' else '下班'

    actions = []
    if inferred_dt:
        actions.append({
            'key': 'add_inferred',
            'label': f"补一条 {inferred_dt.strftime('%H:%M:%S')} 的{missing_label}（推断值）",
            'op': 'add',
            'params': {
                'date': inferred_dt.strftime('%Y-%m-%d'),
                'time': inferred_dt.strftime('%H:%M:%S'),
                'action': missing_action,
            },
            'recommended': True,
        })

    actions.append({
        'key': 'add_manual',
        'label': f"手动补一条{missing_label}",
        'op': 'add',
        'fixed_params': {'date': anchor_date, 'action': missing_action},
        'interactive_fields': [{'name': 'time', 'type': 'time'}],
    })

    return actions


def build_actions_for_isolated(issue, db):
    """isolated 的修复选项（多选项）"""
    primary = issue['primary']
    kind = issue.get('kind', 'in' if primary['action'] == 'clock_in' else 'out')
    pair_action = 'clock_out' if kind == 'in' else 'clock_in'
    pair_label = '下班' if kind == 'in' else '上班'

    return [
        {
            'key': 'delete',
            'label': f"删除 {primary['time']} 这条",
            'op': 'delete',
            'params': {'id': primary['id']},
            'recommended': True,
        },
        {
            'key': 'add_pair_manual',
            'label': f"手动补一条{pair_label}",
            'op': 'add',
            'fixed_params': {'date': primary['date'], 'action': pair_action},
            'interactive_fields': [{'name': 'time', 'type': 'time'}],
        },
        {
            'key': 'edit_time',
            'label': '修改时间',
            'op': 'edit_time',
            'fixed_params': {'id': primary['id']},
            'interactive_fields': [{'name': 'time', 'type': 'time'}],
        },
        {
            'key': 'keep',
            'label': '保留（开会一整天等情况）',
            'op': 'keep',
            'params': {},
        },
    ]
