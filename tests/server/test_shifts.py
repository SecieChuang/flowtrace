"""shifts.py 单元测试：活跃段构造 + 槽位填充 + 解析工具。

fill_segment_slots 是纯函数，直接构造段/打卡 dict；
build_active_segments 需要查 activity_slices，用内存 sqlite 库。
实时窗口（in_progress）相关用 FrozenDateTime 冻结 shifts.datetime。

配置为空 → 默认摸鱼阈值 40 分钟，搜索窗 = SLOT_SEARCH_MULTIPLIER(1.5) × 40 = 60 分钟。
"""

from datetime import datetime
from unittest.mock import patch

import pytest

import shifts
from server_helpers import FrozenDateTime, card, insert_activity, seg

D = "2026-08-28"
D_NEXT = "2026-08-29"
GAP = 40  # 默认阈值
WINDOW = 60  # 1.5 × 40


class _FrozenNow(FrozenDateTime):
    """本文件实时用例统一冻结到 2026-09-02 23:00。"""

    current = datetime(2026, 9, 2, 23, 0, 0)


FROZEN_DAY = "2026-09-02"


# ── build_active_segments ─────────────────────────────


def test_build_segments_empty_activity(mem_db):
    """无任何活动切片时返回空段列表。"""
    assert shifts.build_active_segments(mem_db, [D]) == []


def test_build_segments_single_slice(mem_db):
    """单条切片产生一个活跃段，历史日期 in_progress=False。"""
    insert_activity(mem_db, D, "09:00:00", "10:00:00")
    segments = shifts.build_active_segments(mem_db, [D])
    assert len(segments) == 1
    assert segments[0]["start_dt"] == datetime(2026, 8, 28, 9, 0)
    assert segments[0]["end_dt"] == datetime(2026, 8, 28, 10, 0)
    assert segments[0]["in_progress"] is False


def test_build_segments_merge_gap_within_threshold(mem_db):
    """间隙 30 分钟（< 40）的两段合并为一段。"""
    insert_activity(mem_db, D, "09:00:00", "10:00:00")
    insert_activity(mem_db, D, "10:30:00", "11:00:00")
    segments = shifts.build_active_segments(mem_db, [D])
    assert len(segments) == 1
    assert segments[0]["start_dt"] == datetime(2026, 8, 28, 9, 0)
    assert segments[0]["end_dt"] == datetime(2026, 8, 28, 11, 0)


def test_build_segments_merge_gap_exactly_threshold(mem_db):
    """间隙恰好 40 分钟（≤ 阈值）仍合并。"""
    insert_activity(mem_db, D, "09:00:00", "10:00:00")
    insert_activity(mem_db, D, "10:40:00", "11:30:00")
    segments = shifts.build_active_segments(mem_db, [D])
    assert len(segments) == 1
    assert segments[0]["end_dt"] == datetime(2026, 8, 28, 11, 30)


def test_build_segments_split_gap_beyond_threshold(mem_db):
    """间隙 41 分钟（> 阈值）拆成两段。"""
    insert_activity(mem_db, D, "09:00:00", "10:00:00")
    insert_activity(mem_db, D, "10:41:00", "11:30:00")
    segments = shifts.build_active_segments(mem_db, [D])
    assert len(segments) == 2
    assert segments[0]["end_dt"] == datetime(2026, 8, 28, 10, 0)
    assert segments[1]["start_dt"] == datetime(2026, 8, 28, 10, 41)


def test_build_segments_cross_midnight_stays_continuous(mem_db):
    """跨零点连续活跃（前日 23:30-24:00 + 次日 00:00-00:30）合并为一个跨日段。"""
    insert_activity(mem_db, D, "23:30:00", "24:00:00")
    insert_activity(mem_db, D_NEXT, "00:00:00", "00:30:00")
    segments = shifts.build_active_segments(mem_db, [D, D_NEXT])
    assert len(segments) == 1
    assert segments[0]["start_dt"] == datetime(2026, 8, 28, 23, 30)
    assert segments[0]["end_dt"] == datetime(2026, 8, 29, 0, 30)


