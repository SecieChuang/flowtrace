"""server.py HTTP API 测试（Flask test client + 临时 SQLite 库）。

基准日期取 2026-08-28（周五，历史日期），避免命中"今天"的实时窗口逻辑。
配置为空（conftest 重定向 CONFIG_PATH 到不存在的临时文件），因此：
- API_KEY = ""（写接口放行；401 分支用 monkeypatch 改 server.API_KEY 测）
- CHECKIN_DEDUPE_SECONDS = 60
- INFER_MERGE_GAP_MINUTES = 40
"""

import re
from datetime import datetime

import pytest

import server
from server_helpers import insert_activity, insert_checkin

D = "2026-08-28"  # 周五
D_NEXT = "2026-08-29"
WEEK_MONDAY = "2026-08-24"  # D 所在周的周一


def _seed_workday(conn, date=D):
    """标准工作日：09:00 上班卡 + 18:00 下班卡 + 09:00-18:00 连续活跃。

    预期指标：on_duty = active = effective = 540 分钟。
    """
    insert_checkin(conn, "clock_in", date, "09:00:00")
    insert_checkin(conn, "clock_out", date, "18:00:00")
    insert_activity(conn, date, "09:00:00", "18:00:00")


# ── /api/ping ─────────────────────────────────────────


def test_ping_returns_ok(client):
    """GET /api/ping 返回 200 且 status 为 ok。"""
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


# ── /api/checkin ──────────────────────────────────────


def test_checkin_stores_clock_in(client, db):
    """正常上班卡：success/deduped=False/kept_time 等于提交时间，落库一行。"""
    resp = client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:00"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["deduped"] is False
    assert body["kept_time"] == "09:00:00"
    assert body["ignored_time"] is None
    assert body["message"] == "checkin_stored"

    rows = db.execute("SELECT action, date, time FROM checkins").fetchall()
    assert len(rows) == 1
    assert (rows[0]["action"], rows[0]["date"], rows[0]["time"]) == ("clock_in", D, "09:00:00")


@pytest.mark.parametrize("payload", [{"action": "login"}, {"action": ""}, {}])
def test_checkin_rejects_invalid_action(client, db, payload):
    """action 缺失或不是 clock_in/clock_out 时返回 400，且不落库。"""
    resp = client.post("/api/checkin", json={**payload, "date": D, "time": "09:00:00"})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert db.execute("SELECT COUNT(*) AS c FROM checkins").fetchone()["c"] == 0


def test_checkin_dedupe_within_threshold(client, db):
    """60 秒去重窗口内同种卡被忽略：保留第一条，响应 deduped=True。"""
    client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:00"})
    resp = client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:30"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["deduped"] is True
    assert body["kept_time"] == "09:00:00"
    assert body["ignored_time"] == "09:00:30"
    assert body["message"] == "duplicate_checkin_ignored"
    assert db.execute("SELECT COUNT(*) AS c FROM checkins").fetchone()["c"] == 1


def test_checkin_dedupe_boundary_59s_deduped(client, db):
    """间隔 59 秒（< 60）仍被去重。"""
    client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:00"})
    resp = client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:59"})
    assert resp.get_json()["deduped"] is True
    assert db.execute("SELECT COUNT(*) AS c FROM checkins").fetchone()["c"] == 1


def test_checkin_dedupe_boundary_60s_stored(client, db):
    """间隔恰好 60 秒（不小于阈值）不去重，两条都落库。"""
    client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:00"})
    resp = client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:01:00"})
    assert resp.get_json()["deduped"] is False
    assert db.execute("SELECT COUNT(*) AS c FROM checkins").fetchone()["c"] == 2


def test_checkin_dedupe_only_applies_to_same_action(client, db):
    """不同 action 不参与去重：上班卡后 10 秒的下班卡正常落库。"""
    client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": "09:00:00"})
    resp = client.post("/api/checkin", json={"action": "clock_out", "date": D, "time": "09:00:10"})
    assert resp.get_json()["deduped"] is False
    assert db.execute("SELECT COUNT(*) AS c FROM checkins").fetchone()["c"] == 2


def test_checkin_invalid_date_falls_back_to_today(client, db):
    """非法日期（如 2026-13-40）回退为今天。"""
    resp = client.post("/api/checkin", json={"action": "clock_in", "date": "2026-13-40", "time": "09:00:00"})
    today = datetime.now().strftime("%Y-%m-%d")
    body = resp.get_json()
    assert body["success"] is True
    assert body["date"] == today
    assert db.execute("SELECT date FROM checkins").fetchone()["date"] == today


