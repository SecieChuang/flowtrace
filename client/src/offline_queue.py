"""离线队列 - 当服务端不可达时，将同步事件追加到 JSONL 文件，恢复后批量刷新。
修复: 每个事件保存目标 endpoint，flush 时按类型分发到正确的 API。

并发设计（无文件锁）：
- enqueue 以 append 模式追加单行，天然原子；
- flush 先把队列文件原子改名到 .processing，处理期间新入队的事件写新主文件，
  互不干扰；失败的事件 append 回主文件，绝不覆盖新入队的事件。
"""
import json
import os
import time
from datetime import datetime

import requests

_QUEUE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backup")
_QUEUE_PATH = os.path.join(_QUEUE_DIR, "sync_queue.jsonl")


def enqueue(endpoint: str, event: dict) -> None:
    """入队一个事件，同时记录其目标 endpoint。"""
    os.makedirs(_QUEUE_DIR, exist_ok=True)
    line = json.dumps(
        {
            "queued_at": datetime.now().isoformat(timespec="seconds"),
            "endpoint": endpoint,
            "event": event,
        },
        ensure_ascii=False,
    )
    with open(_QUEUE_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        if os.path.getsize(_QUEUE_PATH) > 1_000_000:
            _compact()
    except Exception:
        pass


def _compact() -> None:
    if not os.path.exists(_QUEUE_PATH):
        return
    with open(_QUEUE_PATH, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    last_by_key = {}
    kept = []
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        ev = obj.get("event") if isinstance(obj, dict) else None
        if not isinstance(ev, dict):
            continue
        endpoint = obj.get("endpoint", "")
        date = (ev.get("date") or "").strip()
        start_time = (ev.get("start_time") or "").strip()
        # 按 (endpoint, date, start_time) 去重，避免不同类型事件互相覆盖
        if date and start_time:
            last_by_key[(endpoint, date, start_time)] = obj
        else:
            kept.append(obj)
    kept.extend(last_by_key.values())
    kept.sort(key=lambda o: (o.get("queued_at") or ""))
    tmp = _QUEUE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for obj in kept:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    os.replace(tmp, _QUEUE_PATH)


def flush(server_base_url: str, api_key: str = "") -> bool:
    """尝试将队列中的事件逐条发送到对应端点，全部成功后清空队列。"""
    if not os.path.exists(_QUEUE_PATH):
        return True
    # 无锁并发保护：先把队列文件原子改名，隔离本次要处理的事件。
    # 处理期间新入队的事件会写入新的主文件，不会被本流程误删。
    processing_path = _QUEUE_PATH + ".processing"
    for _attempt in range(3):
        try:
            os.replace(_QUEUE_PATH, processing_path)
            break
        except OSError:
            # 另一进程正以写模式打开队列文件（enqueue 窗口极短），稍后重试
            time.sleep(0.05)
    else:
        # 仍被占用：跳过本次，事件保留在队列，下次刷新时再处理
        return False

    try:
        with open(processing_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        # 极端并发：队列已被另一个 flush 进程处理
        return True
    if not lines:
        try:
            os.remove(processing_path)
        except Exception:
            pass
        return True

    parsed = []
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        ev = obj.get("event") if isinstance(obj, dict) else None
        endpoint = obj.get("endpoint", "") if isinstance(obj, dict) else ""
        if isinstance(ev, dict) and endpoint:
            parsed.append((endpoint, ev))
        elif isinstance(ev, dict):
            # 兼容旧格式（无 endpoint 字段）：尝试推断
            if "action" in ev:
                parsed.append(("/api/checkin", ev))
            elif "value" in ev and "start_time" not in ev:
                parsed.append(("/api/rating", ev))
            else:
                parsed.append(("/api/activity", ev))

    if not parsed:
        try:
            os.remove(processing_path)
        except Exception:
            pass
        return True

    base = server_base_url.rstrip("/")
    failed = []
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    for endpoint, ev in parsed:
        url = f"{base}{endpoint}"
        try:
            resp = requests.post(url, json=ev, timeout=5, headers=headers)
            if resp.status_code != 200:
                failed.append((endpoint, ev))
        except Exception:
            failed.append((endpoint, ev))

    if failed:
        # 失败的事件 append 回主文件（不覆盖 flush 期间新入队的事件）
        with open(_QUEUE_PATH, "a", encoding="utf-8") as f:
            for endpoint, ev in failed:
                line = json.dumps(
                    {
                        "queued_at": datetime.now().isoformat(timespec="seconds"),
                        "endpoint": endpoint,
                        "event": ev,
                    },
                    ensure_ascii=False,
                )
                f.write(line + "\n")
        try:
            os.remove(processing_path)
        except Exception:
            pass
        return False

    # 全部成功，清空队列
    try:
        os.remove(processing_path)
    except Exception:
        pass
    return True