def test_build_segments_exclude_manual_tagged(mem_db):
    """tags 含 manual 的切片不参与活跃段构造。"""
    insert_activity(mem_db, D, "09:00:00", "10:00:00", tags="manual")
    insert_activity(mem_db, D, "10:30:00", "11:00:00", tags="auto")
    segments = shifts.build_active_segments(mem_db, [D])
    assert len(segments) == 1
    assert segments[0]["start_dt"] == datetime(2026, 8, 28, 10, 30)


def test_build_segments_in_progress_marks_only_last_segment(mem_db):
    """实时窗口内只有最后一段被标记 in_progress，且保留真实结束时间。"""
    insert_activity(mem_db, FROZEN_DAY, "09:00:00", "10:00:00")
    insert_activity(mem_db, FROZEN_DAY, "22:00:00", "22:58:00")
    with patch.object(shifts, "datetime", _FrozenNow):
        segments = shifts.build_active_segments(mem_db, [FROZEN_DAY])
    assert len(segments) == 2
    assert segments[0]["in_progress"] is False
    assert segments[1]["in_progress"] is True
    assert segments[1]["end_dt"] == datetime(2026, 9, 2, 22, 58)


def test_build_segments_in_progress_false_outside_window(mem_db):
    """最后一段结束超过 1.5×阈值（60 分钟）后不再标记 in_progress。"""
    insert_activity(mem_db, FROZEN_DAY, "20:00:00", "21:00:00")
    with patch.object(shifts, "datetime", _FrozenNow):
        segments = shifts.build_active_segments(mem_db, [FROZEN_DAY])
    assert segments[0]["in_progress"] is False


def test_build_segments_open_slice_today_extends_to_now(mem_db):
    """当天未闭合切片以当前时间为临时终点，且落在实时窗口内。"""
    insert_activity(mem_db, FROZEN_DAY, "22:30:00", None)
    with patch.object(shifts, "datetime", _FrozenNow):
        segments = shifts.build_active_segments(mem_db, [FROZEN_DAY])
    assert len(segments) == 1
    assert segments[0]["end_dt"] == datetime(2026, 9, 2, 23, 0)
    assert segments[0]["in_progress"] is True


# ── SLOT_SEARCH_MULTIPLIER 与阈值默认值 ─────────────────


def test_slot_search_multiplier_is_1_5():
    """搜索窗倍率固定为 1.5。"""
    assert shifts.SLOT_SEARCH_MULTIPLIER == 1.5


def test_lazy_config_defaults_when_config_empty():
    """空配置下惰性属性给出默认值：阈值 40，无分时段覆盖。"""
    assert shifts.INFER_MERGE_GAP_MINUTES == 40
    assert shifts.INFER_DAYPART_THRESHOLDS == {}


