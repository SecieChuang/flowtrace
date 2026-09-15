"""槽位填充集成测试（迁移自 scripts/test_slot_filling.py，断言语义保持不变）。

覆盖 fill_segment_slots 的配对/缺卡/孤立/方向/距离/搜索窗行为，
以及 _build_shift_intervals 在实时窗口内的临时闭合逻辑。
"""

import sqlite3
from datetime import datetime
from unittest.mock import patch

import server as server_module
import shifts
from detection import build_actions_for_missing_checkin
from server import _build_shift_intervals
from server_helpers import FrozenDateTime

THRESHOLD_MINUTES = 40


def _realtime_db(activity_start, activity_end, checkins=()):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE activity_slices (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            tags TEXT
        );
        CREATE TABLE checkins (
            id INTEGER PRIMARY KEY,
            action TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        );
        CREATE TABLE ratings (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            value INTEGER NOT NULL
        );
    """)
    db.execute(
        "INSERT INTO activity_slices (date, start_time, end_time, tags) VALUES (?, ?, ?, ?)",
        ("2026-09-02", activity_start, activity_end, "auto"),
    )
    db.executemany(
        "INSERT INTO checkins (action, date, time) VALUES (?, ?, ?)",
        [(action, "2026-09-02", time) for action, time in checkins],
    )
    return db


def _build_realtime_intervals(db):
    with patch.object(server_module, "datetime", FrozenDateTime), patch.object(shifts, "datetime", FrozenDateTime):
        return _build_shift_intervals(db, "2026-09-02", include_inferred_marks=True)


def test_basic_slot_filling():
    """正常配对：段首段尾都填满时无异常，两张卡都被收纳。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 9, 0),
        'end_dt': datetime(2026, 8, 28, 18, 0),
        'in_progress': False,
    }]

    checkins = [
        {'id': 1, 'date': '2026-08-28', 'time': '09:00:00', 'action': 'clock_in'},
        {'id': 2, 'date': '2026-08-28', 'time': '18:00:00', 'action': 'clock_out'},
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)

    assert len(result['anomalies']) == 0, f"Expected 0 anomalies, got {len(result['anomalies'])}"
    assert len(result['slotted_checkin_ids']) == 2, f"Expected 2 slotted, got {len(result['slotted_checkin_ids'])}"


def test_missing_checkout():
    """缺下班卡：段尾槽空产生 missing_checkin/tail_empty。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 9, 0),
        'end_dt': datetime(2026, 8, 28, 18, 0),
        'in_progress': False,
    }]

    checkins = [
        {'id': 1, 'date': '2026-08-28', 'time': '09:00:00', 'action': 'clock_in'},
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)

    anomalies = result['anomalies']
    assert len(anomalies) == 1, f"Expected 1 anomaly, got {len(anomalies)}"
    assert anomalies[0]['category'] == 'missing_checkin'
    assert anomalies[0]['sub_kind'] == 'tail_empty'


def test_both_empty_segment_is_not_an_anomaly():
    """无任何打卡的活跃段必须保持为离岗活跃，不能双端推断。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 20, 0),
        'end_dt': datetime(2026, 8, 28, 22, 0),
        'in_progress': False,
    }]

    result = shifts.fill_segment_slots(segments, [], threshold_minutes=THRESHOLD_MINUTES)

    assert result['anomalies'] == [], f"Expected no anomalies, got {result['anomalies']}"
    assert result['slotted_checkin_ids'] == set()


def test_isolated_checkin():
    """孤立卡：打卡不在任何段范围内时单独报 isolated。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 9, 0),
        'end_dt': datetime(2026, 8, 28, 18, 0),
        'in_progress': False,
    }]

    checkins = [
        {'id': 1, 'date': '2026-08-28', 'time': '09:00:00', 'action': 'clock_in'},
        {'id': 2, 'date': '2026-08-28', 'time': '18:00:00', 'action': 'clock_out'},
        {'id': 3, 'date': '2026-08-28', 'time': '22:00:00', 'action': 'clock_in'},  # 孤立
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)

    isolated = [a for a in result['anomalies'] if a['category'] == 'isolated']
    assert len(isolated) == 1, f"Expected 1 isolated, got {len(isolated)}"
    assert isolated[0]['primary']['id'] == 3


def test_wrong_direction():
    """方向错误：段首找到下班卡时报 wrong_dir。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 9, 0),
        'end_dt': datetime(2026, 8, 28, 18, 0),
        'in_progress': False,
    }]

    checkins = [
        {'id': 1, 'date': '2026-08-28', 'time': '09:00:00', 'action': 'clock_out'},  # 方向错
        {'id': 2, 'date': '2026-08-28', 'time': '18:00:00', 'action': 'clock_out'},
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)

    wrong_dir = [a for a in result['anomalies'] if 'wrong_dir' in a.get('sub_kind', '')]
    assert len(wrong_dir) >= 1, f"Expected >=1 wrong_dir, got {len(wrong_dir)}"