@pytest.mark.parametrize("bad_time", ["25:00:00", "12:60:00", "24:00:00", "9:00:00", "abc"])
def test_checkin_invalid_time_falls_back_to_now(client, db, bad_time):
    """非法时间（含 checkin 不允许的 24:00:00）回退为当前时间，不按原样入库。"""
    resp = client.post("/api/checkin", json={"action": "clock_in", "date": D, "time": bad_time})
    body = resp.get_json()
    assert body["success"] is True
    assert body["time"] != bad_time
    assert re.match(r"^\d{2}:\d{2}:\d{2}$", body["time"])
    assert db.execute("SELECT time FROM checkins").fetchone()["time"] == body["time"]


# ── /api/activity ─────────────────────────────────────


def test_activity_requires_start_time(client):
    """缺 start_time 返回 400。"""
    resp = client.post("/api/activity", json={"date": D, "end_time": "10:00:00"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "start_time required"


@pytest.mark.parametrize("bad_start", ["9:00:00", "24:00:00", "25:00:00", "abc"])
def test_activity_rejects_invalid_start_time(client, bad_start):
    """start_time 非法（含 24:00:00，起点不允许）返回 400。"""
    resp = client.post("/api/activity", json={"date": D, "start_time": bad_start, "end_time": "10:00:00"})
    assert resp.status_code == 400


def test_activity_same_day_insert_computes_duration(client, db):
    """普通同日切片：落库并计算 duration_minutes=60.0。"""
    resp = client.post("/api/activity", json={"date": D, "start_time": "09:00:00", "end_time": "10:00:00"})
    assert resp.get_json()["success"] is True
    row = db.execute("SELECT * FROM activity_slices").fetchone()
    assert (row["date"], row["start_time"], row["end_time"]) == (D, "09:00:00", "10:00:00")
    assert row["duration_minutes"] == 60.0
    assert row["tags"] == "auto"


def test_activity_open_slice_stores_null_end(client, db):
    """不传 end_time 存为开放切片：end_time 与 duration 均为 NULL。"""
    resp = client.post("/api/activity", json={"date": D, "start_time": "09:00:00"})
    assert resp.get_json()["success"] is True
    row = db.execute("SELECT * FROM activity_slices").fetchone()
    assert row["end_time"] is None
    assert row["duration_minutes"] is None


def test_activity_overnight_splits_across_midnight(client, db):
    """跨零点切片拆成两行：D 23:00-24:00（60 分钟）+ D+1 00:00-01:00（60 分钟）。"""
    resp = client.post("/api/activity", json={"date": D, "start_time": "23:00:00", "end_time": "01:00:00"})
    assert resp.get_json()["success"] is True
    rows = db.execute("SELECT * FROM activity_slices ORDER BY date, start_time").fetchall()
    assert len(rows) == 2
    assert (rows[0]["date"], rows[0]["start_time"], rows[0]["end_time"], rows[0]["duration_minutes"]) == (
        D, "23:00:00", "24:00:00", 60.0,
    )
    assert (rows[1]["date"], rows[1]["start_time"], rows[1]["end_time"], rows[1]["duration_minutes"]) == (
        D_NEXT, "00:00:00", "01:00:00", 60.0,
    )


def test_activity_end_2400_is_single_slice(client, db):
    """end_time=24:00:00 是当日结束哨兵：不拆分，时长算到午夜。"""
    client.post("/api/activity", json={"date": D, "start_time": "23:00:00", "end_time": "24:00:00"})
    rows = db.execute("SELECT * FROM activity_slices").fetchall()
    assert len(rows) == 1
    assert rows[0]["end_time"] == "24:00:00"
    assert rows[0]["duration_minutes"] == 60.0


def test_activity_wrong_date_overnight_corrected_to_previous_day(client, db):
    """隔夜切片的常见日期错误：date 给成次日，但前一日有同 start 的开放切片时改写前一日。"""
    insert_activity(db, D, "23:30:00", None)  # 前一日未闭合切片
    resp = client.post(
        "/api/activity",
        json={"date": D_NEXT, "start_time": "23:30:00", "end_time": "01:00:00"},
    )
    assert resp.get_json()["success"] is True
    rows = db.execute("SELECT * FROM activity_slices ORDER BY date, start_time").fetchall()
    assert len(rows) == 2
    # 前一日的开放切片被闭合（UPDATE 而非新增）
    assert (rows[0]["date"], rows[0]["start_time"], rows[0]["end_time"], rows[0]["duration_minutes"]) == (
        D, "23:30:00", "24:00:00", 30.0,
    )
    assert (rows[1]["date"], rows[1]["start_time"], rows[1]["end_time"], rows[1]["duration_minutes"]) == (
        D_NEXT, "00:00:00", "01:00:00", 60.0,
    )


def test_activity_upsert_same_date_and_start(client, db):
    """同 (date, start_time) 重复提交执行 UPDATE：只一行，结束时间以最后提交为准。"""
    client.post("/api/activity", json={"date": D, "start_time": "09:00:00", "end_time": "10:00:00"})
    client.post("/api/activity", json={"date": D, "start_time": "09:00:00", "end_time": "11:30:00"})
    rows = db.execute("SELECT * FROM activity_slices").fetchall()
    assert len(rows) == 1
    assert rows[0]["end_time"] == "11:30:00"
    assert rows[0]["duration_minutes"] == 150.0


def test_activity_invalid_end_time_becomes_open_slice(client, db):
    """end_time 非法时被清空为开放切片而不是报错。"""
    resp = client.post("/api/activity", json={"date": D, "start_time": "09:00:00", "end_time": "abc"})
    assert resp.get_json()["success"] is True
    row = db.execute("SELECT * FROM activity_slices").fetchone()
    assert row["end_time"] is None
    assert row["duration_minutes"] is None


# ── /api/activity/batch ───────────────────────────────


def test_activity_batch_processes_all_events(client, db):
    """批量接口处理全部事件（含跨零点拆分），processed 等于事件总数。"""
    resp = client.post(
        "/api/activity/batch",
        json={
            "events": [
                {"date": D, "start_time": "09:00:00", "end_time": "10:00:00"},
                {"date": D, "start_time": "10:30:00", "end_time": "12:00:00"},
                {"date": D, "start_time": "23:00:00", "end_time": "01:00:00"},
            ]
        },
    )
    body = resp.get_json()
    assert body["success"] is True
    assert body["processed"] == 3
    rows = db.execute("SELECT * FROM activity_slices ORDER BY date, start_time").fetchall()
    assert len(rows) == 4  # 跨零点事件拆成两行
    assert rows[2]["date"] == D and rows[2]["end_time"] == "24:00:00"
    assert rows[3]["date"] == D_NEXT and rows[3]["start_time"] == "00:00:00"


def test_activity_batch_skips_invalid_events_but_counts_them(client, db):
    """缺 date/start_time 的事件被跳过落库，但 processed 仍按原始事件数返回。"""
    resp = client.post(
        "/api/activity/batch",
        json={
            "events": [
                {"date": D, "start_time": "09:00:00", "end_time": "10:00:00"},
                {"date": "", "start_time": "11:00:00", "end_time": "12:00:00"},
                {"date": D, "start_time": "", "end_time": "12:00:00"},
            ]
        },
    )
    assert resp.get_json()["processed"] == 3
    rows = db.execute("SELECT * FROM activity_slices").fetchall()
    assert len(rows) == 1
    assert rows[0]["start_time"] == "09:00:00"


def test_activity_batch_empty_events(client, db):
    """空事件列表返回成功且 processed=0。"""
    resp = client.post("/api/activity/batch", json={"events": []})
    body = resp.get_json()
    assert body["success"] is True
    assert body["processed"] == 0


# ── /api/rating ───────────────────────────────────────


@pytest.mark.parametrize("value", [1, 5])
def test_rating_accepts_boundary_values(client, db, value):
    """评分边界值 1 与 5 均可落库。"""
    resp = client.post("/api/rating", json={"date": D, "time": "21:00:00", "value": value})
    assert resp.get_json()["success"] is True
    assert db.execute("SELECT value FROM ratings").fetchone()["value"] == value


@pytest.mark.parametrize("value", [0, 6, -3, "4", 4.5, None])
def test_rating_rejects_out_of_range_or_non_int(client, db, value):
    """评分非 1-5 的整数（含字符串/浮点/缺失）返回 400，不落库。"""
    resp = client.post("/api/rating", json={"date": D, "time": "21:00:00", "value": value})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "value must be 1-5"
    assert db.execute("SELECT COUNT(*) AS c FROM ratings").fetchone()["c"] == 0


# ── /api/dashboard/daily ──────────────────────────────


def test_daily_empty_day_structure(client):
    """无数据日：分钟数全 0、列表全空，时间轴为一段覆盖全天的 offline。"""
    resp = client.get(f"/api/dashboard/daily?date={D}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["date"] == D
    assert body["on_duty_minutes"] == 0
    assert body["active_minutes"] == 0
    assert body["effective_minutes"] == 0
    assert body["focus_ratio"] == 0
    assert body["checkin_intervals"] == []
    assert body["checkin_marks"] == []
    assert body["activity_slices"] == []
    assert body["ratings"] == []
    assert len(body["timeline_segments"]) == 1
    seg = body["timeline_segments"][0]
    assert (seg["start"], seg["end"]) == ("00:00:00", "24:00:00")
    assert seg["display_state"] == "offline"
    assert seg["on_duty"] is False


def test_daily_workday_minutes(client, db):
    """标准工作日：on_duty=active=effective=540 分钟，focus_ratio=100，在岗区间 09:00-18:00。"""
    _seed_workday(db)
    body = client.get(f"/api/dashboard/daily?date={D}").get_json()
    assert body["on_duty_minutes"] == 540.0
    assert body["active_minutes"] == 540.0
    assert body["effective_minutes"] == 540.0
    assert body["focus_ratio"] == 100.0
    assert body["checkin_intervals"] == [{"start": "09:00:00", "end": "18:00:00"}]
    assert len(body["activity_slices"]) == 1
    assert len(body["checkin_marks"]) == 2
    assert all(not m["inferred"] for m in body["checkin_marks"])


def test_daily_ratings_included(client, db):
    """评分出现在 daily 响应中；无打卡无活跃时分钟数仍为 0。"""
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (D, "12:00:00", 4))
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (D, "18:00:00", 2))
    db.commit()
    body = client.get(f"/api/dashboard/daily?date={D}").get_json()
    assert [r["value"] for r in body["ratings"]] == [4, 2]
    assert body["on_duty_minutes"] == 0
    assert body["focus_ratio"] == 0


# ── /api/dashboard/weekly ─────────────────────────────


def test_weekly_aggregation(client, db):
    """周视图：7 天结构 + 汇总（540 分钟 = 9 小时），days_with_data 只数有在岗的天。"""
    _seed_workday(db)  # 2026-08-28 是该周的周五
    body = client.get(f"/api/dashboard/weekly?week_start={WEEK_MONDAY}").get_json()
    assert body["week_start"] == WEEK_MONDAY
    assert body["week_end"] == "2026-08-30"
    assert body["week_start_day"] == "monday"
    assert len(body["days"]) == 7
    assert body["days"][4]["date"] == D
    assert body["days"][4]["on_duty_minutes"] == 540.0
    assert body["days_with_data"] == 1
    assert body["total_hours"] == 9.0
    assert body["active_hours"] == 9.0
    assert body["effective_hours"] == 9.0
    assert body["active_off_duty_hours"] == 0.0
    assert body["focus_ratio"] == 100.0


# ── /api/dashboard/history ────────────────────────────


def test_history_series_and_summary(client, db):
    """历史序列：逐日字段（首末卡、评分均值）与汇总小时数正确。"""
    _seed_workday(db)
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (D, "12:00:00", 4))
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (D, "18:00:00", 2))
    db.commit()
    body = client.get(f"/api/dashboard/history?days=7&end_date={D}").get_json()
    assert body["from"] == "2026-08-22"
    assert body["to"] == D
    assert len(body["days"]) == 7
    day = body["days"][-1]
    assert day["date"] == D
    assert day["on_duty_hours"] == 9.0
    assert day["active_hours"] == 9.0
    assert day["effective_hours"] == 9.0
    assert day["focus_ratio"] == 100.0
    assert day["first_clock_in"] == "09:00:00"
    assert day["last_clock_out"] == "18:00:00"
    assert day["rating_avg"] == 3.0
    assert day["rating_count"] == 2
    assert body["summary"] == {"on_duty_hours": 9.0, "effective_hours": 9.0, "focus_ratio": 100.0}


