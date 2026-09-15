"""tests for client/src/screenshotter.py — 留存清理、缩放/质量参数、摄像头降级。

截屏主流程依赖真实屏幕（ImageGrab.grab），这里 mock 掉；只测纯逻辑。
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import screenshotter


@pytest.fixture()
def shots_dir(tmp_path, monkeypatch):
    """把 SCREENSHOTS_DIR 重定向到临时目录。"""
    d = tmp_path / "screenshots"
    monkeypatch.setattr(screenshotter, "SCREENSHOTS_DIR", d)
    return d


def _make_day_dir(base: Path, date: str, files=("09-00-00.jpg", "10-00-00.jpg")):
    day = base / date
    day.mkdir(parents=True)
    for name in files:
        (day / name).write_bytes(b"\xff\xd8fake-jpeg")
    return day


# ---------------------------------------------------------------------------
# cleanup_old_screenshots 留存期清理
# ---------------------------------------------------------------------------

def test_cleanup_removes_expired_day_dirs(shots_dir):
    """超过留存期的日期目录连同其中 jpg 一起删除。"""
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old_dir = _make_day_dir(shots_dir, old_date)
    screenshotter.cleanup_old_screenshots(7)
    assert not old_dir.exists()


def test_cleanup_keeps_recent_dirs(shots_dir):
    """留存期内的目录保留。"""
    today_dir = _make_day_dir(shots_dir, datetime.now().strftime("%Y-%m-%d"))
    screenshotter.cleanup_old_screenshots(7)
    assert today_dir.exists()
    assert (today_dir / "09-00-00.jpg").exists()


def test_cleanup_skips_non_date_dirs(shots_dir):
    """非日期命名的目录（如 misc）不受影响。"""
    misc = shots_dir / "misc"
    misc.mkdir(parents=True)
    (misc / "a.jpg").write_bytes(b"x")
    screenshotter.cleanup_old_screenshots(7)
    assert (misc / "a.jpg").exists()


def test_cleanup_zero_retention_is_noop(shots_dir):
    """retention_days<=0 时完全不清理。"""
    old_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    old_dir = _make_day_dir(shots_dir, old_date)
    screenshotter.cleanup_old_screenshots(0)
    assert old_dir.exists()


def test_cleanup_runs_once_per_day_via_sentinel(shots_dir):
    """哨兵文件机制：当天第二次调用直接跳过，过期的目录也不会被删。"""
    screenshotter.cleanup_old_screenshots(7)
    assert (shots_dir / ".last_cleanup").exists()
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old_dir = _make_day_dir(shots_dir, old_date)
    screenshotter.cleanup_old_screenshots(7)  # 当天已清理过 → 跳过
    assert old_dir.exists()


def test_cleanup_reruns_after_sentinel_ages(shots_dir):
    """哨兵文件 mtime 是昨天时，清理重新生效。"""
    screenshotter.cleanup_old_screenshots(7)
    sentinel = shots_dir / ".last_cleanup"
    yesterday = time.time() - 86400
    os.utime(sentinel, (yesterday, yesterday))
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old_dir = _make_day_dir(shots_dir, old_date)
    screenshotter.cleanup_old_screenshots(7)
    assert not old_dir.exists()


# ---------------------------------------------------------------------------
# take_screenshot 缩放 / 质量 / 摄像头降级（mock ImageGrab）
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_grab(monkeypatch):
    """用 Pillow 生成 3000x2000 纯红色假屏幕，替代真实截屏。"""
    from PIL import Image

    calls = []

    def grab(all_screens=False):
        calls.append(all_screens)
        return Image.new("RGB", (3000, 2000), (255, 0, 0))

    monkeypatch.setattr("PIL.ImageGrab.grab", grab)
    return calls


def test_take_screenshot_resizes_to_max_dimension(shots_dir, fake_grab):
    """超过 max_dimension 时按比例缩小，最长边恰好等于 max_dimension。"""
    from PIL import Image

    out = shots_dir / "x.jpg"
    screenshotter.take_screenshot(quality=60, max_dimension=1200, output_path=out)
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.size == (1200, 800)  # 3000x2000 × 0.4
    assert fake_grab == [True]


def test_take_screenshot_no_resize_when_small(shots_dir, monkeypatch):
    """小于 max_dimension 时不缩放，尺寸保持原样。"""
    from PIL import Image

    monkeypatch.setattr("PIL.ImageGrab.grab", lambda all_screens=True: Image.new("RGB", (800, 600)))
    out = shots_dir / "x.jpg"
    screenshotter.take_screenshot(quality=60, max_dimension=1280, output_path=out)
    with Image.open(out) as img:
        assert img.size == (800, 600)


def test_take_screenshot_quality_affects_file_size(shots_dir, monkeypatch):
    """quality 参数生效：带渐变的图 quality=20 的文件显著小于 quality=95。"""
    from PIL import Image

    # 纯色图压缩后都很小，用渐变图让质量差异体现出来
    grad = Image.linear_gradient("L").resize((2000, 1200)).convert("RGB")
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda all_screens=True: grad)
    low = shots_dir / "low.jpg"
    high = shots_dir / "high.jpg"
    screenshotter.take_screenshot(quality=20, max_dimension=2000, output_path=low)
    screenshotter.take_screenshot(quality=95, max_dimension=2000, output_path=high)
    assert low.stat().st_size < high.stat().st_size


def test_take_screenshot_creates_parent_dirs(shots_dir, fake_grab):
    """输出路径的父目录不存在时自动创建。"""
    out = shots_dir / "2024-05-02" / "09-00-00.jpg"
    screenshotter.take_screenshot(quality=60, max_dimension=1280, output_path=out)
    assert out.exists()


def test_take_screenshot_webcam_import_error_degrades(shots_dir, fake_grab, monkeypatch, capsys):
    """摄像头不可用（cv2 未安装）时打印警告、跳过摄像头，截屏仍正常保存。"""
    monkeypatch.setitem(sys.modules, "cv2", None)  # import cv2 → ImportError
    out = shots_dir / "x.jpg"
    screenshotter.take_screenshot(quality=60, max_dimension=1280, output_path=out, capture_webcam=True)
    assert out.exists()
    assert "SCREENSHOT_WARNING" in capsys.readouterr().err


def test_take_screenshot_webcam_failure_degrades(shots_dir, fake_grab, monkeypatch, capsys):
    """摄像头打开失败（isOpened=False）时静默降级，只保存屏幕截图。"""
    from types import SimpleNamespace

    class FakeCap:
        def isOpened(self):
            return False

    fake_cv2 = SimpleNamespace(VideoCapture=lambda idx: FakeCap())
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    out = shots_dir / "x.jpg"
    screenshotter.take_screenshot(quality=60, max_dimension=1280, output_path=out, capture_webcam=True)
    assert out.exists()


# ---------------------------------------------------------------------------
# main 的参数处理
# ---------------------------------------------------------------------------

def test_main_cleanup_only(shots_dir, tmp_path):
    """--cleanup-only 只清理不截屏：返回 0，且旧的日期目录被清掉。"""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"screenshot_retention_days": 7}), encoding="utf-8")
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old_dir = _make_day_dir(shots_dir, old_date)
    assert screenshotter.main(["--config", str(cfg), "--cleanup-only"]) == 0
    assert not old_dir.exists()


def test_main_screenshot_disabled_returns_0(shots_dir, tmp_path, monkeypatch):
    """screenshot_enabled=false 时 main 直接返回 0，不发生截屏。"""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"screenshot_enabled": False}), encoding="utf-8")

    def forbidden(all_screens=True):
        raise AssertionError("should not grab")

    monkeypatch.setattr("PIL.ImageGrab.grab", forbidden)
    assert screenshotter.main(["--config", str(cfg)]) == 0
