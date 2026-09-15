"""Run scheduled notification jobs with lock, history and logs."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Sequence


CLIENT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = CLIENT_DIR / "config" / "config.json"
RUNTIME_DIR = CLIENT_DIR / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
HISTORY_PATH = RUNTIME_DIR / "job_history.jsonl"

JOB_SPECS = {
    "screenshot-summary": {
        "script": CLIENT_DIR / "src" / "screenshot_summary.py",
        "lock": RUNTIME_DIR / "screenshot-summary.lock",
    },
}


def load_config(path: Path) -> Dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"WARNING: Failed to parse config {path}: {e}", file=sys.stderr)
    return {}


def append_history(entry: Dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cleanup_old_logs(retention_days: int) -> None:
    if retention_days <= 0 or not LOG_DIR.exists():
        return
    cutoff = time.time() - retention_days * 86400
    for path in LOG_DIR.glob("*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def acquire_lock(lock_path: Path, stale_seconds: int) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            age = max(0, time.time() - lock_path.stat().st_mtime)
        except OSError:
            age = 0
        if stale_seconds > 0 and age > stale_seconds:
            try:
                lock_path.unlink()
            except OSError:
                pass
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(lock_path), flags)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False))
    return os.getpid()


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def build_command(job_name: str, config_path: Path) -> list[str]:
    script_path = JOB_SPECS[job_name]["script"]
    return [sys.executable, str(script_path), "--config", str(config_path)]


def write_log(
    log_path: Path,
    command: Sequence[str],
    result: subprocess.CompletedProcess[str],
    started_at: datetime,
    finished_at: datetime,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    content = [
        f"started_at={started_at.isoformat(timespec='seconds')}",
        f"finished_at={finished_at.isoformat(timespec='seconds')}",
        f"exit_code={result.returncode}",
        f"command={' '.join(command)}",
        "",
        "[stdout]",
        result.stdout or "",
        "",
        "[stderr]",
        result.stderr or "",
    ]
    log_path.write_text("\n".join(content), encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduled Flowtrace notification jobs")
    parser.add_argument("--job", choices=sorted(JOB_SPECS), required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config_path = Path(args.config).expanduser().resolve()
    cfg = load_config(config_path)
    stale_seconds = int(cfg.get("job_lock_stale_seconds", 43200))
    retention_days = int(cfg.get("notification_log_retention_days", 14))
    if stale_seconds < 0:
        raise RuntimeError("job_lock_stale_seconds must be greater than or equal to 0")
    if retention_days < 0:
        raise RuntimeError("notification_log_retention_days must be greater than or equal to 0")

    cleanup_old_logs(retention_days)
    lock_path = JOB_SPECS[args.job]["lock"]
    started_at = datetime.now()
    try:
        acquire_lock(lock_path, stale_seconds)
    except FileExistsError:
        print(f"JOB_SKIPPED|job={args.job}|reason=already_running")
        append_history({
            "job": args.job,
            "status": "skipped",
            "reason": "already_running",
            "started_at": started_at.isoformat(timespec="seconds"),
        })
        return 0

    command = build_command(args.job, config_path)
    timestamp = started_at.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"{args.job}-{timestamp}.log"
    try:
        result = subprocess.run(
            command,
            cwd=str(CLIENT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        finished_at = datetime.now()
        write_log(log_path, command, result, started_at, finished_at)
        append_history({
            "job": args.job,
            "status": "success" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "log_path": str(log_path),
        })
        if result.stdout:
            try:
                sys.stdout.write(result.stdout.rstrip() + "\n")
            except UnicodeEncodeError:
                sys.stdout.buffer.write((result.stdout.rstrip() + "\n").encode("utf-8", errors="replace"))
        if result.stderr:
            try:
                sys.stderr.write(result.stderr.rstrip() + "\n")
            except UnicodeEncodeError:
                sys.stderr.buffer.write((result.stderr.rstrip() + "\n").encode("utf-8", errors="replace"))
        return result.returncode
    finally:
        release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
