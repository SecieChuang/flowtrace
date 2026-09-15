from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ActivitySlice:
    date: str
    start_time: str
    end_time: Optional[str]
    duration_minutes: Optional[float]
    tags: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


DEFAULT_CSV_PATH = _repo_root() / "timedata.csv"
DEFAULT_DB_PATH = _repo_root() / "data" / "flowtrace.db"


def _normalize_date(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("date is required")
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"invalid date: {value!r}")


def _normalize_time(raw: str, field_name: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"{field_name} is required")

    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid {field_name}: {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2])
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _compute_duration_minutes(date: str, start_time: str, end_time: Optional[str]) -> Optional[float]:
    if not end_time:
        return None

    start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M:%S")
    if end_dt < start_dt:
        end_dt += timedelta(days=1)
    return round((end_dt - start_dt).total_seconds() / 60.0, 1)


def _is_blank_row(row: dict[str, str]) -> bool:
    return all(not (row.get(col) or "").strip() for col in ("date", "start_time", "end_time", "duration", "tags"))


def _parse_csv(csv_path: Path, strict: bool) -> tuple[list[ActivitySlice], list[str], int, int]:
    slices: list[ActivitySlice] = []
    errors: list[str] = []
    total_rows = 0
    blank_rows = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required_cols = {"date", "start_time", "end_time", "duration", "tags"}
        if not reader.fieldnames or not required_cols.issubset(set(reader.fieldnames)):
            raise ValueError(f"CSV header must include: {sorted(required_cols)}")

        for line_no, row in enumerate(reader, start=2):
            total_rows += 1

            if _is_blank_row(row):
                blank_rows += 1
                continue

            try:
                date = _normalize_date(row.get("date", ""))
                start_time = _normalize_time(row.get("start_time", ""), "start_time")
                raw_end = (row.get("end_time", "") or "").strip()
                end_time = _normalize_time(raw_end, "end_time") if raw_end else None
                tags = (row.get("tags", "") or "").strip() or "auto"
                duration_minutes = _compute_duration_minutes(date, start_time, end_time)
                slices.append(
                    ActivitySlice(
                        date=date,
                        start_time=start_time,
                        end_time=end_time,
                        duration_minutes=duration_minutes,
                        tags=tags,
                    )
                )
            except ValueError as exc:
                msg = f"line {line_no}: {exc}"
                if strict:
                    raise ValueError(msg) from exc
                errors.append(msg)

    return slices, errors, total_rows, blank_rows


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity_slices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes REAL,
            tags TEXT DEFAULT 'auto',
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(date, start_time)
        );
        CREATE INDEX IF NOT EXISTS idx_activity_date ON activity_slices(date);
        """
    )


def _fetch_existing_keys(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_slices' LIMIT 1"
    ).fetchone()
    if not row:
        return set()

    existing = conn.execute("SELECT date, start_time FROM activity_slices").fetchall()
    return {(r[0], r[1]) for r in existing}


def _make_backup(db_path: Path, backup_path: Optional[Path]) -> Optional[Path]:
    if not db_path.exists():
        return None

    if backup_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = db_path.with_name(f"{db_path.stem}.backup.{stamp}{db_path.suffix}")

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup_path)
    return backup_path


def run_import(
    csv_path: Path | str,
    db_path: Path | str,
    dry_run: bool = False,
    strict: bool = False,
    backup: bool = True,
    backup_path: Path | str | None = None,
) -> dict[str, object]:
    csv_path = Path(csv_path).resolve()
    db_path = Path(db_path).resolve()
    backup_path_obj = Path(backup_path).resolve() if backup_path is not None else None

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    slices, errors, total_rows, blank_rows = _parse_csv(csv_path, strict=strict)
    summary: dict[str, object] = {
        "csv_path": str(csv_path),
        "db_path": str(db_path),
        "dry_run": dry_run,
        "strict": strict,
        "total_rows": total_rows,
        "valid_rows": len(slices),
        "blank_rows": blank_rows,
        "invalid_rows": len(errors),
        "errors": errors,
    }

    if dry_run:
        existing_keys: set[tuple[str, str]] = set()
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            try:
                existing_keys = _fetch_existing_keys(conn)
            finally:
                conn.close()

        would_update = sum(1 for item in slices if (item.date, item.start_time) in existing_keys)
        summary["would_update"] = would_update
        summary["would_insert"] = len(slices) - would_update
        summary["backup_path"] = None
        return summary

    created_backup = _make_backup(db_path, backup_path_obj) if backup else None
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    inserted = 0
    updated = 0
    try:
        _ensure_schema(conn)
        existing_keys = _fetch_existing_keys(conn)

        conn.execute("BEGIN")
        for item in slices:
            if (item.date, item.start_time) in existing_keys:
                updated += 1
            else:
                inserted += 1
                existing_keys.add((item.date, item.start_time))

            conn.execute(
                """
                INSERT INTO activity_slices (date, start_time, end_time, duration_minutes, tags)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date, start_time) DO UPDATE SET
                    end_time = excluded.end_time,
                    duration_minutes = excluded.duration_minutes,
                    tags = excluded.tags,
                    updated_at = datetime('now')
                """,
                (item.date, item.start_time, item.end_time, item.duration_minutes, item.tags),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    summary["inserted"] = inserted
    summary["updated"] = updated
    summary["backup_path"] = str(created_backup) if created_backup else None
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import timedata.csv into flowtrace SQLite database.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH), help="Path to timedata.csv")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite database file")
    parser.add_argument("--dry-run", action="store_true", help="Parse CSV and show summary only")
    parser.add_argument("--strict", action="store_true", help="Fail on first invalid non-empty row")
    parser.add_argument("--no-backup", action="store_true", help="Do not create DB backup before writing")
    parser.add_argument("--backup-path", default="", help="Custom backup path")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    backup_path = args.backup_path.strip() or None

    try:
        summary = run_import(
            csv_path=args.csv,
            db_path=args.db,
            dry_run=args.dry_run,
            strict=args.strict,
            backup=not args.no_backup,
            backup_path=backup_path,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"CSV: {summary['csv_path']}")
    print(f"DB: {summary['db_path']}")
    print(
        "Rows: total={total_rows}, valid={valid_rows}, blank={blank_rows}, invalid={invalid_rows}".format(
            **summary
        )
    )

    errors = summary.get("errors", [])
    if errors:
        print("Invalid rows (showing up to 20):")
        for msg in errors[:20]:
            print(f"  - {msg}")

    if summary["dry_run"]:
        print(f"Dry-run result: would_insert={summary['would_insert']}, would_update={summary['would_update']}")
    else:
        print(f"Write result: inserted={summary['inserted']}, updated={summary['updated']}")

    backup_path = summary.get("backup_path")
    if backup_path:
        print(f"Backup: {backup_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
