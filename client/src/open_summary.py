#!/usr/bin/env python3
"""根据时间打开对应的日报 HTML。

规则：
- 23:40 之后 → 打开今日 summary
- 23:30 之前 → 打开昨日 summary
- 23:30-23:40 之间 → 打开昨日 summary（因为今日还没生成完）
"""
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 相对路径：src/ → runtime/summaries/
SCRIPT_DIR = Path(__file__).resolve().parent
SUMMARIES_DIR = SCRIPT_DIR.parent / "runtime" / "summaries"


def get_target_date() -> str:
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute

    # 23:40 之后打开今日，否则打开昨日
    if current_hour == 23 and current_minute >= 40:
        return now.strftime("%Y-%m-%d")
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def find_summary_file(date: str) -> Path | None:
    """按日期精确查找；找不到时回退到最新一份日报；一份都没有返回 None。"""
    target = SUMMARIES_DIR / f"screenshot-summary-{date}.html"
    if target.exists():
        return target
    if not SUMMARIES_DIR.exists():
        return None
    candidates = sorted(SUMMARIES_DIR.glob("screenshot-summary-*.html"))
    return candidates[-1] if candidates else None


def open_summary(summary_file: Path) -> bool:
    print(f"正在打开: {summary_file}")

    # Windows: 用 cmd /c start 异步打开
    if sys.platform == "win32":
        subprocess.Popen(
            ["cmd", "/c", "start", "", str(summary_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    # Mac
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(summary_file)])
    # Linux
    else:
        subprocess.Popen(["xdg-open", str(summary_file)])

    return True


def main() -> int:
    target_date = get_target_date()
    now = datetime.now().strftime("%H:%M")

    print(f"当前时间: {now} → 目标日期: {target_date}")

    summary_file = find_summary_file(target_date)
    if summary_file is None:
        print(f"未找到任何日报：{SUMMARIES_DIR} 下没有 screenshot-summary-*.html")
        print("请确认截图摘要功能已启用，且至少成功生成过一次日报。")
        return 1

    expected = f"screenshot-summary-{target_date}.html"
    if summary_file.name != expected:
        print(f"目标日期 {target_date} 的日报不存在，改打开最新一份：{summary_file.name}")

    open_summary(summary_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