def test_far_distance():
    """打卡距段首较远（开会场景）：报 *_far，severity=low。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 11, 0),  # 活动从 11:00 开始
        'end_dt': datetime(2026, 8, 28, 18, 0),
        'in_progress': False,
    }]

    checkins = [
        {'id': 1, 'date': '2026-08-28', 'time': '10:00:00', 'action': 'clock_in'},  # 距段首 1h（1.5×阈值）
        {'id': 2, 'date': '2026-08-28', 'time': '18:00:00', 'action': 'clock_out'},
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)

    far = [a for a in result['anomalies'] if 'far' in a.get('sub_kind', '')]
    assert len(far) >= 1, f"Expected >=1 far, got {len(far)}"
    assert far[0]['severity'] == 'low'


def test_search_window_excludes_distant_wrong_direction_card():
    """段尾搜索不得跨过 1.5 倍阈值，将后续上班卡伪装成下班卡。"""
    segments = [{
        'start_dt': datetime(2026, 8, 28, 9, 0),
        'end_dt': datetime(2026, 8, 28, 12, 0),
        'in_progress': False,
    }]
    checkins = [
        {'id': 1, 'date': '2026-08-28', 'time': '09:00:00', 'action': 'clock_in'},
        {'id': 2, 'date': '2026-08-28', 'time': '13:30:00', 'action': 'clock_in'},
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)

    segment = result['segments_with_slots'][0]
    assert segment['head_slot']['card']['id'] == 1
    assert segment['tail_slot'] is None
    assert any(a['category'] == 'missing_checkin' and a['sub_kind'] == 'tail_empty' for a in result['anomalies'])
    assert any(a['category'] == 'isolated' and a['primary']['id'] == 2 for a in result['anomalies'])


def test_tail_card_anchor_builds_on_duty_interval():
    """真实下班卡应锚定推断上班，并保留在岗区间。"""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE activity_slices (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            tags TEXT
        );
        CREATE TABLE checkins (
            id INTEGER PRIMARY KEY,
            action TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        );
    """)
    db.execute(
        "INSERT INTO activity_slices (date, start_time, end_time, tags) VALUES (?, ?, ?, ?)",
        ("2026-08-28", "14:00:00", "16:30:00", "auto"),
    )
    db.execute(
        "INSERT INTO checkins (action, date, time) VALUES (?, ?, ?)",
        ("clock_out", "2026-08-28", "16:30:00"),
    )

    intervals, marks, has_manual_anchor, _ = _build_shift_intervals(
        db, "2026-08-28", include_inferred_marks=True
    )

    assert has_manual_anchor is True
    assert intervals == [(datetime(2026, 8, 28, 14, 0), datetime(2026, 8, 28, 16, 30))]
    assert any(mark["action"] == "clock_in" and mark["inferred"] for mark in marks)


def test_slot_search_does_not_cross_long_gap_or_prefer_reverse_card():
    """槽位不得跨越长空档；窗口内有同向卡时优先同向卡。"""
    segments = [
        {
            'start_dt': datetime(2026, 8, 28, 20, 0),
            'end_dt': datetime(2026, 8, 28, 21, 0),
            'in_progress': False,
        },
        {
            'start_dt': datetime(2026, 8, 29, 9, 0),
            'end_dt': datetime(2026, 8, 29, 10, 0),
            'in_progress': False,
        },
    ]
    checkins = [
        # 距第一段尾超过向外搜索窗，不应被第一段吸收。
        {'id': 1, 'date': '2026-08-28', 'time': '22:30:00', 'action': 'clock_in'},
        # 第二段首同时存在反向近卡和同向稍远卡，应优先同向卡。
        {'id': 2, 'date': '2026-08-29', 'time': '08:30:00', 'action': 'clock_out'},
        {'id': 3, 'date': '2026-08-29', 'time': '09:20:00', 'action': 'clock_in'},
    ]

    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=THRESHOLD_MINUTES)
    first, second = result['segments_with_slots']

    assert first['head_slot'] is None and first['tail_slot'] is None
    assert second['head_slot']['card']['id'] == 3
    assert second['head_slot']['status'] == 'normal'
    assert any(a['category'] == 'isolated' and a['primary']['id'] == 1 for a in result['anomalies'])
    assert any(a['category'] == 'isolated' and a['primary']['id'] == 2 for a in result['anomalies'])


