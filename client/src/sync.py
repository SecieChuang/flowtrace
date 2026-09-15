"""sync.py - AHK 按需调用的同步脚本
用法:
    python sync.py checkin clock_in
    python sync.py checkin clock_out
    python sync.py activity start 09:30:00
    python sync.py activity update 09:30:00 10:45:00
    python sync.py activity end 09:30:00 11:20:00
    python sync.py rating 4
    python sync.py flush
    python sync.py screenshot
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
from offline_queue import enqueue, flush

CONFIG_PATH = os.path.join(os.path.dirname(_DIR), "config", "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"WARNING: Failed to parse config {CONFIG_PATH}: {e}", file=sys.stderr)
    return {}


CFG = load_config()
# config 里空字符串会遮蔽默认值，strip 后为空则回退服务端默认地址
_server_cfg = str(CFG.get("server_base_url", "")).strip()
SERVER = (_server_cfg or "http://localhost:8000").rstrip("/")
API_KEY = CFG.get("api_key", "")
RESULT_FILE = ""
DATE_ARG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATE_PATH = Path(os.path.dirname(_DIR)) / "runtime" / "state.json"


def _now():
    return datetime.now()


def _extract_activity_date(args, fallback_date: str):
    if args and DATE_ARG_RE.match(str(args[0])):
        return str(args[0]), list(args[1:])
    return fallback_date, list(args)


def _write_result_line(line: str):
    if not RESULT_FILE:
        return
    try:
        parent = os.path.dirname(RESULT_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            f.write(line.strip() + "\n")
    except Exception:
        pass


def _post(endpoint: str, data: dict) -> dict:
    """POST 到服务端，失败则入队（携带 endpoint 信息）。"""
    try:
        flush(SERVER, API_KEY)
    except Exception:
        pass
    url = f"{SERVER}{endpoint}"
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    try:
        resp = requests.post(url, json=data, timeout=5, headers=headers)
        payload = None
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return {
            "transport_ok": True,
            "http_ok": resp.status_code == 200,
            "queued": False,
            "response": payload,
            "status_code": resp.status_code,
        }
    except Exception:
        enqueue(endpoint, data)
        return {
            "transport_ok": False,
            "http_ok": False,
            "queued": True,
            "response": None,
            "status_code": None,
        }


def _load_local_state() -> dict:
    try:
        if STATE_PATH.exists():
            with STATE_PATH.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _is_on_duty_local() -> bool:
    return bool(_load_local_state().get("is_on_duty"))


def _write_local_state(is_on_duty: bool) -> None:
    payload = {
        "is_on_duty": bool(is_on_duty),
        "updated_at": datetime.now().isoformat(),
    }
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_current_duty() -> dict:
    url = f"{SERVER}/api/dashboard/current-duty"
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    try:
        resp = requests.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def cmd_checkin(action: str):
    now = _now()
    data = {
        "action": action,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
    }
    result = _post("/api/checkin", data)
    label = "上班" if action == "clock_in" else "下班"
    if result["queued"]:
        status = "queued"
        message = f"已入离线队列：{label}打卡 {data['time']}"
    elif result["http_ok"]:
        resp = result.get("response") or {}
        if resp.get("deduped"):
            status = "deduped"
            kept_time = resp.get("kept_time") or data["time"]
            message = f"重复打卡已忽略，保留 {kept_time}"
        else:
            status = "stored"
            kept_time = resp.get("kept_time") or data["time"]
            message = f"{label}打卡已落库 {kept_time}"
    else:
        status = "failed"
        code = result.get("status_code")
        message = f"打卡失败（HTTP {code}）" if code else "打卡失败"

    line = (
        "CHECKIN_RESULT"
        f"|status={status}"
        f"|action={action}"
        f"|time={data['time']}"
        f"|message={message}"
    )
    print(line)
    _write_result_line(line)


def cmd_activity(sub: str, *args):
    now = _now()
    date, activity_args = _extract_activity_date(args, now.strftime("%Y-%m-%d"))
    if sub == "start":
        start_time = activity_args[0] if activity_args else now.strftime("%H:%M:%S")
        data = {"date": date, "start_time": start_time, "end_time": "", "tags": "auto"}
    elif sub == "update":
        start_time = activity_args[0] if len(activity_args) > 0 else ""
        end_time = activity_args[1] if len(activity_args) > 1 else now.strftime("%H:%M:%S")
        data = {"date": date, "start_time": start_time, "end_time": end_time, "tags": "auto"}
    elif sub == "end":
        start_time = activity_args[0] if len(activity_args) > 0 else ""
        end_time = activity_args[1] if len(activity_args) > 1 else now.strftime("%H:%M:%S")
        data = {"date": date, "start_time": start_time, "end_time": end_time, "tags": "auto"}
    else:
        print(f"未知 activity 子命令: {sub}")
        return
    result = _post("/api/activity", data)
    if result["http_ok"]:
        print(f"活跃度同步成功: {sub} {data.get('start_time', '')} {data.get('end_time', '')}")
    elif result["queued"]:
        print(f"活跃度已排队: {sub}")
    else:
        print(f"活跃度同步失败: {sub}")


def cmd_rating(value: str):
    now = _now()
    data = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "value": int(value),
    }
    result = _post("/api/rating", data)
    if result["http_ok"]:
        print(f"评分同步成功: {value}")
    elif result["queued"]:
        print(f"评分已排队: {value}")
    else:
        print(f"评分同步失败: {value}")


def cmd_flush():
    ok = flush(SERVER, API_KEY)
    if ok:
        print("队列刷新完成")
    else:
        print("队列刷新失败")


def cmd_screenshot():
    import subprocess

    screenshotter = os.path.join(_DIR, "screenshotter.py")
    local_on_duty = _is_on_duty_local()
    server_on_duty = False
    server_checked = False
    explicit_open_clock_in_now = False
    local_state_corrected = False

    webcam_enabled = local_on_duty
    if not webcam_enabled:
        current_duty = _get_current_duty()
        server_checked = True
        server_on_duty = bool(current_duty.get("on_duty_now"))
        explicit_open_clock_in_now = bool(current_duty.get("explicit_open_clock_in_now"))
        webcam_enabled = server_on_duty
        if explicit_open_clock_in_now:
            _write_local_state(True)
            local_state_corrected = True

    webcam_flag = "--webcam" if webcam_enabled else "--no-webcam"
    print(
        "SCREENSHOT_DECISION"
        f"|local_on_duty={str(local_on_duty).lower()}"
        f"|server_checked={str(server_checked).lower()}"
        f"|server_on_duty={str(server_on_duty).lower()}"
        f"|explicit_open_clock_in_now={str(explicit_open_clock_in_now).lower()}"
        f"|local_state_corrected={str(local_state_corrected).lower()}"
        f"|webcam_flag={webcam_flag}"
    )
    try:
        result = subprocess.run(
            [sys.executable, screenshotter, "--config", CONFIG_PATH, webcam_flag],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip() or result.stderr.strip()
        print(output or ("SCREENSHOT_OK" if result.returncode == 0 else "SCREENSHOT_ERROR"))
    except Exception as e:
        print(f"SCREENSHOT_ERROR|error={e}")


def main():
    global RESULT_FILE
    # 可选参数：--result-file <path>，供 AHK 在无控制台模式下读取结果
    if "--result-file" in sys.argv:
        idx = sys.argv.index("--result-file")
        if idx + 1 < len(sys.argv):
            RESULT_FILE = sys.argv[idx + 1]
        # 从 argv 中移除该参数，避免影响命令解析
        if idx + 1 < len(sys.argv):
            del sys.argv[idx: idx + 2]
        else:
            del sys.argv[idx: idx + 1]

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "checkin" and args:
        cmd_checkin(args[0])
    elif cmd == "activity" and args:
        cmd_activity(args[0], *args[1:])
    elif cmd == "rating" and args:
        cmd_rating(args[0])
    elif cmd == "flush":
        cmd_flush()
    elif cmd == "screenshot":
        cmd_screenshot()
    else:
        print(f"未知命令: {cmd} {args}")
        sys.exit(1)


if __name__ == "__main__":
    main()
