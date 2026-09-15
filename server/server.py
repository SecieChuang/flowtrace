"""Flowtrace Server - Flask + SQLite
轻量服务端：API 网关 + 数据持久化 + 静态面板
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory, g, Response

# Import shifts engine (support both package and flat deployment)
try:
    from server import shifts
except ImportError:
    import shifts

# ── 配置 ──────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.environ.get("CONFIG_PATH", os.path.join(_DIR, "config.json"))


def _load_config():
    if os.path.exists(_CFG_PATH):
        try:
            with open(_CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            import sys
            print(f"WARNING: Failed to parse config {_CFG_PATH}: {e}", file=sys.stderr)
    return {}


CFG = _load_config()
HOST = CFG.get("host", "0.0.0.0")
PORT = int(CFG.get("port", 8000))
DB_PATH = os.environ.get("DATABASE_PATH") or CFG.get("database") or os.path.normpath(
    os.path.join(_DIR, "..", "data", "flowtrace.db")
)
# 数据库规范位置：仓库根 data/。config.json 的 "database" 为相对路径时相对 server/ 目录解析。
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.normpath(os.path.join(_DIR, DB_PATH))
AUTO_MERGE_GAP = int(CFG.get("auto_merge_gap_minutes", 2))
AUTO_MIN_FRAGMENT = int(CFG.get("auto_min_fragment_minutes", 2))
OPEN_MANUAL_CLIP_DAYS = int(CFG.get("open_manual_clip_days", 7))
# 注意：用 `or` 而非 get 的默认值——docker-compose 总会注入 API_KEY 环境变量
# （未设置时为 ""），空字符串必须回退到 config.json 的 api_key，否则会被遮蔽。
API_KEY = os.environ.get("API_KEY") or CFG.get("api_key", "")
# 周视图统一固定为周一起始（前后端一致）
WEEK_START_DAY = "monday"
CHECKIN_DEDUPE_SECONDS = int(CFG.get("checkin_dedupe_seconds", 60))

INFER_CFG = CFG.get("inference", {}) if isinstance(CFG.get("inference", {}), dict) else {}
INFER_NO_MANUAL_DAY = bool(INFER_CFG.get("enable_no_manual_day", False))
INFER_MERGE_GAP_MINUTES = int(INFER_CFG.get("merge_gap_minutes", 40))
INFER_DAYPART_THRESHOLDS = INFER_CFG.get("daypart_thresholds", {}) if isinstance(INFER_CFG.get("daypart_thresholds", {}), dict) else {}

# 确保数据目录存在
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(_DIR, "static"))


# ── 时间与区间工具 ─────────────────────────────────────
def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _validate_date(s):
    """Return the date string if valid YYYY-MM-DD, else None."""
    if not s or not _DATE_RE.match(str(s)):
        return None
    try:
        datetime.strptime(str(s), "%Y-%m-%d")
        return str(s)
    except ValueError:
        return None


def _validate_time(s, allow_24=False):
    """Return the time string if valid HH:MM:SS (00-23), else None.
    If allow_24 is True, also accepts '24:00:00' as end-of-day marker.
    """
    if not s:
        return None
    s = str(s)
    if s == "24:00:00":
        return s if allow_24 else None
    if not _TIME_RE.match(s):
        return None
    # Reject out-of-range hours (e.g. "25:00:00")
    try:
        h, m, sec = int(s[:2]), int(s[3:5]), int(s[6:8])
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59):
            return None
    except ValueError:
        return None
    return s


def _parse_time_to_min(time_str):
    if not time_str:
        return None
    try:
        parts = [int(x) for x in str(time_str).split(":")]
        if len(parts) < 2:
            return None
        # 24:00:00 is the end-of-day sentinel = 1440 minutes
        if parts[0] == 24:
            return 24 * 60
        return parts[0] * 60 + parts[1]
    except Exception:
        return None


def _min_to_hms(minute):
    minute = max(0, min(24 * 60, int(round(minute))))
    h = minute // 60
    m = minute % 60
    if h >= 24:
        return "24:00:00"
    return f"{h:02d}:{m:02d}:00"


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
    return _safe_int(INFER_DAYPART_THRESHOLDS.get(key, INFER_MERGE_GAP_MINUTES), INFER_MERGE_GAP_MINUTES)


def _merge_ranges_by_gap(ranges):
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


def _sum_minutes(ranges):
    return round(sum(e - s for s, e in ranges), 1)


def _sum_intersection_minutes(a_ranges, b_ranges):
    a = _normalize_ranges(a_ranges)
    b = _normalize_ranges(b_ranges)
    i = 0
    j = 0
    total = 0
    while i < len(a) and j < len(b):
        a_s, a_e = a[i]
        b_s, b_e = b[j]
        overlap = min(a_e, b_e) - max(a_s, b_s)
        if overlap > 0:
            total += overlap
        if a_e <= b_e:
            i += 1
        else:
            j += 1
    return round(total, 1)


def _accumulate_minutes_by_hour(minute_totals, start_min, end_min):
    """Accumulate overlap minutes into each hour bucket [0, 23]."""
    s = max(0, min(24 * 60, int(start_min)))
    e = max(0, min(24 * 60, int(end_min)))
    if e <= s:
        return

    hour = s // 60
    while hour < 24 and hour * 60 < e:
        hour_start = hour * 60
        hour_end = hour_start + 60
        overlap = min(e, hour_end) - max(s, hour_start)
        if overlap > 0:
            minute_totals[hour] += overlap
        hour += 1


def _pick_best_hour(minute_totals):
    best_hour = None
    best_minutes = 0
    for hour, mins in enumerate(minute_totals):
        if mins > best_minutes:
            best_hour = hour
            best_minutes = mins
    if best_hour is None or best_minutes <= 0:
        return None, 0
    return best_hour, round(best_minutes, 1)


def _format_hours_minutes(total_minutes):
    total_minutes = int(round(total_minutes))
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h}:{m:02d}"


def _week_start_for_date(dt):
    if WEEK_START_DAY == "sunday":
        delta = (dt.weekday() + 1) % 7
    else:
        delta = dt.weekday()
    return dt - timedelta(days=delta)


def _date_range(start_dt, end_dt):
    cur = start_dt
    while cur <= end_dt:
        yield cur
        cur += timedelta(days=1)


# ── API Key 认证中间件 ────────────────────────────────
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            return f(*args, **kwargs)
        key = request.headers.get("X-API-Key", "")
        if key != API_KEY:
            return jsonify({"success": False, "error": "unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated


# ── 数据库 ────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=5000")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS activity_slices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_minutes REAL,
                tags TEXT DEFAULT 'auto',
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(date, start_time)
            );
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                value INTEGER NOT NULL CHECK(value BETWEEN 1 AND 5),
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);
            CREATE INDEX IF NOT EXISTS idx_activity_date ON activity_slices(date);
            CREATE INDEX IF NOT EXISTS idx_ratings_date ON ratings(date);
        """
        )
        db.commit()


