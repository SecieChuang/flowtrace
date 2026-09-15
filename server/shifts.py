"""Flowtrace · 班次推断引擎
共享模块：活跃段构造 + 槽位填充 + 在岗区间推断
供 server.py (显示) 和 detection.py (检测) 共用
"""
from datetime import datetime, timedelta
import re


# ── 配置读取（惰性）────────────────────────────────────
# 不在 import 时读取 server 配置：gunicorn 等加载顺序下 server 模块可能尚未
# 初始化完成，过早读取会触发 AttributeError 并被静默吞掉，导致
# inference.daypart_thresholds 等配置失效。改为在使用处运行时读取。

_DEFAULT_MERGE_GAP_MINUTES = 40
_DEFAULT_DAYPART_THRESHOLDS = {}


def _server_inference_config():
    """运行时从 server 模块读取已初始化的推断配置；读不到则返回 None。"""
    try:
        try:
            from server import server as srv
        except ImportError:
            # Flat deployment mode
            import server as srv
    except Exception:
        return None
    merge_gap = getattr(srv, 'INFER_MERGE_GAP_MINUTES', None)
    daypart = getattr(srv, 'INFER_DAYPART_THRESHOLDS', None)
    if merge_gap is None or daypart is None:
        return None
    return merge_gap, daypart


def _merge_gap_minutes():
    cfg = _server_inference_config()
    return cfg[0] if cfg else _DEFAULT_MERGE_GAP_MINUTES


def _daypart_thresholds():
    cfg = _server_inference_config()
    return cfg[1] if cfg else _DEFAULT_DAYPART_THRESHOLDS


def __getattr__(name):
    # 惰性兼容外部对 shifts.INFER_MERGE_GAP_MINUTES / INFER_DAYPART_THRESHOLDS
    # 的读取（如 detection.py），取值时机推迟到属性访问时。
    if name == 'INFER_MERGE_GAP_MINUTES':
        return _merge_gap_minutes()
    if name == 'INFER_DAYPART_THRESHOLDS':
        return _daypart_thresholds()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


SLOT_SEARCH_MULTIPLIER = 1.5


# ── 工具函数 ──────────────────────────────────────────

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _parse_time_to_min(time_str):
    if not time_str:
        return None
    try:
        parts = [int(x) for x in str(time_str).split(":")]
        if len(parts) < 2:
            return None
        if parts[0] == 24:
            return 24 * 60
        return parts[0] * 60 + parts[1]
    except Exception:
        return None


def _parse_tags(tags_str):
    if not tags_str:
        return []
    return [t.strip() for t in str(tags_str).split(",") if t.strip()]


