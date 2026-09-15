"""tests for client/src/open_summary.py — 日期规则、文件查找兜底、打开行为。"""

from datetime import datetime
from pathlib import Path

import pytest

import open_summary


@pytest.fixture()
def summaries_dir(tmp_path, monkeypatch):
    """把 SUMMARIES_DIR 重定向到临时目录。"""
    d = tmp_path / "summaries"
    monkeypatch.setattr(open_summary, "SUMMARIES_DIR", d)
    return d


def _freeze_now(monkeypatch, hour, minute, day=2, month=5, year=2024):
    """冻结 open_summary.datetime.now() 到指定时刻。"""

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(year, month, day, hour, minute)

    monkeypatch.setattr(open_summary, "datetime", FakeDateTime)
    return FakeDateTime.now()


def _make_summary(dir_path: Path, date: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    f = dir_path / f"screenshot-summary-{date}.html"
    f.write_text("<html></html>", encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# get_target_date 日期规则
# ---------------------------------------------------------------------------

def test_target_date_after_2340_is_today(monkeypatch):
    """23:40 之后（如 23:45）目标日期为今天。"""
    _freeze_now(monkeypatch, 23, 45)
    assert open_summary.get_target_date() == "2024-05-02"


def test_target_date_exactly_2340_is_today(monkeypatch):
    """边界：正好 23:40 也算"之后"，打开今日。"""
    _freeze_now(monkeypatch, 23, 40)
    assert open_summary.get_target_date() == "2024-05-02"


def test_target_date_2339_is_yesterday(monkeypatch):
    """23:39（23:30-23:40 之间）打开昨日。"""
    _freeze_now(monkeypatch, 23, 39)
    assert open_summary.get_target_date() == "2024-05-01"


def test_target_date_morning_is_yesterday(monkeypatch):
    """上午时间一律打开昨日。"""
    _freeze_now(monkeypatch, 9, 0)
    assert open_summary.get_target_date() == "2024-05-01"


def test_target_date_crosses_month_boundary(monkeypatch):
    """跨月：5 月 1 日上午打开 4 月 30 日的日报。"""
    _freeze_now(monkeypatch, 8, 0, day=1)
    assert open_summary.get_target_date() == "2024-04-30"


# ---------------------------------------------------------------------------
# find_summary_file 查找与兜底
# ---------------------------------------------------------------------------

def test_find_exact_date_file(summaries_dir):
    """目标日期文件存在时精确命中。"""
    f = _make_summary(summaries_dir, "2024-05-02")
    assert open_summary.find_summary_file("2024-05-02") == f


def test_find_falls_back_to_latest(summaries_dir):
    """目标日期不存在时回退到文件名排序的最新一份。"""
    _make_summary(summaries_dir, "2024-04-28")
    latest = _make_summary(summaries_dir, "2024-04-30")
    _make_summary(summaries_dir, "2024-04-29")
    assert open_summary.find_summary_file("2024-05-02") == latest


def test_find_returns_none_when_dir_missing(summaries_dir):
    """summaries 目录都不存在时返回 None。"""
    assert open_summary.find_summary_file("2024-05-02") is None


def test_find_returns_none_when_empty(summaries_dir):
    """目录存在但没有任何日报时返回 None。"""
    summaries_dir.mkdir(parents=True)
    (summaries_dir / "other.txt").write_text("x", encoding="utf-8")
    assert open_summary.find_summary_file("2024-05-02") is None


# ---------------------------------------------------------------------------
# main 端到端（mock 打开动作）
# ---------------------------------------------------------------------------

def test_main_returns_1_when_no_summary(summaries_dir, monkeypatch, capsys):
    """一份日报都没有：返回 1 并打印提示，不调用打开动作。"""
    called = []
    monkeypatch.setattr(open_summary.subprocess, "Popen", lambda *a, **k: called.append(1))
    rc = open_summary.main()
    assert rc == 1
    assert called == []
    out = capsys.readouterr().out
    assert "未找到任何日报" in out


def test_main_opens_exact_file(summaries_dir, monkeypatch, capsys):
    """目标日期日报存在：返回 0，调用 Popen 打开该文件。"""
    now = _freeze_now(monkeypatch, 23, 45)
    f = _make_summary(summaries_dir, now.strftime("%Y-%m-%d"))
    opened = []
    monkeypatch.setattr(open_summary.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    assert open_summary.main() == 0
    assert len(opened) == 1
    assert str(f) in opened[0]
    out = capsys.readouterr().out
    assert "正在打开" in out
    assert "改打开最新一份" not in out


def test_main_falls_back_to_latest_with_message(summaries_dir, monkeypatch, capsys):
    """目标日期缺失但有旧日报：打印"改打开最新一份"提示并打开它。"""
    _freeze_now(monkeypatch, 23, 45)  # 目标 2024-05-02，不存在
    latest = _make_summary(summaries_dir, "2024-04-30")
    opened = []
    monkeypatch.setattr(open_summary.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    assert open_summary.main() == 0
    out = capsys.readouterr().out
    assert "改打开最新一份：screenshot-summary-2024-04-30.html" in out
    assert str(latest) in opened[0]
