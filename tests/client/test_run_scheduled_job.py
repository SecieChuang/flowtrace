"""tests for client/src/run_scheduled_job.py — 锁文件并发保护、日志与历史写入。

锁/历史/日志这些纯文件逻辑可测；真正跑子任务的部分用 mock 的 subprocess.run 代替，
不依赖 Windows 计划任务环境。
"""

import json
import os
import subprocess
import time
from datetime import datetime

import pytest

import run_scheduled_job as rsj


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    """把 runtime 目录、日志/历史路径和 JOB_SPECS 全部重定向到 tmp_path。"""
    runtime = tmp_path / "runtime"
    log_dir = runtime / "logs"
    history = runtime / "job_history.jsonl"
    lock = runtime / "testjob.lock"
    monkeypatch.setattr(rsj, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(rsj, "LOG_DIR", log_dir)
    monkeypatch.setattr(rsj, "HISTORY_PATH", history)
    monkeypatch.setattr(rsj, "JOB_SPECS", {"testjob": {"script": tmp_path / "fake_job.py", "lock": lock}})
    return Simple(runtime=runtime, log_dir=log_dir, history=history, lock=lock, tmp=tmp_path)


class Simple:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _write_config(tmp_path, obj=None):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(obj or {}), encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# acquire_lock / release_lock
# ---------------------------------------------------------------------------

def test_acquire_lock_writes_pid_json(job_env):
    """获得锁后锁文件内容是含 pid/started_at 的 JSON。"""
    pid = rsj.acquire_lock(job_env.lock, stale_seconds=3600)
    assert pid == os.getpid()
    data = json.loads(job_env.lock.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert "started_at" in data


def test_acquire_lock_blocks_second_holder(job_env):
    """锁未释放时第二次 acquire 抛 FileExistsError（O_EXCL 语义）。"""
    rsj.acquire_lock(job_env.lock, stale_seconds=3600)
    with pytest.raises(FileExistsError):
        rsj.acquire_lock(job_env.lock, stale_seconds=3600)


def test_acquire_lock_removes_stale_lock(job_env):
    """锁文件 mtime 超过 stale_seconds 被视为死锁，删除后重新获取成功。"""
    job_env.lock.parent.mkdir(parents=True, exist_ok=True)
    job_env.lock.write_text("stale", encoding="utf-8")
    old = time.time() - 7200
    os.utime(job_env.lock, (old, old))
    pid = rsj.acquire_lock(job_env.lock, stale_seconds=3600)
    assert pid == os.getpid()
    assert json.loads(job_env.lock.read_text(encoding="utf-8"))["pid"] == pid


def test_release_lock_idempotent(job_env):
    """release_lock 删除锁文件；锁不存在时也不报错。"""
    rsj.acquire_lock(job_env.lock, stale_seconds=3600)
    rsj.release_lock(job_env.lock)
    assert not job_env.lock.exists()
    rsj.release_lock(job_env.lock)  # 第二次也不抛异常


# ---------------------------------------------------------------------------
# cleanup_old_logs / append_history
# ---------------------------------------------------------------------------

def test_cleanup_old_logs_removes_expired(job_env):
    """mtime 早于留存期的 .log 被删除，新日志保留。"""
    job_env.log_dir.mkdir(parents=True)
    old_log = job_env.log_dir / "old.log"
    new_log = job_env.log_dir / "new.log"
    old_log.write_text("old", encoding="utf-8")
    new_log.write_text("new", encoding="utf-8")
    old_mtime = time.time() - 30 * 86400
    os.utime(old_log, (old_mtime, old_mtime))
    rsj.cleanup_old_logs(14)
    assert not old_log.exists()
    assert new_log.exists()


def test_cleanup_old_logs_zero_retention_noop(job_env):
    """retention_days<=0 时不清理。"""
    job_env.log_dir.mkdir(parents=True)
    f = job_env.log_dir / "x.log"
    f.write_text("x", encoding="utf-8")
    old_mtime = time.time() - 365 * 86400
    os.utime(f, (old_mtime, old_mtime))
    rsj.cleanup_old_logs(0)
    assert f.exists()


def test_append_history_writes_jsonl(job_env):
    """append_history 以 JSONL 追加，每行一条合法 JSON。"""
    rsj.append_history({"job": "testjob", "status": "success"})
    rsj.append_history({"job": "testjob", "status": "failed"})
    rows = [json.loads(ln) for ln in job_env.history.read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == ["success", "failed"]


# ---------------------------------------------------------------------------
# main：锁冲突跳过 / 正常跑完写日志
# ---------------------------------------------------------------------------

def test_main_skips_when_lock_held(job_env, monkeypatch, capsys):
    """锁被占用时 main 返回 0、打印 JOB_SKIPPED、写 skipped 历史，且不启动子进程。"""
    job_env.lock.parent.mkdir(parents=True, exist_ok=True)
    job_env.lock.write_text("held", encoding="utf-8")
    cfg = _write_config(job_env.tmp)

    def forbidden(*a, **k):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", forbidden)
    rc = rsj.main(["--job", "testjob", "--config", str(cfg)])
    assert rc == 0
    assert "JOB_SKIPPED|job=testjob|reason=already_running" in capsys.readouterr().out
    rows = [json.loads(ln) for ln in job_env.history.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "skipped"
    assert rows[0]["reason"] == "already_running"


def test_main_runs_job_writes_log_and_history(job_env, monkeypatch, capsys):
    """正常执行：写日志文件（含 exit_code/stdout）、追加 success 历史、释放锁。"""
    cfg = _write_config(job_env.tmp)
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="SUMMARY_OK", stderr="warn-line")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed)

    rc = rsj.main(["--job", "testjob", "--config", str(cfg)])
    assert rc == 0

    logs = list(job_env.log_dir.glob("testjob-*.log"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "exit_code=0" in content
    assert "[stdout]" in content and "SUMMARY_OK" in content
    assert "[stderr]" in content and "warn-line" in content

    rows = [json.loads(ln) for ln in job_env.history.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "success"
    assert rows[0]["exit_code"] == 0
    assert rows[0]["log_path"] == str(logs[0])

    assert not job_env.lock.exists()  # finally 里释放锁
    assert "SUMMARY_OK" in capsys.readouterr().out


def test_main_failed_job_returns_exit_code(job_env, monkeypatch):
    """子进程非零退出：main 透传 exit code，历史记 failed，锁仍被释放。"""
    cfg = _write_config(job_env.tmp)
    completed = subprocess.CompletedProcess(args=[], returncode=3, stdout="", stderr="boom")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed)
    rc = rsj.main(["--job", "testjob", "--config", str(cfg)])
    assert rc == 3
    rows = [json.loads(ln) for ln in job_env.history.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "failed"
    assert rows[0]["exit_code"] == 3
    assert not job_env.lock.exists()


def test_main_negative_stale_seconds_rejected(job_env):
    """job_lock_stale_seconds 为负数时直接抛 RuntimeError。"""
    cfg = _write_config(job_env.tmp, {"job_lock_stale_seconds": -1})
    with pytest.raises(RuntimeError, match="job_lock_stale_seconds"):
        rsj.main(["--job", "testjob", "--config", str(cfg)])