def test_cross_day_reverse_card_does_not_absorb_next_morning():
    """昨日段的反向上班卡仍参与构造，但不得跨长空档吸收次日早卡。"""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE activity_slices (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            tags TEXT
        );
        CREATE TABLE checkins (
            id INTEGER PRIMARY KEY,
            action TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        );
    """)
    db.executemany(
        "INSERT INTO activity_slices (date, start_time, end_time, tags) VALUES (?, ?, ?, ?)",
        [
            ("2026-08-28", "20:00:00", "21:00:00", "auto"),
            ("2026-08-29", "09:00:00", "10:00:00", "auto"),
        ],
    )
    db.executemany(
        "INSERT INTO checkins (action, date, time) VALUES (?, ?, ?)",
        [
            ("clock_out", "2026-08-28", "20:00:00"),
            ("clock_in", "2026-08-29", "09:00:00"),
        ],
    )

    intervals, marks, has_manual_anchor, _ = _build_shift_intervals(
        db, "2026-08-28", include_inferred_marks=True
    )

    assert has_manual_anchor is True
    assert intervals == [
        (datetime(2026, 8, 28, 20, 0), datetime(2026, 8, 28, 21, 0)),
        (datetime(2026, 8, 29, 9, 0), datetime(2026, 8, 29, 10, 0)),
    ]
    assert any(mark["action"] == "clock_in" and mark["time"] == "20:00:00" and mark["anomaly"] for mark in marks)
    assert not any(mark["time"] == "09:00:00" and mark["action"] == "clock_out" for mark in marks)


def test_realtime_pending_tail_extends_statistics_without_inferred_checkout():
    """实时窗口内缺尾卡：统计临时闭合，前端使用 current 标记。"""
    db = _realtime_db("22:00:00", "22:58:00", [("clock_in", "22:00:00")])
    intervals, marks, has_manual_anchor, _ = _build_realtime_intervals(db)

    assert has_manual_anchor is True
    assert intervals == [(datetime(2026, 9, 2, 22, 0), datetime(2026, 9, 2, 23, 0))]
    assert not any(mark.get("inferred") and mark["action"] == "clock_out" for mark in marks)
    current_marks = [mark for mark in marks if mark.get("current")]
    assert len(current_marks) == 1
    assert current_marks[0]["action"] == "current"
    assert current_marks[0]["time"] == "23:00:00"


def test_realtime_pending_tail_after_window_uses_inferred_checkout():
    """超过 60 分钟后，尾卡缺失按普通历史段正式推断。"""
    db = _realtime_db("20:00:00", "21:59:00", [("clock_in", "20:00:00")])
    intervals, marks, _, _ = _build_realtime_intervals(db)

    assert intervals == [(datetime(2026, 9, 2, 20, 0), datetime(2026, 9, 2, 21, 59))]
    assert not any(mark.get("current") for mark in marks)
    assert any(mark["action"] == "clock_out" and mark.get("inferred") for mark in marks)


def test_realtime_tail_card_closes_segment_without_current_marker():
    """实时窗口内已找到尾卡时视为闭合，不产生 current 标记。"""
    db = _realtime_db(
        "22:00:00",
        "22:58:00",
        [("clock_in", "22:00:00"), ("clock_out", "22:59:00")],
    )
    intervals, marks, _, _ = _build_realtime_intervals(db)

    assert intervals == [(datetime(2026, 9, 2, 22, 0), datetime(2026, 9, 2, 22, 59))]
    assert not any(mark.get("current") for mark in marks)
    assert any(mark["action"] == "clock_out" and not mark.get("inferred") for mark in marks)


def test_realtime_double_empty_segment_remains_off_duty_active():
    """实时窗口内双端无卡仍是离岗活跃，不临时构造在岗。"""
    db = _realtime_db("22:00:00", "22:58:00")
    intervals, marks, has_manual_anchor, _ = _build_realtime_intervals(db)

    assert intervals == []
    assert marks == []
    assert has_manual_anchor is False


def test_realtime_day_metrics_count_temporary_interval_to_now():
    """日统计必须把临时闭合区间计入在岗，但不增加推断下班卡。"""
    db = _realtime_db("22:00:00", "22:58:00", [("clock_in", "22:00:00")])
    with patch.object(server_module, "datetime", FrozenDateTime), patch.object(shifts, "datetime", FrozenDateTime):
        day = server_module._build_day_metrics(db, "2026-09-02")

    assert day["on_duty_minutes"] == 60.0
    assert day["effective_minutes"] == 58.0
    assert any(mark.get("current") for mark in day["checkin_marks"])
    assert not any(mark.get("inferred") and mark["action"] == "clock_out" for mark in day["checkin_marks"])


def test_missing_checkin_actions_follow_the_missing_slot_direction():
    """段首缺上班卡，段尾缺下班卡；修复动作不能反向。"""
    head_actions = build_actions_for_missing_checkin({
        "anchor_date": "2026-09-02",
        "kind": "in",
        "inferred_dt": datetime(2026, 9, 2, 9, 0),
    }, None)
    tail_actions = build_actions_for_missing_checkin({
        "anchor_date": "2026-09-02",
        "kind": "out",
        "inferred_dt": datetime(2026, 9, 2, 18, 0),
    }, None)

    assert head_actions[0]["params"]["action"] == "clock_in"
    assert "上班" in head_actions[0]["label"]
    assert head_actions[1]["fixed_params"]["action"] == "clock_in"
    assert tail_actions[0]["params"]["action"] == "clock_out"
    assert "下班" in tail_actions[0]["label"]
    assert tail_actions[1]["fixed_params"]["action"] == "clock_out"