def test_history_days_clamped_to_minimum_7(client):
    """days=3 被钳制到最小 7 天窗口。"""
    body = client.get(f"/api/dashboard/history?days=3&end_date={D}").get_json()
    assert body["from"] == "2026-08-22"
    assert body["to"] == D
    assert len(body["days"]) == 7


# ── /api/dashboard/stats ──────────────────────────────


def test_stats_with_workday(client, db):
    """趣味统计：最早打卡/最晚下班/专注巅峰/黄金小时取自标准工作日数据。"""
    _seed_workday(db)
    body = client.get(f"/api/dashboard/stats?days=30&end_date={D}").get_json()
    assert body["early"] == {"time": "09:00", "date": D}
    assert body["late"] == {"time": "18:00", "date": D}
    assert body["peak"] == {"min": 540.0, "date": D}
    # 09:00-18:00 连续有效活跃：小时 9..17 各 60 分钟，取第一个峰值小时 9
    assert body["golden"] == {"hour": "9点到10点", "minutes": 60.0, "basis": "effective"}


def test_stats_empty_database(client):
    """无任何数据时四项统计均为 None。"""
    body = client.get(f"/api/dashboard/stats?days=30&end_date={D}").get_json()
    assert body["early"] is None
    assert body["late"] is None
    assert body["peak"] is None
    assert body["golden"] is None