def test_fill_slots_default_threshold_from_config():
    """threshold_minutes=None 时读取 server 配置（空配置 → 40）：距段首 39 分钟判 normal。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0))]
    checkins = [card(1, D, "08:21:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=None)
    head = result["segments_with_slots"][0]["head_slot"]
    assert head["status"] == "normal"
    assert head["distance_min"] == 39.0
    assert result["anomalies"] == []


# ── fill_segment_slots 距离/窗口边界 ─────────────────────


def test_distance_exactly_threshold_is_normal():
    """打卡距槽位锚点恰好 40 分钟（≤ 阈值）状态 normal，不产生异常。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0))]
    checkins = [card(1, D, "08:20:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    head = result["segments_with_slots"][0]["head_slot"]
    assert head["status"] == "normal"
    assert head["distance_min"] == 40.0
    assert result["anomalies"] == []


def test_distance_beyond_threshold_is_far_low():
    """距段首 41 分钟（> 阈值 ≤ 搜索窗）：head_far 异常，severity=low，建议 keep。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0))]
    checkins = [card(1, D, "08:19:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    head = result["segments_with_slots"][0]["head_slot"]
    assert head["status"] == "far"
    assert head["distance_min"] == 41.0
    (anom,) = result["anomalies"]
    assert anom["category"] == "slot_mismatch"
    assert anom["sub_kind"] == "head_far"
    assert anom["severity"] == "low"
    assert anom["recommended_action"] == "keep"
    assert anom["primary"]["id"] == 1


def test_search_window_inclusive_boundary_still_slotted():
    """距段首恰好 60 分钟（= 1.5×阈值搜索窗边界，含边界）仍被收纳为 far。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0))]
    checkins = [card(1, D, "08:00:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    head = result["segments_with_slots"][0]["head_slot"]
    assert head is not None
    assert head["status"] == "far"
    assert head["distance_min"] == 60.0
    assert 1 in result["slotted_checkin_ids"]


def test_beyond_search_window_not_slotted():
    """距段首 61 分钟（超出搜索窗）不收纳：段首空 → missing head_empty，卡本身判孤立。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0))]
    checkins = [card(1, D, "07:59:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    segment = result["segments_with_slots"][0]
    assert segment["head_slot"] is None
    assert segment["tail_slot"]["card"]["id"] == 2
    kinds = {(a["category"], a["sub_kind"]) for a in result["anomalies"]}
    assert ("missing_checkin", "head_empty") in kinds
    assert ("isolated", None) in kinds
    assert any(a["category"] == "isolated" and a["primary"]["id"] == 1 for a in result["anomalies"])


# ── fill_segment_slots 槽位构造边界 ──────────────────────


def test_both_empty_slots_no_anomaly():
    """双端都空的段视为离岗活跃：槽位为 None，不产生任何异常。"""
    segments = [seg(datetime(2026, 8, 28, 20, 0), datetime(2026, 8, 28, 22, 0))]
    result = shifts.fill_segment_slots(segments, [], threshold_minutes=GAP)
    segment = result["segments_with_slots"][0]
    assert segment["head_slot"] is None
    assert segment["tail_slot"] is None
    assert result["anomalies"] == []
    assert result["slotted_checkin_ids"] == set()


def test_in_progress_tail_empty_suppresses_missing_checkin():
    """进行中的段尾槽空不是漏卡（可能还没下班），不产生 missing_checkin。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0), in_progress=True)]
    checkins = [card(1, D, "09:00:00", "clock_in")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    segment = result["segments_with_slots"][0]
    assert segment["head_slot"]["card"]["id"] == 1
    assert segment["tail_slot"] is None
    assert result["anomalies"] == []


def test_in_progress_segment_still_accepts_tail_card():
    """进行中的段也能正常收纳尾卡（已下班场景），不产生异常。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0), in_progress=True)]
    checkins = [card(1, D, "09:00:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    segment = result["segments_with_slots"][0]
    assert segment["tail_slot"]["card"]["id"] == 2
    assert segment["tail_slot"]["status"] == "normal"
    assert result["anomalies"] == []


def test_cross_day_segment_fills_both_slots():
    """跨日段（23:00 → 次日 01:00）：头尾卡分属两天也能正常配对。"""
    segments = [seg(datetime(2026, 8, 28, 23, 0), datetime(2026, 8, 29, 1, 0))]
    checkins = [card(1, D, "23:00:00", "clock_in"), card(2, D_NEXT, "01:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    segment = result["segments_with_slots"][0]
    assert segment["head_slot"]["card"]["id"] == 1
    assert segment["head_slot"]["status"] == "normal"
    assert segment["tail_slot"]["card"]["id"] == 2
    assert segment["tail_slot"]["status"] == "normal"
    assert result["anomalies"] == []
    assert result["slotted_checkin_ids"] == {1, 2}


def test_wrong_dir_slot_severity_medium_recommended_flip():
    """段首收纳反向卡（下班卡）：head_wrong_dir，severity=medium，建议 flip。"""
    segments = [seg(datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 18, 0))]
    checkins = [card(1, D, "09:00:00", "clock_out"), card(2, D, "18:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    segment = result["segments_with_slots"][0]
    assert segment["head_slot"]["status"] == "wrong_dir"
    (anom,) = [a for a in result["anomalies"] if a["category"] == "slot_mismatch"]
    assert anom["sub_kind"] == "head_wrong_dir"
    assert anom["severity"] == "medium"
    assert anom["recommended_action"] == "flip"
    assert anom["primary"]["id"] == 1


def test_shared_card_assigned_to_slot_with_fewest_candidates():
    """两段的尾槽共享一张候选卡时，全局匹配保证候选少的槽位优先得到满足。"""
    # 段 A 尾窗 [09:20, 11:00]，段 B 尾窗 [11:00, 12:30]（内向窗 = min(段长/2, 阈值)）
    segments = [
        seg(datetime(2026, 8, 28, 8, 0), datetime(2026, 8, 28, 10, 0)),
        seg(datetime(2026, 8, 28, 10, 30), datetime(2026, 8, 28, 11, 30)),
    ]
    # id1 只在段 A 尾窗内；id2(11:00) 落在两窗交界处，同时是两段的候选
    checkins = [card(1, D, "10:00:00", "clock_out"), card(2, D, "11:00:00", "clock_out")]
    result = shifts.fill_segment_slots(segments, checkins, threshold_minutes=GAP)
    seg_a, seg_b = result["segments_with_slots"]
    assert seg_a["tail_slot"]["card"]["id"] == 1
    assert seg_b["tail_slot"]["card"]["id"] == 2
    # 两段都缺段首卡 → 两个 head_empty
    head_empty = [a for a in result["anomalies"] if a["sub_kind"] == "head_empty"]
    assert len(head_empty) == 2


# ── 打卡事件解析工具 ────────────────────────────────────


def test_parse_checkin_events_orders_clock_in_before_out_at_same_time():
    """同一时刻的上班卡排在下班卡之前（排序键 action 优先级）。"""
    events = shifts._parse_checkin_events([
        card(1, D, "09:00:00", "clock_out"),
        card(2, D, "09:00:00", "clock_in"),
    ])
    assert [e["action"] for e in events] == ["clock_in", "clock_out"]


def test_parse_checkin_events_skips_invalid_rows():
    """缺 action/time 或时间格式非法的行被跳过。"""
    events = shifts._parse_checkin_events([
        {"id": 1, "date": D, "time": "09:00:00", "action": "clock_in"},
        {"id": 2, "date": D, "time": "", "action": "clock_out"},
        {"id": 3, "date": D, "time": "99:99:99", "action": "clock_out"},
        {"id": 4, "date": D, "time": "10:00:00", "action": ""},
    ])
    assert [e["id"] for e in events] == [1]


def test_find_explicit_open_clock_in_returns_latest_unclosed():
    """上班-下班-上班：返回最后一次未闭合的上班时间。"""
    checkins = [
        card(1, D, "09:00:00", "clock_in"),
        card(2, D, "18:00:00", "clock_out"),
        card(3, D, "19:00:00", "clock_in"),
    ]
    assert shifts._find_explicit_open_clock_in(checkins) == datetime(2026, 8, 28, 19, 0)


def test_find_explicit_open_clock_in_none_when_closed():
    """上班后已有更晚的下班卡：无未闭合上班，返回 None。"""
    checkins = [card(1, D, "09:00:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    assert shifts._find_explicit_open_clock_in(checkins) is None


def test_find_explicit_open_clock_in_ignores_earlier_clock_out():
    """下班卡早于上班卡时不视为闭合（乱序数据），仍返回上班时间。"""
    checkins = [card(1, D, "08:00:00", "clock_out"), card(2, D, "09:00:00", "clock_in")]
    assert shifts._find_explicit_open_clock_in(checkins) == datetime(2026, 8, 28, 9, 0)


# ── 区间工具 ───────────────────────────────────────────


def test_normalize_ranges_clips_merges_and_drops_invalid():
    """_normalize_ranges：裁剪到 [0,1440]、合并重叠、丢弃倒置/越界区间。"""
    result = shifts._normalize_ranges([(600, 540), (100, 200), (150, 250), (1500, 2000), (-50, 30)])
    assert result == [(0, 30), (100, 250)]


def test_merge_ranges_by_gap_uses_default_40():
    """_merge_ranges_by_gap：间隙 40 合并、41 不合并（空配置默认阈值）。"""
    assert shifts._merge_ranges_by_gap([(540, 600), (640, 700)]) == [(540, 700)]
    assert shifts._merge_ranges_by_gap([(540, 600), (641, 700)]) == [(540, 600), (641, 700)]


@pytest.mark.parametrize("minute", [0, 300, 700, 1000, 1400])
def test_gap_allowance_defaults_to_40_for_all_dayparts(minute):
    """空配置下四个时段（夜/早/午/晚）的摸鱼阈值都是 40 分钟。"""
    assert shifts._gap_allowance_for_minute(minute) == 40
