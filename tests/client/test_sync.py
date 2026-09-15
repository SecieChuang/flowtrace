"""tests for client/src/sync.py — 子命令分发、server_base_url 解析、离线兜底。"""

import importlib.util
import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
import requests

import sync
from conftest import FakeResponse

# 所有用例都把离线队列重定向到 tmp，避免 _post 里的 flush 碰到真实 client/backup/
pytestmark = pytest.mark.usefixtures("queue_dir")

SYNC_FILE = sync.__file__


def _load_sync_fresh(monkeypatch, config):
    """以给定 config 内容重新执行 sync.py 顶层代码，返回新模块对象。

    用于测试 SERVER/API_KEY 等模块级常量的解析逻辑（它们只在 import 时计算一次）。
    """
    import io
    import os

    if config is None:
        monkeypatch.setattr(os.path, "exists", lambda p: False)
    else:
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        # json.load 已被替换，文件内容不重要；open 也必须 mock，
        # 否则依赖开发机上恰好存在的真实 config.json（CI 上不存在）
        monkeypatch.setattr(json, "load", lambda f: config)
        monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO("{}"))
    spec = importlib.util.spec_from_file_location("sync_fresh", SYNC_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# server_base_url 解析（含空字符串回退的回归测试）
# ---------------------------------------------------------------------------

def test_server_url_empty_string_falls_back_to_default(monkeypatch):
    """回归：config 里 server_base_url 为空字符串时必须回退 localhost:8000，而不是拼出非法 URL。"""
    mod = _load_sync_fresh(monkeypatch, {"server_base_url": ""})
    assert mod.SERVER == "http://localhost:8000"


def test_server_url_whitespace_falls_back_to_default(monkeypatch):
    """空白字符串同样回退默认地址。"""
    mod = _load_sync_fresh(monkeypatch, {"server_base_url": "   "})
    assert mod.SERVER == "http://localhost:8000"


def test_server_url_missing_key_uses_default(monkeypatch):
    """config 里没有 server_base_url 键时使用默认地址。"""
    mod = _load_sync_fresh(monkeypatch, {"api_key": "abc"})
    assert mod.SERVER == "http://localhost:8000"
    assert mod.API_KEY == "abc"


def test_server_url_config_missing_file_uses_default(monkeypatch):
    """config 文件不存在时使用默认地址且 API_KEY 为空。"""
    mod = _load_sync_fresh(monkeypatch, None)
    assert mod.SERVER == "http://localhost:8000"
    assert mod.API_KEY == ""


def test_server_url_trailing_slash_stripped(monkeypatch):
    """配置的地址末尾斜杠被去掉，避免拼接出双斜杠。"""
    mod = _load_sync_fresh(monkeypatch, {"server_base_url": "https://ft.example.com/"})
    assert mod.SERVER == "https://ft.example.com"


# ---------------------------------------------------------------------------
# _post / checkin
# ---------------------------------------------------------------------------

def _patch_post(monkeypatch, resp=None, exc=None, recorder=None):
    def fake_post(url, json=None, timeout=None, headers=None):
        if recorder is not None:
            recorder.append({"url": url, "json": json, "headers": headers})
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(requests, "post", fake_post)


def test_post_sends_to_server_with_api_key(monkeypatch):
    """_post 拼接 SERVER+endpoint，配置 api_key 时带 X-API-Key 头。"""
    monkeypatch.setattr(sync, "SERVER", "http://srv:9000")
    monkeypatch.setattr(sync, "API_KEY", "secret-key")
    calls = []
    _patch_post(monkeypatch, resp=FakeResponse(200, {"ok": True}), recorder=calls)
    result = sync._post("/api/rating", {"value": 4})
    assert calls[0]["url"] == "http://srv:9000/api/rating"
    assert calls[0]["headers"]["X-API-Key"] == "secret-key"
    assert result["http_ok"] is True and result["queued"] is False
    assert result["response"] == {"ok": True}


def test_post_network_error_enqueues(monkeypatch, queue_path):
    """网络异常时 _post 返回 queued=True，事件连同 endpoint 写入离线队列。"""
    _patch_post(monkeypatch, exc=requests.ConnectionError("down"))
    result = sync._post("/api/checkin", {"action": "clock_in"})
    assert result["queued"] is True and result["transport_ok"] is False
    rows = [json.loads(ln) for ln in queue_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["endpoint"] == "/api/checkin"
    assert rows[0]["event"]["action"] == "clock_in"


def test_post_non_json_response_tolerated(monkeypatch):
    """响应不是 JSON 时 response 为 None，但 transport/http 状态仍正确。"""
    _patch_post(monkeypatch, resp=FakeResponse(200, json_error=True))
    result = sync._post("/api/rating", {"value": 1})
    assert result["http_ok"] is True
    assert result["response"] is None


def test_checkin_stored(monkeypatch, capsys):
    """打卡成功：输出 CHECKIN_RESULT|status=stored，message 含落库时间。"""
    _patch_post(monkeypatch, resp=FakeResponse(200, {"kept_time": "09:00:11"}))
    sync.cmd_checkin("clock_in")
    out = capsys.readouterr().out
    assert "CHECKIN_RESULT|status=stored|action=clock_in" in out
    assert "上班打卡已落库 09:00:11" in out


def test_checkin_deduped(monkeypatch, capsys):
    """服务端去重：status=deduped，提示保留的时间。"""
    _patch_post(monkeypatch, resp=FakeResponse(200, {"deduped": True, "kept_time": "09:00:05"}))
    sync.cmd_checkin("clock_in")
    out = capsys.readouterr().out
    assert "status=deduped" in out
    assert "重复打卡已忽略，保留 09:00:05" in out


def test_checkin_http_error(monkeypatch, capsys):
    """HTTP 500：status=failed 且 message 含状态码，不入队。"""
    _patch_post(monkeypatch, resp=FakeResponse(500))
    sync.cmd_checkin("clock_out")
    out = capsys.readouterr().out
    assert "status=failed|action=clock_out" in out
    assert "打卡失败（HTTP 500）" in out


def test_checkin_network_error_queued(monkeypatch, capsys, queue_path):
    """打卡时网络失败：status=queued 且事件进入离线队列。"""
    _patch_post(monkeypatch, exc=requests.Timeout("slow"))
    sync.cmd_checkin("clock_in")
    out = capsys.readouterr().out
    assert "status=queued" in out
    assert "已入离线队列：上班打卡" in out
    assert queue_path.exists()


def test_checkin_writes_result_file(monkeypatch, tmp_path, capsys):
    """设置 RESULT_FILE 后，结果行同时写入该文件（供 AHK 无控制台读取）。"""
    result_file = tmp_path / "out" / "result.txt"
    monkeypatch.setattr(sync, "RESULT_FILE", str(result_file))
    _patch_post(monkeypatch, resp=FakeResponse(200, {"kept_time": "18:00:00"}))
    sync.cmd_checkin("clock_out")
    content = result_file.read_text(encoding="utf-8").strip()
    assert content.startswith("CHECKIN_RESULT|status=stored|action=clock_out")


# ---------------------------------------------------------------------------
# activity / rating / flush 子命令
# ---------------------------------------------------------------------------

def test_activity_start_payload(monkeypatch, capsys):
    """activity start 显式时间：POST 到 /api/activity，tags 固定为 auto。"""
    calls = []
    _patch_post(monkeypatch, resp=FakeResponse(200, {}), recorder=calls)
    sync.cmd_activity("start", "09:30:00")
    data = calls[0]["json"]
    assert calls[0]["url"].endswith("/api/activity")
    assert data["start_time"] == "09:30:00"
    assert data["end_time"] == ""
    assert data["tags"] == "auto"
    assert data["date"] == datetime.now().strftime("%Y-%m-%d")
    assert "活跃度同步成功" in capsys.readouterr().out


def test_activity_explicit_date_arg(monkeypatch):
    """activity 子命令首参为 YYYY-MM-DD 时作为日期，其余参数顺移。"""
    calls = []
    _patch_post(monkeypatch, resp=FakeResponse(200, {}), recorder=calls)
    sync.cmd_activity("update", "2024-01-02", "09:00:00", "10:00:00")
    data = calls[0]["json"]
    assert data["date"] == "2024-01-02"
    assert data["start_time"] == "09:00:00"
    assert data["end_time"] == "10:00:00"


def test_activity_end_payload(monkeypatch):
    """activity end 带起止时间。"""
    calls = []
    _patch_post(monkeypatch, resp=FakeResponse(200, {}), recorder=calls)
    sync.cmd_activity("end", "09:30:00", "11:20:00")
    data = calls[0]["json"]
    assert data["start_time"] == "09:30:00"
    assert data["end_time"] == "11:20:00"


def test_activity_unknown_subcommand(monkeypatch, capsys):
    """未知 activity 子命令：打印提示且不发出任何 HTTP 请求。"""
    calls = []
    _patch_post(monkeypatch, resp=FakeResponse(200, {}), recorder=calls)
    sync.cmd_activity("bogus")
    assert "未知 activity 子命令: bogus" in capsys.readouterr().out
    assert calls == []


def test_activity_network_error_queued(monkeypatch, capsys, queue_path):
    """activity 网络失败时提示已排队且事件入队。"""
    _patch_post(monkeypatch, exc=requests.ConnectionError("down"))
    sync.cmd_activity("start", "09:00:00")
    assert "活跃度已排队: start" in capsys.readouterr().out
    rows = [json.loads(ln) for ln in queue_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["endpoint"] == "/api/activity"


def test_rating_posts_int_value(monkeypatch, capsys):
    """rating 子命令把字符串参数转成 int 后 POST。"""
    calls = []
    _patch_post(monkeypatch, resp=FakeResponse(200, {}), recorder=calls)
    sync.cmd_rating("4")
    assert calls[0]["json"]["value"] == 4
    assert "评分同步成功: 4" in capsys.readouterr().out


def test_rating_network_error_queued(monkeypatch, capsys, queue_path):
    """rating 网络失败时入队 /api/rating。"""
    _patch_post(monkeypatch, exc=requests.ConnectionError("down"))
    sync.cmd_rating("2")
    assert "评分已排队: 2" in capsys.readouterr().out
    rows = [json.loads(ln) for ln in queue_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["endpoint"] == "/api/rating"
    assert rows[0]["event"]["value"] == 2


def test_cmd_flush_success(monkeypatch, capsys):
    """flush 成功打印完成提示。"""
    monkeypatch.setattr(sync, "flush", lambda server, key: True)
    sync.cmd_flush()
    assert "队列刷新完成" in capsys.readouterr().out


def test_cmd_flush_failure(monkeypatch, capsys):
    """flush 失败打印失败提示。"""
    monkeypatch.setattr(sync, "flush", lambda server, key: False)
    sync.cmd_flush()
    assert "队列刷新失败" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main 分发 与 screenshot 决策
# ---------------------------------------------------------------------------

def test_main_dispatches_rating(monkeypatch, tmp_path, capsys):
    """main 按 argv 分发到 rating，--result-file 参数先被剥离。"""
    result_file = str(tmp_path / "r.txt")
    monkeypatch.setattr(sys, "argv", ["sync.py", "--result-file", result_file, "rating", "5"])
    _patch_post(monkeypatch, resp=FakeResponse(200, {}))
    sync.main()
    assert "评分同步成功: 5" in capsys.readouterr().out


def test_main_unknown_command_exits_1(monkeypatch, capsys):
    """未知命令打印提示并以 exit code 1 退出。"""
    monkeypatch.setattr(sys, "argv", ["sync.py", "frobnicate"])
    with pytest.raises(SystemExit) as ei:
        sync.main()
    assert ei.value.code == 1
    assert "未知命令: frobnicate" in capsys.readouterr().out


def test_main_no_args_exits_1(monkeypatch):
    """无参数时打印用法并退出 1。"""
    monkeypatch.setattr(sys, "argv", ["sync.py"])
    with pytest.raises(SystemExit) as ei:
        sync.main()
    assert ei.value.code == 1


def _fake_completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_screenshot_local_on_duty_uses_webcam(monkeypatch, tmp_path, capsys):
    """本地状态在班：不查服务端，直接以 --webcam 调 screenshotter。"""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"is_on_duty": True}), encoding="utf-8")
    monkeypatch.setattr(sync, "STATE_PATH", state)
    get_called = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: get_called.append(1))
    runs = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: runs.append(cmd) or _fake_completed(stdout="SCREENSHOT_OK"))
    sync.cmd_screenshot()
    out = capsys.readouterr().out
    assert "local_on_duty=true" in out
    assert "server_checked=false" in out
    assert "webcam_flag=--webcam" in out
    assert "--webcam" in runs[0]
    assert get_called == []