# ── /api/dashboard/table ──────────────────────────────


def test_table_row_content(client, db):
    """表格行：工时格式 H:MM、小时数、标签、段数、评分均值齐全且正确。"""
    _seed_workday(db)
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (D, "12:00:00", 4))
    db.execute("INSERT INTO ratings (date, time, value) VALUES (?, ?, ?)", (D, "18:00:00", 2))
    db.commit()
    body = client.get(f"/api/dashboard/table?from={D}&to={D}").get_json()
    assert body["from"] == D
    assert body["to"] == D
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["date"] == D
    assert row["total_work_time"] == "9:00"
    assert row["on_duty_hours"] == 9.0
    assert row["active_hours"] == 9.0
    assert row["effective_hours"] == 9.0
    assert row["focus_ratio"] == 100.0
    assert row["tags"] == ["auto"]
    assert row["segments"] == 1
    assert row["rating_avg"] == 3.0
    assert row["rating_count"] == 2


def test_table_empty_range_returns_no_rows(client):
    """范围内无任何数据时 rows 为空。"""
    body = client.get(f"/api/dashboard/table?from={D}&to={D}").get_json()
    assert body["rows"] == []


def test_table_swaps_inverted_range(client, db):
    """from 晚于 to 时自动交换，仍能取到数据。"""
    _seed_workday(db)
    body = client.get(f"/api/dashboard/table?from={D}&to={WEEK_MONDAY}").get_json()
    assert [r["date"] for r in body["rows"]] == [D]