# ── 调和引擎（移植自 ref server.py reconcile_segments）──
def _parse_tags(tags_str):
    if not tags_str:
        return []
    return [t.strip().lower() for t in str(tags_str).split(",") if t.strip()]


def _segment_source(tags_list):
    return "manual" if "manual" in tags_list else "auto"


def _dt(date_str, time_str):
    """Parse date+time into a datetime. '24:00:00' is treated as the next day's midnight."""
    if str(time_str) == "24:00:00":
        return datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")


def _upsert_activity_slice(db, date, start_time, end_time, duration, tags):
    """Insert or update a single activity_slices row."""
    existing = db.execute(
        "SELECT id FROM activity_slices WHERE date = ? AND start_time = ?",
        (date, start_time),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE activity_slices SET end_time=?, duration_minutes=?, tags=?, updated_at=datetime('now') WHERE id=?",
            (end_time or None, duration, tags, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO activity_slices (date, start_time, end_time, duration_minutes, tags) VALUES (?,?,?,?,?)",
            (date, start_time, end_time or None, duration, tags),
        )


def _resolve_activity_date(db, date, start_time, end_time):
    """Auto-correct the common mistake of sending next-day's date for an overnight end.

    Triggers when ALL of the following are true:
      - end_time < start_time (overnight signal)
      - (date, start_time) does not exist in DB yet
      - (date-1, start_time, end_time=NULL) exists in DB (open slice from previous day)
    In that case, returns date-1 so the caller writes to the correct day.
    """
    if not end_time or end_time == "24:00:00":
        return date
    start_min = _parse_time_to_min(start_time)
    end_min = _parse_time_to_min(end_time)
    if start_min is None or end_min is None or end_min >= start_min:
        return date
    # Overnight signal present — check if current key already exists (don't redirect)
    existing_d2 = db.execute(
        "SELECT id FROM activity_slices WHERE date = ? AND start_time = ?",
        (date, start_time),
    ).fetchone()
    if existing_d2:
        return date
    # Check previous day for an open (unclosed) slice with the same start_time
    date_d1 = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    open_d1 = db.execute(
        "SELECT id FROM activity_slices WHERE date = ? AND start_time = ? AND end_time IS NULL",
        (date_d1, start_time),
    ).fetchone()
    if open_d1:
        return date_d1
    return date


def _prepare_activity_write(db, date, start_time, end_time, tags):
    """Normalize a single activity write.

    Returns a tuple of:
      - affected_dates: set[str]
      - rows_to_write: list[tuple[date, start_time, end_time, duration, tags]]

    This centralizes:
      - wrong-date overnight correction
      - overnight split into D1/D2
      - same-day duration calculation
    """
    resolved_date = _resolve_activity_date(db, date, start_time, end_time)

    if not end_time:
        return {resolved_date}, [(resolved_date, start_time, None, None, tags)]

    start_min = _parse_time_to_min(start_time)
    end_min = _parse_time_to_min(end_time)
    if start_min is None or end_min is None:
        return {resolved_date}, [(resolved_date, start_time, end_time, None, tags)]

    if end_time != "24:00:00" and end_min < start_min:
        date_d2 = (datetime.strptime(resolved_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            resolved_date,
            date_d2,
        }, [
            (resolved_date, start_time, "24:00:00", round(1440 - start_min, 1), tags),
            (date_d2, "00:00:00", end_time, round(end_min, 1), tags),
        ]

    return {resolved_date}, [(resolved_date, start_time, end_time, round(end_min - start_min, 1), tags)]


def _merge_intervals(intervals, gap):
    if not intervals:
        return []
    intervals.sort(key=lambda x: x["start"])
    merged = [dict(intervals[0])]
    for cur in intervals[1:]:
        last = merged[-1]
        if cur["start"] <= last["end"] + gap:
            if cur["end"] > last["end"]:
                last["end"] = cur["end"]
            last["tags"] |= cur.get("tags", set())
        else:
            merged.append(dict(cur))
    return merged


def _subtract_intervals(base, blocks):
    pieces = [base]
    for b in blocks:
        new_pieces = []
        for p in pieces:
            if b["end"] <= p["start"] or b["start"] >= p["end"]:
                new_pieces.append(p)
                continue
            if b["start"] > p["start"]:
                new_pieces.append({"start": p["start"], "end": b["start"], "tags": set(p.get("tags") or [])})
            if b["end"] < p["end"]:
                new_pieces.append({"start": b["end"], "end": p["end"], "tags": set(p.get("tags") or [])})
        pieces = new_pieces
        if not pieces:
            break
    return pieces


def reconcile_activity(db, date):
    rows = db.execute("SELECT * FROM activity_slices WHERE date = ? ORDER BY start_time", (date,)).fetchall()

    manual_closed = []
    auto_closed = []
    manual_open_blocks = []
    open_segments = []

    for row in rows:
        tags_list = _parse_tags(row["tags"])
        source = _segment_source(tags_list)
        if not row["end_time"]:
            open_segments.append(dict(row))
            if source == "manual":
                start_dt = _dt(row["date"], row["start_time"])
                manual_open_blocks.append(
                    {
                        "start": start_dt,
                        "end": start_dt + timedelta(days=OPEN_MANUAL_CLIP_DAYS),
                        "tags": set(tags_list),
                    }
                )
            continue

        start_dt = _dt(row["date"], row["start_time"])
        end_dt = _dt(row["date"], row["end_time"])
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        interval = {"start": start_dt, "end": end_dt, "tags": set(tags_list)}
        if source == "manual":
            manual_closed.append(interval)
        else:
            auto_closed.append(interval)

    manual_merged = _merge_intervals(manual_closed, timedelta(minutes=0))
    blocks = _merge_intervals(manual_merged + manual_open_blocks, timedelta(minutes=0))
    auto_merged = _merge_intervals(auto_closed, timedelta(minutes=AUTO_MERGE_GAP))

    auto_pieces = []
    for auto_seg in auto_merged:
        for piece in _subtract_intervals(auto_seg, blocks):
            if (piece["end"] - piece["start"]).total_seconds() / 60 >= AUTO_MIN_FRAGMENT:
                auto_pieces.append(piece)
    auto_final = _merge_intervals(auto_pieces, timedelta(minutes=AUTO_MERGE_GAP))

    result = []
    for m in manual_merged:
        result.append(
            {
                "date": m["start"].strftime("%Y-%m-%d"),
                "start_time": m["start"].strftime("%H:%M:%S"),
                "end_time": m["end"].strftime("%H:%M:%S"),
                "end_date": m["end"].strftime("%Y-%m-%d"),
                "duration_minutes": round((m["end"] - m["start"]).total_seconds() / 60, 1),
                "tags": "manual",
            }
        )
    for a in auto_final:
        result.append(
            {
                "date": a["start"].strftime("%Y-%m-%d"),
                "start_time": a["start"].strftime("%H:%M:%S"),
                "end_time": a["end"].strftime("%H:%M:%S"),
                "end_date": a["end"].strftime("%Y-%m-%d"),
                "duration_minutes": round((a["end"] - a["start"]).total_seconds() / 60, 1),
                "tags": "auto",
            }
        )
    for seg in open_segments:
        result.append(dict(seg))

    result.sort(key=lambda s: s.get("start_time", ""))
    return result


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
        parsed.append({"dt": event_dt, "action": action, "date": row.get("date"), "time": time_str})
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


def _build_shift_intervals(db, anchor_date_str, include_inferred_marks=False):
    """按槽位约束构造在岗区间（新实现，废除强制闭合）

    保持签名与返回值格式不变，调用方无感
    """
    anchor_day = datetime.strptime(anchor_date_str, "%Y-%m-%d")
    range_start = (anchor_day - timedelta(days=1)).strftime("%Y-%m-%d")
    range_end = (anchor_day + timedelta(days=1)).strftime("%Y-%m-%d")
    dates = [range_start, anchor_date_str, range_end]

    # 查询打卡
    checkin_rows = [
        dict(r)
        for r in db.execute(
            "SELECT id, date, action, time FROM checkins WHERE date >= ? AND date <= ? ORDER BY date, time",
            (range_start, range_end),
        ).fetchall()
    ]

    # 构建活跃段
    segments = shifts.build_active_segments(db, dates, INFER_MERGE_GAP_MINUTES)

    # 填充槽位
    slot_result = shifts.fill_segment_slots(segments, checkin_rows, INFER_MERGE_GAP_MINUTES)

    # 构造在岗区间 + marks
    intervals = []
    marks = []
    has_clock_in = False
    now = datetime.now()
    realtime_window = timedelta(minutes=shifts.SLOT_SEARCH_MULTIPLIER * INFER_MERGE_GAP_MINUTES)

    for seg_idx, seg_data in enumerate(slot_result['segments_with_slots']):
        head = seg_data.get('head_slot')
        tail = seg_data.get('tail_slot')
        start_dt = seg_data['start_dt']
        end_dt = seg_data['end_dt']
        has_head_card = bool(head and head.get('card'))
        has_tail_card = bool(tail and tail.get('card'))
        is_realtime_pending = (
            anchor_date_str == now.strftime("%Y-%m-%d")
            and seg_idx == len(slot_result['segments_with_slots']) - 1
            and has_head_card
            and not has_tail_card
            and 0 <= (now - end_dt).total_seconds() < realtime_window.total_seconds()
        )

        # 此标志决定是否允许保留由活动段和人工打卡共同构造的在岗区间。
        # 段尾真实下班卡同样是有效人工锚点；否则“推断上班 + 真实下班”
        # 会在 _build_day_metrics 中被误当成无打卡日清空。
        if has_head_card or has_tail_card:
            has_clock_in = True

        # 段首卡
        if has_head_card:
            start_dt = head['card']['dt']
            marks.append({
                'date': head['card']['date'],
                'time': head['card']['time'],
                'action': 'clock_in',
                'inferred': False,
                'anomaly': head['status'] not in ('normal',),
            })
        elif has_tail_card and include_inferred_marks:
            # 只有段尾已有真实卡时，活动段才能补全缺失的段首卡。
            marks.append({
                'date': start_dt.strftime("%Y-%m-%d"),
                'time': start_dt.strftime("%H:%M:%S"),
                'action': 'clock_in',
                'inferred': True,
            })

        # 段尾卡
        if has_tail_card:
            end_dt = tail['card']['dt']
            marks.append({
                'date': tail['card']['date'],
                'time': tail['card']['time'],
                'action': 'clock_out',
                'inferred': False,
                'anomaly': tail['status'] not in ('normal',),
            })
        elif has_head_card and is_realtime_pending:
            # 当前尾卡暂未出现：临时闭合仅用于统计，不生成推断打卡且不写库。
            end_dt = now
            if include_inferred_marks:
                marks.append({
                    'date': now.strftime("%Y-%m-%d"),
                    'time': now.strftime("%H:%M:%S"),
                    'action': 'current',
                    'current': True,
                    'inferred': False,
                    'anomaly': False,
                })
        elif has_head_card and include_inferred_marks:
            # 只有段首已有真实卡时，活动段才能补全缺失的段尾卡。
            marks.append({
                'date': end_dt.strftime("%Y-%m-%d"),
                'time': end_dt.strftime("%H:%M:%S"),
                'action': 'clock_out',
                'inferred': True,
            })

        # 双边都空 → 离岗活跃：不产出在岗区间，即使活跃段仍在进行。
        if has_head_card or has_tail_card:
            intervals.append((start_dt, end_dt))

    # 孤立卡也加入 marks（带 anomaly 标记）
    for anom in slot_result['anomalies']:
        if anom['category'] == 'isolated' and anom['primary']:
            card = anom['primary']
            marks.append({
                'date': card['date'],
                'time': card['time'],
                'action': card['action'],
                'inferred': False,
                'anomaly': True,
            })

    # 兼容旧返回值格式
    passive_by_date = shifts._extract_passive_windows_by_date(db, dates)

    marks.sort(key=lambda x: (x["date"], x["time"], 0 if x["action"] == "clock_in" else 1, 1 if x.get("inferred") else 0))
    return intervals, marks, has_clock_in, passive_by_date


def _build_manual_on_duty_ranges(db, date_str, checkins, passive_ranges):
    day_start = datetime.strptime(date_str, "%Y-%m-%d")
    day_end = day_start + timedelta(days=1)
    intervals_abs, marks_abs, has_clock_in, _ = _build_shift_intervals(db, date_str, include_inferred_marks=True)

    ranges = []
    for start_dt, end_dt in intervals_abs:
        clip_start = max(start_dt, day_start)
        clip_end = min(end_dt, day_end)
        if clip_end <= clip_start:
            continue
        start_min = int((clip_start - day_start).total_seconds() // 60)
        end_min = int((clip_end - day_start).total_seconds() // 60)
        if end_min > start_min:
            ranges.append((start_min, end_min))

    marks = []
    for mark in marks_abs:
        mark_date = mark.get("date") or date_str
        mark_dt = _dt(mark_date, mark.get("time"))
        if day_start <= mark_dt < day_end:
            marks.append(
                {
                    "time": mark_dt.strftime("%H:%M:%S"),
                    "action": mark.get("action"),
                    "inferred": bool(mark.get("inferred")),
                    "anomaly": bool(mark.get("anomaly")),
                    "current": bool(mark.get("current")),
                }
            )

    return _normalize_ranges(ranges), marks, has_clock_in


def _compose_timeline_segments(passive_ranges, on_duty_ranges):
    boundaries = {0, 24 * 60}
    for s, e in passive_ranges:
        boundaries.add(s)
        boundaries.add(e)
    for s, e in on_duty_ranges:
        boundaries.add(s)
        boundaries.add(e)

    points = sorted(boundaries)
    segments = []
    for idx in range(len(points) - 1):
        start = points[idx]
        end = points[idx + 1]
        if end <= start:
            continue

        probe = (start + end) / 2
        passive_active = any(s <= probe < e for s, e in passive_ranges)
        on_duty = any(s <= probe < e for s, e in on_duty_ranges)

        # 离岗时若检测到活跃片段，仍应标记为 active（用于前端“活跃(离岗)”渲染）。
        if passive_active:
            display_state = "active"
            passive_state = "active"
        elif on_duty:
            display_state = "idle"
            passive_state = "idle"
        else:
            display_state = "offline"
            passive_state = "offline"

        opacity = 1.0 if on_duty else 0.35
        if segments and segments[-1]["display_state"] == display_state and segments[-1]["opacity"] == opacity:
            segments[-1]["end_min"] = end
            segments[-1]["end"] = _min_to_hms(end)
            continue

        segments.append(
            {
                "start": _min_to_hms(start),
                "end": _min_to_hms(end),
                "start_min": start,
                "end_min": end,
                "passive_state": passive_state,
                "display_state": display_state,
                "on_duty": on_duty,
                "opacity": opacity,
            }
        )
    return segments


# ── 日指标缓存（历史日期不再变化，可安全缓存）────────────
_day_metrics_cache = {}


def _invalidate_day_cache(date_str):
    _day_metrics_cache.pop(date_str, None)


def _get_day_metrics(db, date_str):
    today = datetime.now().strftime("%Y-%m-%d")
    if date_str != today and date_str in _day_metrics_cache:
        return _day_metrics_cache[date_str]
    result = _build_day_metrics(db, date_str)
    if date_str != today:
        _day_metrics_cache[date_str] = result
    return result


def _build_day_metrics(db, date_str):
    checkins = [
        dict(r)
        for r in db.execute("SELECT action, time FROM checkins WHERE date = ? ORDER BY time", (date_str,)).fetchall()
    ]
    activity_rows = reconcile_activity(db, date_str)
    ratings = [
        dict(r)
        for r in db.execute("SELECT time, value FROM ratings WHERE date = ? ORDER BY time", (date_str,)).fetchall()
    ]

    checkin_events = [
        {
            "action": row.get("action"),
            "dt": _dt(date_str, row.get("time")),
        }
        for row in checkins
        if row.get("time")
    ]
    hard_limit_min = None
    if date_str == datetime.now().strftime("%Y-%m-%d"):
        clock_outs = [event["dt"] for event in checkin_events if event["action"] == "clock_out"]
        if clock_outs:
            hard_limit_min = max(event.hour * 60 + event.minute for event in clock_outs)

    passive_ranges, tags, segment_count = _extract_passive_ranges(date_str, activity_rows, hard_limit_min=hard_limit_min)
    on_duty_ranges, checkin_marks, has_clock_in = _build_manual_on_duty_ranges(db, date_str, checkins, passive_ranges)

    if not has_clock_in:
        if INFER_NO_MANUAL_DAY:
            on_duty_ranges = _merge_ranges_by_gap(passive_ranges)
        else:
            on_duty_ranges = []

    timeline_segments = _compose_timeline_segments(passive_ranges, on_duty_ranges)
    on_duty_minutes = _sum_minutes(on_duty_ranges)
    active_minutes = _sum_minutes(passive_ranges)
    effective_minutes = _sum_intersection_minutes(on_duty_ranges, passive_ranges)
    rating_avg = round(sum(r["value"] for r in ratings) / len(ratings), 2) if ratings else None

    return {
        "date": date_str,
        "checkins": checkins,
        "checkin_marks": sorted(checkin_marks, key=lambda x: (x["time"], 0 if x["action"] == "clock_in" else 1, 1 if x.get("inferred") else 0)),
        "checkin_intervals": [{"start": _min_to_hms(s), "end": _min_to_hms(e)} for s, e in on_duty_ranges],
        "activity_slices": activity_rows,
        "timeline_segments": timeline_segments,
        "ratings": ratings,
        "rating_avg": rating_avg,
        "rating_count": len(ratings),
        "tags": tags,
        "segment_count": segment_count,
        "on_duty_minutes": round(on_duty_minutes, 1),
        "active_minutes": round(active_minutes, 1),
        "effective_minutes": round(effective_minutes, 1),
    }


@app.route("/api/dashboard/stats")
def api_dashboard_stats():
    days = _safe_int(request.args.get("days", 30), 30)
    days = max(1, min(365, days))
    end_date_str = request.args.get("end_date", datetime.now().strftime("%Y-%m-%d"))
    try:
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    except Exception:
        end_dt = datetime.now()
    end_date_str = end_dt.strftime("%Y-%m-%d")
    start_dt = end_dt - timedelta(days=days - 1)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    db = get_db()

    # 1. 早起冠军 & 深夜战神
    early = db.execute(
        """
        SELECT date, time
        FROM checkins
        WHERE action='clock_in' AND date >= ? AND date <= ?
        ORDER BY time ASC, date ASC
        LIMIT 1
        """,
        (start_date_str, end_date_str),
    ).fetchone()
    late = db.execute(
        """
        SELECT date, time
        FROM checkins
        WHERE action='clock_out' AND date >= ? AND date <= ?
        ORDER BY time DESC, date DESC
        LIMIT 1
        """,
        (start_date_str, end_date_str),
    ).fetchone()

    # 2. 专注巅峰 + 3. 黄金小时
    # 统一使用调和后的 timeline_segments。
    # 优先统计“有效活跃”(active && on_duty)，若无数据则回退到“活跃”(active)。
    peak_effective = None
    peak_active = None
    effective_hour_minutes = [0.0] * 24
    active_hour_minutes = [0.0] * 24

    for dt in _date_range(start_dt, end_dt):
        date_str = dt.strftime("%Y-%m-%d")
        day = _get_day_metrics(db, date_str)
        for seg in day["timeline_segments"]:
            if seg.get("display_state") != "active":
                continue
            start_min = _safe_int(seg.get("start_min"), 0)
            end_min = _safe_int(seg.get("end_min"), 0)
            duration = max(0, end_min - start_min)
            if duration <= 0:
                continue

            # 活跃（含离岗）
            if not peak_active or duration > peak_active["min"]:
                peak_active = {"date": date_str, "min": duration}
            _accumulate_minutes_by_hour(active_hour_minutes, start_min, end_min)

            # 有效活跃（在岗）
            if seg.get("on_duty"):
                if not peak_effective or duration > peak_effective["min"]:
                    peak_effective = {"date": date_str, "min": duration}
                _accumulate_minutes_by_hour(effective_hour_minutes, start_min, end_min)

    peak = peak_effective or peak_active

    golden_basis = "effective"
    golden_hour, golden_minutes = _pick_best_hour(effective_hour_minutes)
    if golden_hour is None:
        golden_basis = "active"
        golden_hour, golden_minutes = _pick_best_hour(active_hour_minutes)

    golden = None
    if golden_hour is not None:
        golden = {
            "hour": f"{golden_hour}点到{golden_hour + 1}点",
            "minutes": golden_minutes,
            "basis": golden_basis,
        }

    # 4. 效率之王 (专注占比)
    # 我们复用历史逻辑找到最高的那天
    # 5. 能量周期 (周几最有效) - 这两项可以在前端基于 history 数据计算，也可以在这里计算

    data = {
        "early": {"time": early["time"][:5], "date": early["date"]} if early else None,
        "late": {"time": late["time"][:5], "date": late["date"]} if late else None,
        "peak": {"min": round(peak["min"], 0), "date": peak["date"]} if peak else None,
        "golden": golden,
    }
    return Response(json.dumps(data, ensure_ascii=False), mimetype="application/json")


# ── 响应头 ────────────────────────────────────────────
@app.after_request
def add_cache_headers(response):
    if request.path.startswith("/api/") or request.path == "/" or request.path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# ── 静态文件 ──────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── 打卡 API ─────────────────────────────────────────
@app.route("/api/checkin", methods=["POST"])
@require_api_key
def api_checkin():
    data = request.get_json(force=True)
    action = data.get("action", "")
    date = _validate_date(data.get("date")) or datetime.now().strftime("%Y-%m-%d")
    time_str = _validate_time(data.get("time")) or datetime.now().strftime("%H:%M:%S")

    if action not in ("clock_in", "clock_out"):
        return jsonify({"success": False, "error": "action must be clock_in or clock_out"}), 400

    db = get_db()
    # 去重：同一天、同种 action，若与最近一条间隔小于阈值，保留第一条并忽略当前提交
    try:
        now_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M:%S")
    except Exception:
        now_dt = datetime.now()

    last = db.execute(
        "SELECT time FROM checkins WHERE date = ? AND action = ? ORDER BY time DESC LIMIT 1",
        (date, action),
    ).fetchone()
    if last and last["time"]:
        try:
            last_dt = datetime.strptime(f"{date} {last['time']}", "%Y-%m-%d %H:%M:%S")
            delta = abs((now_dt - last_dt).total_seconds())
            if delta < CHECKIN_DEDUPE_SECONDS:
                return jsonify(
                    {
                        "success": True,
                        "deduped": True,
                        "action": action,
                        "date": date,
                        "time": time_str,
                        "kept_time": last["time"],
                        "ignored_time": time_str,
                        "message": "duplicate_checkin_ignored",
                    }
                )
        except Exception:
            pass

    db.execute("INSERT INTO checkins (action, date, time) VALUES (?, ?, ?)", (action, date, time_str))
    db.commit()
    _invalidate_day_cache(date)
    return jsonify(
        {
            "success": True,
            "deduped": False,
            "action": action,
            "date": date,
            "time": time_str,
            "kept_time": time_str,
            "ignored_time": None,
            "message": "checkin_stored",
        }
    )


# ── 活跃度 API ───────────────────────────────────────
@app.route("/api/activity", methods=["POST"])
@require_api_key
def api_activity():
    data = request.get_json(force=True)
    date = _validate_date(data.get("date")) or datetime.now().strftime("%Y-%m-%d")
    start_time = _validate_time(data.get("start_time", "")) or ""
    end_time = _validate_time(data.get("end_time", ""), allow_24=True) or ""
    tags = data.get("tags", "auto")

    if not start_time:
        return jsonify({"success": False, "error": "start_time required"}), 400

    db = get_db()
    affected_dates, rows_to_write = _prepare_activity_write(db, date, start_time, end_time, tags)
    for row_date, row_start, row_end, row_duration, row_tags in rows_to_write:
        _upsert_activity_slice(db, row_date, row_start, row_end, row_duration, row_tags)
    db.commit()
    for d in affected_dates:
        _invalidate_day_cache(d)
    return jsonify({"success": True})


@app.route("/api/activity/batch", methods=["POST"])
@require_api_key
def api_activity_batch():
    data = request.get_json(force=True)
    events = data.get("events", [])
    db = get_db()
    affected_dates = set()
    for ev in events:
        date = _validate_date(ev.get("date", "")) or ""
        start_time = _validate_time(ev.get("start_time", "")) or ""
        end_time = _validate_time(ev.get("end_time", ""), allow_24=True) or ""
        tags = ev.get("tags", "auto")
        if not date or not start_time:
            continue

        event_dates, rows_to_write = _prepare_activity_write(db, date, start_time, end_time, tags)
        affected_dates |= event_dates
        for row_date, row_start, row_end, row_duration, row_tags in rows_to_write:
            _upsert_activity_slice(db, row_date, row_start, row_end, row_duration, row_tags)

    db.commit()
    for d in affected_dates:
        _invalidate_day_cache(d)
    return jsonify({"success": True, "processed": len(events)})


# ── 评分 API ─────────────────────────────────────────
@app.route("/api/rating", methods=["POST"])
@require_api_key
def api_rating():
    data = request.get_json(force=True)
    date = _validate_date(data.get("date")) or datetime.now().strftime("%Y-%m-%d")
    time_str = _validate_time(data.get("time")) or datetime.now().strftime("%H:%M:%S")
    value = data.get("value")

    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 5:
        return jsonify({"success": False, "error": "value must be 1-5"}), 400

    db = get_db()
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (date, time_str, value))
    db.commit()
    _invalidate_day_cache(date)
    return jsonify({"success": True})


# ── 面板数据 API ─────────────────────────────────────
@app.route("/api/dashboard/daily")
def api_dashboard_daily():
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    db = get_db()

    day = _get_day_metrics(db, date)
    focus_ratio = round(day["effective_minutes"] / day["on_duty_minutes"] * 100, 1) if day["on_duty_minutes"] > 0 else 0

    return jsonify(
        {
            "date": date,
            "checkin_intervals": day["checkin_intervals"],
            "checkin_marks": day["checkin_marks"],
            "activity_slices": day["activity_slices"],
            "timeline_segments": day["timeline_segments"],
            "ratings": day["ratings"],
            "on_duty_minutes": day["on_duty_minutes"],
            "active_minutes": day["active_minutes"],
            "effective_minutes": day["effective_minutes"],
            "focus_ratio": focus_ratio,
        }
    )


@app.route("/api/dashboard/current-duty")
def api_dashboard_current_duty():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    db = get_db()

    intervals_abs, _, has_clock_in, _ = _build_shift_intervals(db, today, include_inferred_marks=False)
    on_duty_now = any(start_dt <= now < end_dt for start_dt, end_dt in intervals_abs)

    range_start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    range_end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    checkin_rows = [
        dict(r)
        for r in db.execute(
            "SELECT date, action, time FROM checkins WHERE date >= ? AND date <= ? ORDER BY date, time",
            (range_start, range_end),
        ).fetchall()
    ]
    explicit_open_start = _find_explicit_open_clock_in(checkin_rows)
    explicit_open_clock_in_now = explicit_open_start is not None and explicit_open_start <= now

    # Fallback: 若 interval 检测失败但有未关闭的打卡，以打卡时间到当前时间为准。
    # 解决活动数据未同步时无法识别上班状态的问题。
    if not on_duty_now and explicit_open_clock_in_now:
        on_duty_now = True
        current_interval = {
            "start": explicit_open_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    else:
        current_interval = None
        for start_dt, end_dt in intervals_abs:
            if start_dt <= now < end_dt:
                current_interval = {
                    "start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
                break

    return jsonify(
        {
            "date": today,
            "time": now.strftime("%H:%M:%S"),
            "on_duty_now": on_duty_now,
            "has_clock_in": has_clock_in,
            "current_interval": current_interval,
            "explicit_open_clock_in_now": explicit_open_clock_in_now,
            "explicit_open_clock_in_start": explicit_open_start.strftime("%Y-%m-%d %H:%M:%S") if explicit_open_start else None,
        }
    )


@app.route("/api/dashboard/weekly")
def api_dashboard_weekly():
    week_start = request.args.get("week_start", "")
    if not week_start:
        week_start = _week_start_for_date(datetime.now()).strftime("%Y-%m-%d")

    try:
        week_start_dt = datetime.strptime(week_start, "%Y-%m-%d")
    except ValueError:
        return jsonify({"success": False, "error": "invalid week_start, expected YYYY-MM-DD"}), 400
    week_end_dt = week_start_dt + timedelta(days=6)

    days = []
    total_on_duty = 0.0
    total_active = 0.0
    total_effective = 0.0
    days_with_data = 0

    db = get_db()
    for dt in _date_range(week_start_dt, week_end_dt):
        date_str = dt.strftime("%Y-%m-%d")
        day = _get_day_metrics(db, date_str)

        total_on_duty += day["on_duty_minutes"]
        total_active += day["active_minutes"]
        total_effective += day["effective_minutes"]
        if day["on_duty_minutes"] > 0:
            days_with_data += 1

        days.append(
            {
                "date": date_str,
                "checkin_minutes": day["on_duty_minutes"],
                "active_minutes": day["active_minutes"],
                "ratings": day["ratings"],
                "on_duty_minutes": day["on_duty_minutes"],
                "effective_minutes": day["effective_minutes"],
                "timeline_segments": day["timeline_segments"],
                "checkin_marks": day["checkin_marks"],
            }
        )

    week_end = week_end_dt.strftime("%Y-%m-%d")
    focus_ratio = round(total_effective / total_on_duty * 100, 1) if total_on_duty > 0 else 0
    total_active_off_duty = max(0.0, total_active - total_effective)

    return jsonify(
        {
            "week_start": week_start,
            "week_end": week_end,
            "week_start_day": WEEK_START_DAY,
            "days": days,
            "days_with_data": days_with_data,
            "total_hours": round(total_on_duty / 60, 1),
            "active_hours": round(total_active / 60, 1),
            "active_off_duty_hours": round(total_active_off_duty / 60, 1),
            "effective_hours": round(total_effective / 60, 1),
            "focus_ratio": focus_ratio,
        }
    )


@app.route("/api/dashboard/history")
def api_dashboard_history():
    days = _safe_int(request.args.get("days", 30), 30)
    days = max(7, min(365, days))

    end_date_str = request.args.get("end_date", datetime.now().strftime("%Y-%m-%d"))
    try:
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    except Exception:
        end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days - 1)

    db = get_db()
    series = []
    total_on_duty = 0.0
    total_effective = 0.0

    for dt in _date_range(start_dt, end_dt):
        date_str = dt.strftime("%Y-%m-%d")
        day = _get_day_metrics(db, date_str)
        total_on_duty += day["on_duty_minutes"]
        total_effective += day["effective_minutes"]

        focus_ratio = round(day["effective_minutes"] / day["on_duty_minutes"] * 100, 1) if day["on_duty_minutes"] > 0 else 0
        checkins = day.get("checkins", [])
        clock_in_times = [c["time"] for c in checkins if c.get("action") == "clock_in" and c.get("time")]
        clock_out_times = [c["time"] for c in checkins if c.get("action") == "clock_out" and c.get("time")]
        series.append(
            {
                "date": date_str,
                "on_duty_hours": round(day["on_duty_minutes"] / 60, 2),
                "active_hours": round(day["active_minutes"] / 60, 2),
                "effective_hours": round(day["effective_minutes"] / 60, 2),
                "focus_ratio": focus_ratio,
                "rating_avg": day["rating_avg"],
                "rating_count": day["rating_count"],
                "first_clock_in": min(clock_in_times) if clock_in_times else None,
                "last_clock_out": max(clock_out_times) if clock_out_times else None,
            }
        )

    total_focus = round(total_effective / total_on_duty * 100, 1) if total_on_duty > 0 else 0
    return jsonify(
        {
            "from": start_dt.strftime("%Y-%m-%d"),
            "to": end_dt.strftime("%Y-%m-%d"),
            "days": series,
            "summary": {
                "on_duty_hours": round(total_on_duty / 60, 1),
                "effective_hours": round(total_effective / 60, 1),
                "focus_ratio": total_focus,
            },
        }
    )


@app.route("/api/dashboard/table")
def api_dashboard_table():
    db = get_db()
    from_date = request.args.get("from", "")
    to_date = request.args.get("to", "")
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")

    if not from_date:
        min_dates = []
        for table, col in (("checkins", "date"), ("activity_slices", "date"), ("ratings", "date")):
            row = db.execute(f"SELECT MIN({col}) AS d FROM {table}").fetchone()
            if row and row["d"]:
                min_dates.append(row["d"])
        if min_dates:
            from_date = min(min_dates)
        else:
            from_date = to_date

    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    except Exception:
        return jsonify({"rows": []})

    if from_dt > to_dt:
        from_dt, to_dt = to_dt, from_dt

    rows = []
    for dt in _date_range(from_dt, to_dt):
        date_str = dt.strftime("%Y-%m-%d")
        day = _get_day_metrics(db, date_str)
        if day["on_duty_minutes"] <= 0 and day["active_minutes"] <= 0 and day["rating_count"] <= 0:
            continue

        focus_ratio = round(day["effective_minutes"] / day["on_duty_minutes"] * 100, 1) if day["on_duty_minutes"] > 0 else 0
        rows.append(
            {
                "date": date_str,
                "total_work_time": _format_hours_minutes(day["on_duty_minutes"]),
                "on_duty_hours": round(day["on_duty_minutes"] / 60, 2),
                "active_hours": round(day["active_minutes"] / 60, 2),
                "effective_hours": round(day["effective_minutes"] / 60, 2),
                "focus_ratio": focus_ratio,
                "tags": day["tags"],
                "segments": day["segment_count"],
                "rating_avg": day["rating_avg"],
                "rating_count": day["rating_count"],
            }
        )

    rows.sort(key=lambda x: x["date"], reverse=True)
    return jsonify({"from": from_date, "to": to_date, "rows": rows})


# ── 健康检查 ─────────────────────────────────────────
@app.route("/api/ping")
def api_ping():
    return jsonify({"status": "ok"})


# ── 初始化数据库（模块加载时执行，兼容 gunicorn）──────
init_db()


# ── 启动 ─────────────────────────────────────────────
if __name__ == "__main__":
    if not API_KEY and HOST not in ("127.0.0.1", "localhost"):
        print("!" * 64)
        print("【安全警告】未配置 api_key，且服务绑定了非回环地址（{}）！".format(HOST))
        print("任何能访问该地址的机器都可以未鉴权调用全部接口，")
        print("包括 dashboard 的读取接口（工时、截图统计等隐私数据）。")
        print("请在 config.json 中设置 api_key，或将 host 改为 127.0.0.1。")
        print("!" * 64)
    print(f"Flowtrace server starting on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