def test_screenshot_server_explicit_open_corrects_local_state(monkeypatch, tmp_path, capsys):
    """本地不在班但服务端 explicit_open_clock_in_now=true：纠正本地状态并用 --webcam。"""
    state = tmp_path / "state.json"
    monkeypatch.setattr(sync, "STATE_PATH", state)
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: FakeResponse(200, {"on_duty_now": True, "explicit_open_clock_in_now": True}),
    )
    runs = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: runs.append(cmd) or _fake_completed(stdout="SCREENSHOT_OK"))
    sync.cmd_screenshot()
    out = capsys.readouterr().out
    assert "server_checked=true" in out
    assert "explicit_open_clock_in_now=true" in out
    assert "local_state_corrected=true" in out
    assert "webcam_flag=--webcam" in out
    # 本地状态被纠正为在班
    assert json.loads(state.read_text(encoding="utf-8"))["is_on_duty"] is True


def test_screenshot_off_duty_uses_no_webcam(monkeypatch, tmp_path, capsys):
    """本地、服务端都不在班：使用 --no-webcam。"""
    monkeypatch.setattr(sync, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, {"on_duty_now": False}))
    runs = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: runs.append(cmd) or _fake_completed(stdout=""))
    sync.cmd_screenshot()
    out = capsys.readouterr().out
    assert "webcam_flag=--no-webcam" in out
    assert "--no-webcam" in runs[0]


def test_screenshot_subprocess_error_reported(monkeypatch, tmp_path, capsys):
    """screenshotter 子进程抛异常时输出 SCREENSHOT_ERROR。"""
    monkeypatch.setattr(sync, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, {"on_duty_now": False}))

    def boom(*a, **k):
        raise OSError("cannot spawn")

    monkeypatch.setattr("subprocess.run", boom)
    sync.cmd_screenshot()
    assert "SCREENSHOT_ERROR|error=" in capsys.readouterr().out