def _normalize_ranges(ranges):
    cleaned = []
    for start, end in ranges:
        if start is None or end is None:
            continue
        s = max(0, min(24 * 60, int(start)))
        e = max(0, min(24 * 60, int(end)))
        if e > s:
            cleaned.append((s, e))
    if not cleaned:
        return []
    cleaned.sort(key=lambda x: x[0])
    merged = [cleaned[0]]
    for s, e in cleaned[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _gap_allowance_for_minute(minute):
    minute = max(0, min(24 * 60 - 1, int(minute)))
    hour = minute // 60
    if hour < 6:
        key = "night"
    elif hour < 12:
        key = "morning"
    elif hour < 18:
        key = "afternoon"
    else:
        key = "evening"
    merge_gap = _merge_gap_minutes()
    return _safe_int(_daypart_thresholds().get(key, merge_gap), merge_gap)


def _merge_ranges_by_gap(ranges):
    """合并相邻活动段，空隙 ≤ 摸鱼阈值视为连续"""
    base = _normalize_ranges(ranges)
    if not base:
        return []

    merged = [base[0]]
    for start, end in base[1:]:
        last_s, last_e = merged[-1]
        if start - last_e <= _gap_allowance_for_minute(last_e):
            merged[-1] = (last_s, max(last_e, end))
        else:
            merged.append((start, end))
    return merged


def _dt(date_str, time_str):
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")


# ── 活动片段提取 ─────────────────────────────────────────

def _segment_to_minute_range(date_str, seg, hard_limit_min=None):
    start_min = _parse_time_to_min(seg.get("start_time"))
    if start_min is None:
        return None

    end_time = seg.get("end_time")
    end_min = _parse_time_to_min(end_time)
    synthetic_today_end = False
    if end_min is None:
        if date_str == datetime.now().strftime("%Y-%m-%d"):
            now = datetime.now()
            end_min = now.hour * 60 + now.minute
            synthetic_today_end = True
            if hard_limit_min is not None:
                end_min = min(end_min, hard_limit_min)
        else:
            return None

    end_date = seg.get("end_date") or seg.get("date") or date_str
    if end_date > date_str:
        end_min += 24 * 60
    elif end_min <= start_min:
        if synthetic_today_end:
            return None
        end_min += 24 * 60

    start_clip = max(0, min(24 * 60, start_min))
    end_clip = max(0, min(24 * 60, end_min))
    if end_clip <= start_clip:
        return None
    return (start_clip, end_clip)


def _extract_passive_ranges(date_str, activity_rows, hard_limit_min=None):
    ranges = []
    tag_set = set()
    closed_count = 0

    for seg in activity_rows:
        tags = _parse_tags(seg.get("tags", ""))
        for tag in tags:
            if tag:
                tag_set.add(tag)

        if "manual" in tags:
            continue

        minute_range = _segment_to_minute_range(date_str, seg, hard_limit_min=hard_limit_min)
        if not minute_range:
            continue

        ranges.append(minute_range)
        if seg.get("end_time"):
            closed_count += 1

    return _normalize_ranges(ranges), sorted(tag_set), closed_count


def _extract_passive_windows_by_date(db, dates):
    if not dates:
        return {}

    placeholders = ",".join("?" for _ in dates)
    rows = db.execute(
        f"SELECT date, start_time, end_time, tags FROM activity_slices WHERE date IN ({placeholders}) ORDER BY date, start_time",
        tuple(dates),
    ).fetchall()

    per_day = {date_str: [] for date_str in dates}
    for row in rows:
        per_day.setdefault(row["date"], []).append(dict(row))

    result = {}
    for date_str in dates:
        passive_ranges, _, _ = _extract_passive_ranges(date_str, per_day.get(date_str, []))
        result[date_str] = passive_ranges
    return result


def _iter_passive_ranges_abs(date_str, passive_ranges):
    day_start = datetime.strptime(date_str, "%Y-%m-%d")
    for start_min, end_min in passive_ranges:
        start_dt = day_start + timedelta(minutes=start_min)
        end_dt = day_start + timedelta(minutes=end_min)
        if end_dt > start_dt:
            yield start_dt, end_dt


def _merge_absolute_ranges(ranges):
    cleaned = []
    for start_dt, end_dt in ranges:
        if not start_dt or not end_dt or end_dt <= start_dt:
            continue
        cleaned.append((start_dt, end_dt))

    if not cleaned:
        return []

    cleaned.sort(key=lambda x: x[0])
    merged = [cleaned[0]]
    for start_dt, end_dt in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start_dt <= last_end:
            merged[-1] = (last_start, max(last_end, end_dt))
        else:
            merged.append((start_dt, end_dt))
    return merged


# ── 推断函数（旧实现保留用于兼容）────────────────────────

def _infer_pending_clock_in_abs(clock_out_dt, passive_ranges_abs, floor_dt=None):
    """向前（倒推）推断上班时间"""
    applicable = []
    for start_dt, end_dt in passive_ranges_abs:
        if start_dt >= clock_out_dt:
            continue
        if floor_dt and end_dt <= floor_dt:
            continue
        clipped_start = max(start_dt, floor_dt) if floor_dt else start_dt
        clipped_end = min(end_dt, clock_out_dt)
        if clipped_end > clipped_start:
            applicable.append((clipped_start, clipped_end))

    if not applicable:
        return None

    applicable.sort(key=lambda x: x[0], reverse=True)
    latest_start, latest_end = applicable[0]
    gap_minutes = int((clock_out_dt - latest_end).total_seconds() // 60)
    if gap_minutes > _gap_allowance_for_minute(latest_end.hour * 60 + latest_end.minute):
        return None

    inferred_start = latest_start
    for cur_start, cur_end in applicable[1:]:
        if cur_end >= inferred_start:
            inferred_start = min(inferred_start, cur_start)
            continue
        gap_limit = _gap_allowance_for_minute(cur_end.hour * 60 + cur_end.minute)
        gap_minutes = int((inferred_start - cur_end).total_seconds() // 60)
        if gap_minutes <= gap_limit:
            inferred_start = min(inferred_start, cur_start)
        else:
            break

    return inferred_start


def _infer_pending_clock_out_abs(pending_start_dt, passive_ranges_abs, hard_limit_dt=None):
    """向后推断下班时间"""
    candidates = []
    for start_dt, end_dt in passive_ranges_abs:
        if end_dt <= pending_start_dt:
            continue
        if hard_limit_dt and start_dt >= hard_limit_dt:
            continue
        clipped_start = max(start_dt, pending_start_dt)
        clipped_end = min(end_dt, hard_limit_dt) if hard_limit_dt else end_dt
        if clipped_end > clipped_start:
            candidates.append((clipped_start, clipped_end))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    first_start, first_end = candidates[0]
    gap_minutes = int((first_start - pending_start_dt).total_seconds() // 60)
    if gap_minutes > _gap_allowance_for_minute(pending_start_dt.hour * 60 + pending_start_dt.minute):
        return None

    inferred_end = first_end
    for cur_start, cur_end in candidates[1:]:
        if cur_start <= inferred_end:
            inferred_end = max(inferred_end, cur_end)
            continue
        gap_limit = _gap_allowance_for_minute(inferred_end.hour * 60 + inferred_end.minute)
        gap_minutes = int((cur_start - inferred_end).total_seconds() // 60)
        if gap_minutes <= gap_limit:
            inferred_end = max(inferred_end, cur_end)
        else:
            break

    return inferred_end


def _parse_checkin_events(checkins):
    parsed = []
    for row in checkins:
        action = row.get("action", "")
        time_str = row.get("time")
        if not action or not time_str:
            continue
        try:
            event_dt = _dt(row.get("date"), time_str)
        except Exception:
            continue
        parsed.append({"dt": event_dt, "action": action, "date": row.get("date"), "time": time_str, "id": row.get("id")})
    parsed.sort(key=lambda x: (x["dt"], 0 if x["action"] == "clock_in" else 1))
    return parsed


def _find_explicit_open_clock_in(checkins):
    pending_start_dt = None
    for event in _parse_checkin_events(checkins):
        action = event["action"]
        if action == "clock_in":
            pending_start_dt = event["dt"]
        elif action == "clock_out" and pending_start_dt is not None and event["dt"] > pending_start_dt:
            pending_start_dt = None
    return pending_start_dt


# ── 新核心：活跃段构造 + 槽位填充 ──────────────────────────

def build_active_segments(db, dates, merge_gap_minutes=None):
    """构建活跃段列表（跨日连续）

    Returns:
        List[dict]: [{
            'start_dt': datetime,
            'end_dt': datetime,
            'in_progress': bool,  # 最后一个段且仍在当前实时搜索窗口内
        }]
    """
    if merge_gap_minutes is None:
        merge_gap_minutes = _merge_gap_minutes()

    passive_by_date = _extract_passive_windows_by_date(db, dates)
    passive_ranges_abs = _merge_absolute_ranges([
        r for d, ranges in passive_by_date.items() for r in _iter_passive_ranges_abs(d, ranges)
    ])

    # 按摸鱼阈值合并（跨日不断开）
    segments = []
    for start_dt, end_dt in passive_ranges_abs:
        if not segments:
            segments.append({'start_dt': start_dt, 'end_dt': end_dt})
            continue

        last = segments[-1]
        gap_minutes = (start_dt - last['end_dt']).total_seconds() / 60
        threshold = _gap_allowance_for_minute(last['end_dt'].hour * 60 + last['end_dt'].minute)

        if gap_minutes <= threshold:
            last['end_dt'] = max(last['end_dt'], end_dt)
        else:
            segments.append({'start_dt': start_dt, 'end_dt': end_dt})

    # 标记当前实时窗口内的最后一段，但保留活动段的真实结束时间。
    # 结果层会据此决定是否把统计区间临时延伸到 now。
    if segments:
        last_seg = segments[-1]
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        elapsed_seconds = (now - last_seg['end_dt']).total_seconds()
        realtime_window_seconds = SLOT_SEARCH_MULTIPLIER * merge_gap_minutes * 60
        if (
            last_seg['end_dt'].strftime("%Y-%m-%d") == today
            and 0 <= elapsed_seconds < realtime_window_seconds
        ):
            last_seg['in_progress'] = True
        else:
            last_seg['in_progress'] = False

    for seg in segments[:-1]:
        seg['in_progress'] = False

    return segments


def fill_segment_slots(segments, checkins, threshold_minutes=None):
    """为活跃段填充槽位，返回槽位填充结果 + 异常列表

    Args:
        segments: build_active_segments 的输出
        checkins: [{id, date, time, action}, ...]
        threshold_minutes: 正常距离上限（默认用配置的摸鱼阈值）

    Returns:
        {
            'segments_with_slots': [{
                'start_dt': datetime,
                'end_dt': datetime,
                'in_progress': bool,
                'head_slot': {card: {id, date, time, action, dt}, distance_min: float, status: str} or None,
                'tail_slot': {card: {id, date, time, action, dt}, distance_min: float, status: str} or None,
            }],
            'anomalies': [{
                'category': str,  # slot_mismatch / missing_checkin / isolated
                'sub_kind': str,  # head_far / tail_far / head_wrong_dir / tail_wrong_dir / head_empty / tail_empty
                'severity': str,  # low / medium
                'primary': dict,  # 涉及的打卡
                'secondary': dict or None,
                'anchor_date': str,
                'segment_index': int,
                'recommended_action': str,
            }],
            'slotted_checkin_ids': set,  # 已被槽位收纳的打卡 id
        }
    """
    if threshold_minutes is None:
        threshold_minutes = _merge_gap_minutes()
    search_window_minutes = SLOT_SEARCH_MULTIPLIER * threshold_minutes

    events = _parse_checkin_events(checkins)
    anomalies = []
    slotted_ids = set()
    segments_with_slots = []

    # 先建立所有槽位的固定搜索窗，再全局分配打卡，避免相邻窗口重叠时
    # 前一个段按遍历顺序抢走后一个段更合适的卡。
    slot_specs = []
    for seg_idx, seg in enumerate(segments):
        start_dt = seg['start_dt']
        end_dt = seg['end_dt']
        inward_minutes = min((end_dt - start_dt).total_seconds() / 120, threshold_minutes)
        slot_specs.append({
            'segment_index': seg_idx,
            'slot': 'head',
            'anchor_dt': start_dt,
            'desired_action': 'clock_in',
            'start_dt': start_dt - timedelta(minutes=search_window_minutes),
            'end_dt': start_dt + timedelta(minutes=inward_minutes),
        })
        # 进行中的段也要正常搜索尾卡；是否临时闭合由结果层裁决。
        slot_specs.append({
            'segment_index': seg_idx,
            'slot': 'tail',
            'anchor_dt': end_dt,
            'desired_action': 'clock_out',
            'start_dt': end_dt - timedelta(minutes=inward_minutes),
            'end_dt': end_dt + timedelta(minutes=search_window_minutes),
        })

    assignments = {}

    def distance(spec, event):
        return abs((event['dt'] - spec['anchor_dt']).total_seconds() / 60)

    def assign_direction(specs, reversed_direction=False, blocked_ids=None):
        """按最大匹配分配一组方向相同的候选卡，并在可选时优先近卡。"""
        blocked_ids = blocked_ids or set()
        candidates_by_slot = {}
        for spec in specs:
            slot_key = (spec['segment_index'], spec['slot'])
            candidates = [
                event for event in events
                if event['id'] not in blocked_ids
                and (event['action'] != spec['desired_action']) == reversed_direction
                and spec['start_dt'] <= event['dt'] <= spec['end_dt']
            ]
            candidates_by_slot[slot_key] = sorted(candidates, key=lambda event: distance(spec, event))

        spec_by_key = {(spec['segment_index'], spec['slot']): spec for spec in specs}
        slot_by_event_id = {}

        def assign(slot_key, seen_ids):
            for event in candidates_by_slot[slot_key]:
                if event['id'] in seen_ids:
                    continue
                seen_ids.add(event['id'])
                previous_slot = slot_by_event_id.get(event['id'])
                if previous_slot is None or assign(previous_slot, seen_ids):
                    slot_by_event_id[event['id']] = slot_key
                    return True
            return False

        # 先满足候选最少的槽位；增广路径保证共享卡不会挤掉唯一可配的槽位。
        ordered_slots = sorted(
            spec_by_key,
            key=lambda slot_key: (
                len(candidates_by_slot[slot_key]),
                spec_by_key[slot_key]['anchor_dt'],
                slot_key,
            ),
        )
        for slot_key in ordered_slots:
            assign(slot_key, set())

        return {
            slot_key: event
            for event_id, slot_key in slot_by_event_id.items()
            for event in events if event['id'] == event_id
        }

    # 第一阶段只采信同向卡。第二阶段才让剩余槽位按默认行为翻转反向卡。
    same_direction = assign_direction(slot_specs)
    for slot_key, event in same_direction.items():
        assignments[slot_key] = (event, False)

    remaining_specs = [
        spec for spec in slot_specs
        if (spec['segment_index'], spec['slot']) not in assignments
    ]
    reverse_direction = assign_direction(
        remaining_specs,
        reversed_direction=True,
        blocked_ids={event['id'] for event in same_direction.values()},
    )
    for slot_key, event in reverse_direction.items():
        assignments[slot_key] = (event, True)

    for seg_idx, seg in enumerate(segments):
        seg_copy = seg.copy()
        seg_copy['head_slot'] = None
        seg_copy['tail_slot'] = None

        start_dt = seg['start_dt']
        end_dt = seg['end_dt']
        in_progress = seg.get('in_progress', False)

        for slot_name in ('head', 'tail'):
            assignment = assignments.get((seg_idx, slot_name))
            if not assignment:
                continue
            best_card, reversed_direction = assignment
            anchor_dt = start_dt if slot_name == 'head' else end_dt
            dist_min = abs((best_card['dt'] - anchor_dt).total_seconds() / 60)
            if reversed_direction:
                sub_kind = f'{slot_name}_wrong_dir'
                anomalies.append({
                    'category': 'slot_mismatch',
                    'sub_kind': sub_kind,
                    'severity': 'medium',
                    'primary': best_card,
                    'secondary': None,
                    'anchor_date': best_card['date'],
                    'segment_index': seg_idx,
                    'recommended_action': 'flip',
                })
                status = 'wrong_dir'
            elif dist_min <= threshold_minutes:
                status = 'normal'
            else:
                status = 'far'
                anomalies.append({
                    'category': 'slot_mismatch',
                    'sub_kind': f'{slot_name}_far',
                    'severity': 'low',
                    'primary': best_card,
                    'secondary': None,
                    'anchor_date': best_card['date'],
                    'segment_index': seg_idx,
                    'recommended_action': 'keep',
                })

            seg_copy[f'{slot_name}_slot'] = {
                'card': best_card,
                'distance_min': dist_min,
                'status': status,
            }
            slotted_ids.add(best_card['id'])

        head_card = seg_copy['head_slot'] and seg_copy['head_slot'].get('card')
        tail_card = seg_copy['tail_slot'] and seg_copy['tail_slot'].get('card')

        # 活跃数据不能单独证明在岗：仅当另一端已有真实打卡时，槽位空才是漏卡。
        # 双端都空表示离岗期间的活跃（例如远程/私人活动），不生成异常或推断。
        if not head_card and tail_card:
            anomalies.append({
                'category': 'missing_checkin',
                'sub_kind': 'head_empty',
                'severity': 'low',
                'primary': None,
                'secondary': None,
                'anchor_date': start_dt.strftime("%Y-%m-%d"),
                'segment_index': seg_idx,
                'recommended_action': 'add',
                'inferred_dt': start_dt,
            })
        elif head_card and not tail_card and not in_progress:
            anomalies.append({
                'category': 'missing_checkin',
                'sub_kind': 'tail_empty',
                'severity': 'low',
                'primary': None,
                'secondary': None,
                'anchor_date': end_dt.strftime("%Y-%m-%d"),
                'segment_index': seg_idx,
                'recommended_action': 'add',
                'inferred_dt': end_dt,
            })

        segments_with_slots.append(seg_copy)

    # 孤立卡判定
    for event in events:
        if event['id'] not in slotted_ids:
            anomalies.append({
                'category': 'isolated',
                'sub_kind': None,
                'severity': 'medium',
                'primary': event,
                'secondary': None,
                'anchor_date': event['date'],
                'segment_index': None,
                'recommended_action': 'delete',
            })

    return {
        'segments_with_slots': segments_with_slots,
        'anomalies': anomalies,
        'slotted_checkin_ids': slotted_ids,
    }
