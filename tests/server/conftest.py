"""tests/server/ 共享 fixture。

仓库根 tests/conftest.py 已把 server/ 与 client/src/ 放上 sys.path，
并把 DATABASE_PATH / CONFIG_PATH 重定向到临时目录，因此这里可以直接
import server 并操作那份临时 SQLite 库。

每个测试函数前后清空三张表 + 日指标缓存，保证用例间隔离。
非 fixture 的共享助手（insert_*、FrozenDateTime 等）在 server_helpers.py。
"""

import sqlite3

import pytest

import server
from server_helpers import make_mem_db


TABLES = ("checkins", "activity_slices", "ratings")


def _wipe_tables():
    conn = sqlite3.connect(server.DB_PATH)
    try:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    server._day_metrics_cache.clear()


@pytest.fixture(autouse=True)
def clean_tables():
    """每个测试前后清空临时库所有表与 _day_metrics_cache。"""
    _wipe_tables()
    yield
    _wipe_tables()


@pytest.fixture()
def db():
    """指向临时库的普通 sqlite 连接（Row 工厂），测试结束自动关闭。"""
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture()
def client():
    """Flask test client；app.testing=True 让异常直接抛出便于定位。"""
    server.app.testing = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture()
def mem_db():
    """内存 sqlite 库（三表），供 shifts/detection 等纯函数用例使用。"""
    conn = make_mem_db()
    yield conn
    conn.close()
