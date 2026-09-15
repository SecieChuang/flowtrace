"""detection.py 单元测试：detect_issues 三类产出 + build_actions_for_* 动作结构。

detect_issues 的活跃段来自 db 里的 activity_slices（内存库），打卡以参数传入。
阈值显式传 40（与空配置默认一致），避免对全局配置的隐式依赖。
基准日期 2026-08-28（历史日期），不会命中实时窗口。
"""

from datetime import datetime

from detection import (
    build_actions_for_isolated,
    build_actions_for_missing_checkin,
    build_actions_for_slot_mismatch,
    build_context,
    detect_issues,
)
from server_helpers import card, insert_activity, insert_checkin

D = "2026-08-28"
GAP = 40


def _detect(checkins, db):
    return detect_issues(checkins, db=db, threshold_minutes=GAP)


# ── detect_issues：slot_mismatch 四子类 ──────────────────


def test_detect_empty_checkins_returns_empty(mem_db):
    """无打卡时直接返回空列表。"""
    assert _detect([], mem_db) == []


def test_detect_head_far(mem_db):
    """上班卡距段首 60 分钟（> 阈值 ≤ 搜索窗）→ slot_mismatch/head_far/low。"""
    insert_activity(mem_db, D, "11:00:00", "18:00:00")
    checkins = [card(1, D, "10:00:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "slot_mismatch"
    assert issue["sub_kind"] == "head_far"
    assert issue["severity"] == "low"
    assert issue["kind"] == "in"
    assert issue["type_label"] == "打卡距段首较远"
    assert issue["id"] == "slot_mismatch-1"
    assert issue["anchor_date"] == D
    assert issue["primary"]["id"] == 1
    assert issue["secondary"] is None


def test_detect_tail_far(mem_db):
    """下班卡距段尾 60 分钟 → slot_mismatch/tail_far/low，kind=out。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    checkins = [card(1, D, "09:00:00", "clock_in"), card(2, D, "19:00:00", "clock_out")]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "slot_mismatch"
    assert issue["sub_kind"] == "tail_far"
    assert issue["severity"] == "low"
    assert issue["kind"] == "out"
    assert issue["type_label"] == "打卡距段尾较远"
    assert issue["primary"]["id"] == 2


def test_detect_head_wrong_dir(mem_db):
    """段首槽位收纳到下班卡 → slot_mismatch/head_wrong_dir/medium，建议翻转。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    checkins = [card(1, D, "09:00:00", "clock_out"), card(2, D, "18:00:00", "clock_out")]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "slot_mismatch"
    assert issue["sub_kind"] == "head_wrong_dir"
    assert issue["severity"] == "medium"
    assert issue["kind"] == "in"
    assert issue["type_label"] == "段首方向错误"
    assert issue["target"]["id"] == 1
    assert issue["suggested_action"] == "clock_in"


def test_detect_tail_wrong_dir(mem_db):
    """段尾槽位收纳到上班卡 → slot_mismatch/tail_wrong_dir/medium。"""
    insert_activity(mem_db, D, "09:00:00", "12:00:00")
    checkins = [card(1, D, "09:00:00", "clock_in"), card(2, D, "12:00:00", "clock_in")]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "slot_mismatch"
    assert issue["sub_kind"] == "tail_wrong_dir"
    assert issue["severity"] == "medium"
    assert issue["kind"] == "out"
    assert issue["type_label"] == "段尾方向错误"
    assert issue["target"]["id"] == 2
    assert issue["suggested_action"] == "clock_out"


# ── detect_issues：missing_checkin ──────────────────────


def test_detect_missing_head_checkin(mem_db):
    """只有下班卡 → missing_checkin/head_empty，推断时间为段首，kind=in。"""
    insert_activity(mem_db, D, "14:00:00", "16:30:00")
    checkins = [card(1, D, "16:30:00", "clock_out")]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "missing_checkin"
    assert issue["sub_kind"] == "head_empty"
    assert issue["severity"] == "low"
    assert issue["kind"] == "in"
    assert issue["type_label"] == "缺少上班打卡"
    assert issue["id"] == f"missing_checkin-{D}-head_empty"
    assert issue["primary"] is None
    assert issue["inferred_dt"] == datetime(2026, 8, 28, 14, 0)
    assert issue["anchor_date"] == D


def test_detect_missing_tail_checkin(mem_db):
    """只有上班卡（历史日，非进行中）→ missing_checkin/tail_empty，推断时间为段尾。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    checkins = [card(1, D, "09:00:00", "clock_in")]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "missing_checkin"
    assert issue["sub_kind"] == "tail_empty"
    assert issue["kind"] == "out"
    assert issue["type_label"] == "缺少下班打卡"
    assert issue["inferred_dt"] == datetime(2026, 8, 28, 18, 0)


# ── detect_issues：isolated 与过滤/排序 ──────────────────


def test_detect_isolated_checkin(mem_db):
    """落在所有槽位搜索窗外的打卡 → isolated/medium。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    checkins = [
        card(1, D, "09:00:00", "clock_in"),
        card(2, D, "18:00:00", "clock_out"),
        card(3, D, "22:00:00", "clock_in"),
    ]
    issues = _detect(checkins, mem_db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["category"] == "isolated"
    assert issue["severity"] == "medium"
    assert issue["kind"] == "in"
    assert issue["type_label"] == "孤立打卡"
    assert issue["id"] == "isolated-3"
    assert issue["primary"]["id"] == 3
    assert issue["anchor_date"] == D


def test_detect_filters_issues_outside_scan_range(mem_db):
    """±1 天缓冲区内产出的异常（anchor 在扫描区间外）被过滤掉。"""
    # 打卡只在 2026-08-28；跨日段的段首落在 2026-08-27 → head_empty 的 anchor 被过滤
    insert_activity(mem_db, "2026-08-27", "23:00:00", "24:00:00")
    insert_activity(mem_db, D, "00:00:00", "01:00:00")
    checkins = [card(1, D, "01:00:00", "clock_out")]
    assert _detect(checkins, mem_db) == []


def test_detect_sorted_by_anchor_date_then_time_desc(mem_db):
    """多个 issue 按 (anchor_date, 卡时间) 倒序排列。"""
    # 无活跃数据 → 三张卡全部孤立
    checkins = [
        card(1, "2026-08-27", "09:00:00", "clock_in"),
        card(2, D, "18:00:00", "clock_out"),
        card(3, D, "06:00:00", "clock_in"),
    ]
    issues = _detect(checkins, mem_db)
    assert [i["id"] for i in issues] == ["isolated-2", "isolated-3", "isolated-1"]
    assert all(i["category"] == "isolated" for i in issues)


# ── build_context ────────────────────────────────────────


def test_build_context_marks_highlight_and_reads_activity(mem_db):
    """build_context 返回 anchor 日的打卡（带 highlight）与活动切片。"""
    id1 = insert_checkin(mem_db, "clock_in", D, "09:00:00")
    id2 = insert_checkin(mem_db, "clock_out", D, "18:00:00")
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    issue = {"anchor_date": D, "primary": {"id": id1}, "secondary": None}
    ctx = build_context(issue, mem_db)
    assert [(c["id"], c["highlight"]) for c in ctx["day_checkins"]] == [(id1, True), (id2, False)]
    assert len(ctx["day_activity"]) == 1
    assert ctx["day_activity"][0]["start_time"] == "09:00:00"
    assert ctx["day_activity"][0]["end_time"] == "18:00:00"


# ── build_actions_for_slot_mismatch ─────────────────────


def test_actions_slot_mismatch_far_recommends_keep(mem_db):
    """*_far 的修复动作：keep 推荐（params 空）+ delete + edit_time。"""
    insert_activity(mem_db, D, "11:00:00", "18:00:00")
    checkins = [card(1, D, "10:00:00", "clock_in"), card(2, D, "18:00:00", "clock_out")]
    (issue,) = _detect(checkins, mem_db)
    actions = build_actions_for_slot_mismatch(issue, None)
    assert [a["key"] for a in actions] == ["keep", "delete", "edit_time"]

    keep = actions[0]
    assert keep["op"] == "keep"
    assert keep["params"] == {}
    assert keep["recommended"] is True
    assert "10:00:00" in keep["label"]

    delete = actions[1]
    assert delete["op"] == "delete"
    assert delete["params"] == {"id": 1}
    assert "recommended" not in delete

    edit = actions[2]
    assert edit["op"] == "edit_time"
    assert edit["fixed_params"] == {"id": 1}
    assert edit["interactive_fields"] == [{"name": "time", "type": "time"}]


def test_actions_slot_mismatch_wrong_dir_recommends_flip(mem_db):
    """*_wrong_dir 的修复动作：flip_direction 推荐（翻转为目标方向）+ delete + edit_time。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    checkins = [card(1, D, "09:00:00", "clock_out"), card(2, D, "18:00:00", "clock_out")]
    (issue,) = _detect(checkins, mem_db)
    actions = build_actions_for_slot_mismatch(issue, None)
    assert [a["key"] for a in actions] == ["flip_direction", "delete", "edit_time"]

    flip = actions[0]
    assert flip["op"] == "flip_action"
    assert flip["params"] == {"id": 1, "action": "clock_in"}
    assert flip["recommended"] is True
    assert "09:00:00" in flip["label"]
    assert "上班" in flip["label"]

    assert actions[1]["params"] == {"id": 1}
    assert actions[2]["fixed_params"] == {"id": 1}


def test_actions_wrong_dir_fallback_without_target_fields():
    """issue 缺 target/suggested_action 字段时，翻转方向由 primary.action 推导。"""
    issue = {
        "primary": {"id": 5, "date": D, "time": "09:00:00", "action": "clock_in"},
        "sub_kind": "tail_wrong_dir",
    }
    actions = build_actions_for_slot_mismatch(issue, None)
    flip = actions[0]
    assert flip["params"] == {"id": 5, "action": "clock_out"}
    assert "下班" in flip["label"]


# ── build_actions_for_missing_checkin ───────────────────


def test_actions_missing_head_adds_clock_in(mem_db):
    """head_empty 的修复动作：推荐按推断时间补上班卡 + 手动补上班卡。"""
    insert_activity(mem_db, D, "14:00:00", "16:30:00")
    (issue,) = _detect([card(1, D, "16:30:00", "clock_out")], mem_db)
    actions = build_actions_for_missing_checkin(issue, None)
    assert [a["key"] for a in actions] == ["add_inferred", "add_manual"]

    add_inferred = actions[0]
    assert add_inferred["op"] == "add"
    assert add_inferred["params"] == {"date": D, "time": "14:00:00", "action": "clock_in"}
    assert add_inferred["recommended"] is True
    assert "14:00:00" in add_inferred["label"]
    assert "上班" in add_inferred["label"]

    add_manual = actions[1]
    assert add_manual["op"] == "add"
    assert add_manual["fixed_params"] == {"date": D, "action": "clock_in"}
    assert add_manual["interactive_fields"] == [{"name": "time", "type": "time"}]


def test_actions_missing_tail_adds_clock_out(mem_db):
    """tail_empty 的修复动作方向为下班卡，推断时间取段尾。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    (issue,) = _detect([card(1, D, "09:00:00", "clock_in")], mem_db)
    actions = build_actions_for_missing_checkin(issue, None)
    assert actions[0]["params"] == {"date": D, "time": "18:00:00", "action": "clock_out"}
    assert "下班" in actions[0]["label"]
    assert actions[1]["fixed_params"] == {"date": D, "action": "clock_out"}


def test_actions_missing_without_inferred_dt_only_manual():
    """无推断时间时只给手动补卡一个选项。"""
    issue = {"anchor_date": D, "kind": "out", "sub_kind": "tail_empty", "inferred_dt": None}
    actions = build_actions_for_missing_checkin(issue, None)
    assert [a["key"] for a in actions] == ["add_manual"]
    assert actions[0]["fixed_params"] == {"date": D, "action": "clock_out"}


# ── build_actions_for_isolated ──────────────────────────


def test_actions_isolated_clock_in_pairs_with_clock_out(mem_db):
    """孤立上班卡的修复动作：delete 推荐 + 手动补下班卡 + edit_time + keep。"""
    insert_activity(mem_db, D, "09:00:00", "18:00:00")
    checkins = [
        card(1, D, "09:00:00", "clock_in"),
        card(2, D, "18:00:00", "clock_out"),
        card(3, D, "22:00:00", "clock_in"),
    ]
    (issue,) = _detect(checkins, mem_db)
    actions = build_actions_for_isolated(issue, None)
    assert [a["key"] for a in actions] == ["delete", "add_pair_manual", "edit_time", "keep"]

    delete = actions[0]
    assert delete["op"] == "delete"
    assert delete["params"] == {"id": 3}
    assert delete["recommended"] is True
    assert "22:00:00" in delete["label"]

    pair = actions[1]
    assert pair["op"] == "add"
    assert pair["fixed_params"] == {"date": D, "action": "clock_out"}
    assert "下班" in pair["label"]

    assert actions[2]["fixed_params"] == {"id": 3}
    keep = actions[3]
    assert keep["op"] == "keep"
    assert keep["params"] == {}


def test_actions_isolated_clock_out_pairs_with_clock_in():
    """孤立下班卡（无 kind 字段时按 action 推导）配对动作为补上班卡。"""
    issue = {"primary": {"id": 7, "date": D, "time": "22:00:00", "action": "clock_out"}}
    actions = build_actions_for_isolated(issue, None)
    pair = actions[1]
    assert pair["fixed_params"] == {"date": D, "action": "clock_in"}
    assert "上班" in pair["label"]
    assert actions[0]["params"] == {"id": 7}