def test_table_invalid_dates_return_empty_rows(client):
    """非法日期参数返回空 rows 而不是报错。"""
    body = client.get("/api/dashboard/table?from=garbage&to=2026-13-99").get_json()
    assert body == {"rows": []}


# ── 鉴权（API_KEY 非空时写接口要求 X-API-Key）─────────


def test_write_endpoints_reject_missing_or_wrong_key(client, monkeypatch):
    """设置 API_KEY 后，无头/错误头的写请求一律 401 unauthorized。"""
    monkeypatch.setattr(server, "API_KEY", "secret")
    for method, path, payload in [
        ("post", "/api/checkin", {"action": "clock_in", "date": D, "time": "09:00:00"}),
        ("post", "/api/activity", {"date": D, "start_time": "09:00:00"}),
        ("post", "/api/activity/batch", {"events": []}),
        ("post", "/api/rating", {"date": D, "time": "21:00:00", "value": 4}),
    ]:
        resp = getattr(client, method)(path, json=payload)
        assert resp.status_code == 401, path
        assert resp.get_json() == {"success": False, "error": "unauthorized"}
        resp = getattr(client, method)(path, json=payload, headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401, path


def test_write_endpoint_accepts_correct_key(client, monkeypatch, db):
    """正确的 X-API-Key 头放行写请求。"""
    monkeypatch.setattr(server, "API_KEY", "secret")
    resp = client.post(
        "/api/checkin",
        json={"action": "clock_in", "date": D, "time": "09:00:00"},
        headers={"X-API-Key": "secret"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert db.execute("SELECT COUNT(*) AS c FROM checkins").fetchone()["c"] == 1


def test_read_endpoints_stay_open_with_key_set(client, monkeypatch):
    """API_KEY 设置后读接口（ping/daily）仍不要求鉴权。"""
    monkeypatch.setattr(server, "API_KEY", "secret")
    assert client.get("/api/ping").status_code == 200
    assert client.get(f"/api/dashboard/daily?date={D}").status_code == 200
