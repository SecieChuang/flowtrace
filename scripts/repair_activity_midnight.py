"""repair_activity_midnight.py - 修复历史跨日活跃切片

用法:
    python repair_activity_midnight.py [--db <path>] [--dry-run] [--no-backup]

功能:
  1. 拆分历史跨日记录：
     - 找出所有 end_time 不为空且 end_time < start_time 的行（原始跨日记录）
     - 将该行的 end_time 改为 "24:00:00"，重新计算 duration_minutes
     - 在次日（date+1）补写 start_time="00:00:00", end_time=原 end_time 的新行

  2. 修复"写错 date 的次日 end"模式：
     - 找出 (date=D2, start=S, end=E, E < S) 且前一天 (D1, S, end_time IS NULL) 存在的行
     - 把 D1 的行改为 end_time="24:00:00"，并删除错误的 D2 行
     - 在次日补写 (D2, "00:00:00", E)

脚本可重复运行（幂等）：所有操作在处理前检查目标状态是否已满足。
默认在写入前备份数据库；可用 --no-backup 跳过。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


DEFAULT_DB_PATH = _repo_root() / "data" / "flowtrace.db"


@dataclass
class RepairSummary:
    db_path: str = ""
    dry_run: bool = False
    backup_path: Optional[str] = None
    overnight_splits: list = field(default_factory=list)
    wrong_date_fixes: list = field(default_factory=list)

    @property
    def total_changes(self):
        return len(self.overnight_splits) + len(self.wrong_date_fixes)


def _date_add(date_str: str, days: int) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _time_to_min(t: str) -> Optional[int]:
    if not t:
        return None
    parts = t.split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        if h == 24:
            return 1440
        return h * 60 + m
    except ValueError:
        return None


def _make_backup(db_path: Path) -> Optional[Path]:
    if not db_path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}.backup.{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup)
    return backup


def _fetch_all_slices(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, date, start_time, end_time, duration_minutes, tags "
        "FROM activity_slices ORDER BY date, start_time"
    ).fetchall()
    return [dict(r) for r in rows]


def _key_exists(conn: sqlite3.Connection, date: str, start_time: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, end_time FROM activity_slices WHERE date = ? AND start_time = ?",
        (date, start_time),
    ).fetchone()
    return dict(row) if row else None


def _upsert(conn: sqlite3.Connection, date: str, start_time: str,
            end_time: str, duration: Optional[float], tags: str) -> None:
    existing = _key_exists(conn, date, start_time)
    if existing:
        conn.execute(
            "UPDATE activity_slices SET end_time=?, duration_minutes=?, updated_at=datetime('now') WHERE id=?",
            (end_time, duration, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO activity_slices (date, start_time, end_time, duration_minutes, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (date, start_time, end_time, duration, tags),
        )


def _build_overnight_split_change(row: dict, start_min: int, end_min: int) -> dict:
    date_d2 = _date_add(row["date"], 1)
    return {
        "type": "overnight_split",
        "d1": {
            "date": row["date"],
            "start_time": row["start_time"],
            "end_time": "24:00:00",
            "duration": round(1440 - start_min, 1),
        },
        "d2": {
            "date": date_d2,
            "start_time": "00:00:00",
            "end_time": row["end_time"],
            "duration": round(end_min, 1),
        },
    }


def _build_wrong_date_fix_change(prev_open: dict, bad_row: dict, duration_d2: float) -> dict:
    prev_start_min = _time_to_min(prev_open["start_time"])
    if prev_start_min is None:
        raise ValueError("invalid previous open slice start_time")
    return {
        "type": "wrong_date_fix",
        "close_d1": {
            "date": prev_open["date"],
            "start_time": prev_open["start_time"],
            "new_end_time": "24:00:00",
            "duration": round(1440 - prev_start_min, 1),
            "tags": prev_open["tags"],
        },
        "delete_bad_row": {
            "id": bad_row["id"],
            "date": bad_row["date"],
            "start_time": bad_row["start_time"],
            "end_time": bad_row["end_time"],
        },
        "add_d2_spillover": {
            "date": bad_row["date"],
            "start_time": "00:00:00",
            "end_time": bad_row["end_time"],
            "duration": duration_d2,
            "tags": bad_row["tags"],
        },
    }


def _apply_overnight_split(conn: sqlite3.Connection, row: dict, change: dict, index: dict[tuple[str, str], dict]) -> None:
    d1 = change["d1"]
    d2 = change["d2"]
    conn.execute(
        "UPDATE activity_slices SET end_time='24:00:00', duration_minutes=?, updated_at=datetime('now') WHERE id=?",
        (d1["duration"], row["id"]),
    )
    d2_key = (d2["date"], d2["start_time"])
    if d2_key not in index:
        conn.execute(
            "INSERT INTO activity_slices (date, start_time, end_time, duration_minutes, tags) VALUES (?, ?, ?, ?, ?)",
            (d2["date"], d2["start_time"], d2["end_time"], d2["duration"], row["tags"]),
        )
        index[d2_key] = {"date": d2["date"], "start_time": d2["start_time"], "end_time": d2["end_time"]}


def _apply_wrong_date_fix(conn: sqlite3.Connection, change: dict, index: dict[tuple[str, str], dict]) -> None:
    cl = change["close_d1"]
    bad = change["delete_bad_row"]
    sp = change["add_d2_spillover"]
    _upsert(conn, cl["date"], cl["start_time"], cl["new_end_time"], cl["duration"], cl["tags"])
    conn.execute("DELETE FROM activity_slices WHERE id = ?", (bad["id"],))
    _upsert(conn, sp["date"], sp["start_time"], sp["end_time"], sp["duration"], sp["tags"])
    index.pop((bad["date"], bad["start_time"]), None)


def run_repair(
    db_path: Path | str,
    dry_run: bool = False,
    backup: bool = True,
) -> RepairSummary:
    db_path = Path(db_path).resolve()
    summary = RepairSummary(db_path=str(db_path), dry_run=dry_run)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    if not dry_run and backup:
        created = _make_backup(db_path)
        summary.backup_path = str(created) if created else None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        slices = _fetch_all_slices(conn)
        index: dict[tuple[str, str], dict] = {(r["date"], r["start_time"]): r for r in slices}

        if not dry_run:
            conn.execute("BEGIN")

        for row in slices:
            start_time = row["start_time"]
            end_time = row["end_time"]
            if not end_time or end_time == "24:00:00":
                continue

            start_min = _time_to_min(start_time)
            end_min = _time_to_min(end_time)
            if start_min is None or end_min is None or end_min >= start_min:
                continue

            prev_open = index.get((_date_add(row["date"], -1), start_time))
            if prev_open is not None and not prev_open["end_time"]:
                change = _build_wrong_date_fix_change(prev_open, row, round(end_min, 1))
                summary.wrong_date_fixes.append(change)
                if not dry_run:
                    _apply_wrong_date_fix(conn, change, index)
                continue

            change = _build_overnight_split_change(row, start_min, end_min)
            d2_key = (change["d2"]["date"], change["d2"]["start_time"])
            if d2_key in index:
                change["note"] = "d2_row_already_exists_skipped"
            summary.overnight_splits.append(change)
            if not dry_run:
                _apply_overnight_split(conn, row, change, index)

        if not dry_run:
            conn.commit()

    except Exception:
        if not dry_run:
            conn.rollback()
        raise
    finally:
        conn.close()

    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair historical cross-midnight activity slices in flowtrace.db"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to flowtrace.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip backup before writing (not recommended)")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        summary = run_repair(
            db_path=args.db,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise

    mode = "[DRY-RUN] " if summary.dry_run else ""
    print(f"{mode}DB: {summary.db_path}")
    if summary.backup_path:
        print(f"Backup: {summary.backup_path}")

    if not summary.overnight_splits and not summary.wrong_date_fixes:
        print("No repairs needed — database is clean.")
        return 0

    print(f"\nOvernight splits to {'apply' if not summary.dry_run else 'perform'}: "
          f"{len(summary.overnight_splits)}")
    for c in summary.overnight_splits:
        d1, d2 = c["d1"], c["d2"]
        note = f"  [{c.get('note', '')}]" if c.get("note") else ""
        print(f"  {d1['date']} {d1['start_time']} → {d1['end_time']} | "
              f"{d2['date']} {d2['start_time']} → {d2['end_time']}{note}")

    print(f"\nWrong-date fixes to {'apply' if not summary.dry_run else 'perform'}: "
          f"{len(summary.wrong_date_fixes)}")
    for c in summary.wrong_date_fixes:
        cl = c["close_d1"]
        sp = c["add_d2_spillover"]
        bad = c["delete_bad_row"]
        print(f"  Close D1: {cl['date']} {cl['start_time']} → {cl['new_end_time']}  |  "
              f"Delete bad: {bad['date']} {bad['start_time']}  |  "
              f"Add spillover: {sp['date']} {sp['start_time']} → {sp['end_time']}")

    if summary.dry_run:
        print("\nDry-run complete — no changes written. Re-run without --dry-run to apply.")
    else:
        print(f"\nRepair complete: {summary.total_changes} change(s) applied.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
