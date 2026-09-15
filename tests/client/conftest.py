"""客户端测试共享 fixture。

每个测试都把各模块里的文件路径（离线队列、截屏目录、日报目录、
计划任务 runtime 目录）重定向到 tmp_path，避免碰真实 client/ 下的数据。
"""

import pytest


@pytest.fixture()
def queue_dir(tmp_path, monkeypatch):
    """把 offline_queue 的队列文件重定向到临时目录，返回队列目录 Path。"""
    import offline_queue

    qdir = tmp_path / "queue"
    monkeypatch.setattr(offline_queue, "_QUEUE_DIR", str(qdir))
    monkeypatch.setattr(offline_queue, "_QUEUE_PATH", str(qdir / "sync_queue.jsonl"))
    return qdir


@pytest.fixture()
def queue_path(queue_dir):
    """返回重定向后的队列文件 Path（不一定已存在）。"""
    return queue_dir / "sync_queue.jsonl"


class FakeResponse:
    """模拟 requests.post/get 的返回值。"""

    def __init__(self, status_code: int = 200, payload=None, json_error: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload
