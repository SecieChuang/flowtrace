"""tests for client/src/offline_queue.py — 离线队列的入队/刷新/容错行为。"""

import json

import pytest
import requests

import offline_queue
from conftest import FakeResponse


def _read_lines(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_enqueue_creates_jsonl_with_endpoint(queue_path):
    """入队一条事件后生成 JSONL 文件，单行包含 queued_at/endpoint/event 三个字段。"""
    offline_queue.enqueue("/api/checkin", {"action": "clock_in", "date": "2024-05-01"})
    assert queue_path.exists()
    rows = _read_lines(queue_path)
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "/api/checkin"
    assert rows[0]["event"]["action"] == "clock_in"
    assert "queued_at" in rows[0]


def test_enqueue_appends_multiple_lines(queue_path):
    """连续入队两条事件，文件应有两行且顺序一致。"""
    offline_queue.enqueue("/api/rating", {"value": 4})
    offline_queue.enqueue("/api/activity", {"date": "2024-05-01", "start_time": "09:00:00"})
    rows = _read_lines(queue_path)
    assert len(rows) == 2
    assert rows[0]["endpoint"] == "/api/rating"
    assert rows[1]["endpoint"] == "/api/activity"


def test_flush_returns_true_when_queue_missing(queue_dir):
    """队列文件不存在时 flush 直接返回 True（无事可做）。"""
    assert offline_queue.flush("http://localhost:8000") is True


def test_flush_success_posts_each_event_and_clears(queue_path, monkeypatch):
    """全部事件发送成功：返回 True、队列文件与 .processing 都被清理。"""
    offline_queue.enqueue("/api/checkin", {"action": "clock_in"})
    offline_queue.enqueue("/api/rating", {"value": 5})
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append((url, json, headers))
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)
    assert offline_queue.flush("http://example.com/", "k1") is True
    assert [c[0] for c in calls] == ["http://example.com/api/checkin", "http://example.com/api/rating"]
    assert calls[0][2]["X-API-Key"] == "k1"
    assert not queue_path.exists()
    assert not (queue_path.parent / "sync_queue.jsonl.processing").exists()


def test_flush_network_error_requeues_event(queue_path, monkeypatch):
    """网络异常：flush 返回 False，失败事件写回主队列且保留 endpoint 信息。"""

    def boom(url, json=None, timeout=None, headers=None):
        raise requests.ConnectionError("server down")

    monkeypatch.setattr(requests, "post", boom)
    offline_queue.enqueue("/api/checkin", {"action": "clock_out"})
    assert offline_queue.flush("http://example.com") is False
    rows = _read_lines(queue_path)
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "/api/checkin"
    assert rows[0]["event"]["action"] == "clock_out"


def test_flush_http_500_requeues_event(queue_path, monkeypatch):
    """HTTP 500 视为失败：返回 False 且事件回队。"""

    def fake_post(url, json=None, timeout=None, headers=None):
        return FakeResponse(500)

    monkeypatch.setattr(requests, "post", fake_post)
    offline_queue.enqueue("/api/rating", {"value": 3})
    assert offline_queue.flush("http://example.com") is False
    assert len(_read_lines(queue_path)) == 1


def test_flush_tolerates_corrupt_lines(queue_path, monkeypatch):
    """队列里混入损坏行/缺 event 的行：跳过它们，只发送合法事件并整体成功。"""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        "not-json\n"
        + json.dumps({"endpoint": "/api/rating", "event": {"value": 4}})
        + "\n"
        + json.dumps({"endpoint": "", "event": None})
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", fake_post)
    assert offline_queue.flush("http://example.com") is True
    assert calls == ["http://example.com/api/rating"]
    assert not queue_path.exists()


def test_flush_legacy_format_infers_endpoint(queue_path, monkeypatch):
    """兼容旧格式（无 endpoint 字段）：按事件内容推断目标 API。"""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = [
        {"queued_at": "2024-05-01T09:00:00", "event": {"action": "clock_in"}},
        {"queued_at": "2024-05-01T09:01:00", "event": {"value": 4}},
        {"queued_at": "2024-05-01T09:02:00", "event": {"date": "2024-05-01", "start_time": "09:00:00"}},
    ]
    queue_path.write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in legacy) + "\n", encoding="utf-8"
    )
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", fake_post)
    assert offline_queue.flush("http://example.com") is True
    assert calls == [
        "http://example.com/api/checkin",
        "http://example.com/api/rating",
        "http://example.com/api/activity",
    ]


def test_enqueue_during_flush_is_not_lost(queue_path, monkeypatch):
    """flush 处理期间新入队的事件写入新主文件，flush 成功后仍然保留。"""

    def fake_post(url, json=None, timeout=None, headers=None):
        # 模拟处理期间另一进程入队新事件
        offline_queue.enqueue("/api/rating", {"value": 1})
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", fake_post)
    offline_queue.enqueue("/api/checkin", {"action": "clock_in"})
    assert offline_queue.flush("http://example.com") is True
    rows = _read_lines(queue_path)
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "/api/rating"


def test_compact_dedupes_by_endpoint_date_start(queue_path):
    """_compact 按 (endpoint, date, start_time) 去重，保留最后一条，无该键的事件原样保留。"""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    objs = [
        {"queued_at": "2024-05-01T09:00:00", "endpoint": "/api/activity",
         "event": {"date": "2024-05-01", "start_time": "09:00:00", "end_time": ""}},
        {"queued_at": "2024-05-01T09:05:00", "endpoint": "/api/rating", "event": {"value": 3}},
        {"queued_at": "2024-05-01T10:00:00", "endpoint": "/api/activity",
         "event": {"date": "2024-05-01", "start_time": "09:00:00", "end_time": "10:00:00"}},
    ]
    queue_path.write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in objs) + "\n", encoding="utf-8"
    )
    offline_queue._compact()
    rows = _read_lines(queue_path)
    assert len(rows) == 2
    # 保留 rating 和最后一次 activity，按 queued_at 排序
    assert rows[0]["endpoint"] == "/api/rating"
    assert rows[1]["event"]["end_time"] == "10:00:00"


def test_compact_drops_corrupt_lines(queue_path):
    """_compact 容错：坏 JSON 行和缺 event 的行被丢弃。"""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        "garbage\n"
        + json.dumps({"endpoint": "/api/rating"})
        + "\n"
        + json.dumps({"queued_at": "2024-05-01T09:00:00", "endpoint": "/api/rating", "event": {"value": 2}})
        + "\n",
        encoding="utf-8",
    )
    offline_queue._compact()
    rows = _read_lines(queue_path)
    assert len(rows) == 1
    assert rows[0]["event"]["value"] == 2
