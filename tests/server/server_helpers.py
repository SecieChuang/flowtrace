"""tests/server/ 共享的测试助手（非 fixture 部分）。

独立成模块而不是放在 conftest.py：仓库里存在多个同名 conftest.py
（tests/、tests/server/、tests/client/），`from conftest import ...`
会撞模块名；本模块名全局唯一，import 行为确定。
"""

import sqlite3
from datetime import datetime


class FrozenDateTime(datetime):
    """固定 server/shifts 模块取到的"当前时间"，用于实时尾段场景。"""

    current = datetime(2026, 9, 2, 23, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.current


def insert_checkin(conn, action, date, time):
    """直接向库中插入一条打卡并返回自增 id。"""
    cur = conn.execute(
        "INSERT INTO checkins (action, date, time) VALUES (?, ?, ?)",
        (action, date, time),
    )
    conn.commit()
    return cur.lastrowid


def insert_activity(conn, date, start_time, end_time, tags="auto"):
    """直接向库中插入一条活动切片并返回自增 id。"""
    cur = conn.execute(
        "INSERT INTO activity_slices (date, start_time, end_time, duration_minutes, tags) VALUES (?, ?, ?, ?, ?)",
        (date, start_time, end_time, None, tags),
    )
    conn.commit()
    return cur.lastrowid


def make_mem_db():
    """内存 sqlite 库（checkins/activity_slices/ratings 三表），Row 工厂。

    供直接调用 shifts / detection / server 内部函数的用例使用。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL
        );
        CREATE TABLE activity_slices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes REAL,
            tags TEXT DEFAULT 'auto'
        );
        CREATE TABLE ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            value INTEGER NOT NULL
        );
        """
    )
    return conn


def seg(start, end, in_progress=False):
    """构造 fill_segment_slots 需要的活跃段 dict（start/end 为 datetime）。"""
    return {"start_dt": start, "end_dt": end, "in_progress": in_progress}


def card(cid, date, time, action):
    """构造 fill_segment_slots 需要的打卡 dict。"""
    return {"id": cid, "date": date, "time": time, "action": action}
