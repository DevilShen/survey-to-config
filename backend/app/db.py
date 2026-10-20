"""数据库连接与初始化。

SQLite + Row 工厂，单文件库，方便整包拷走演示。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("SDLC_DB", DATA_DIR / "app.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

_initialized = False

# 演示账号：登录页会用 /api/auth/demo 把这个提示出来，点一下就能进
DEMO_LOGIN = "demo"
DEMO_PASSWORD = "demo1234"
DEMO_NAME = "演示账号"
DEMO_EMAIL = "demo@demo.cn"
DEMO_ROLE = "admin"
# 种子用户的统一演示密码（test_api 用 pm / viewer 角色验权限，不能只有 demo 能登）
SEED_PASSWORD = "demo1234"


def connect() -> sqlite3.Connection:
    """每次请求一个连接，SQLite 开销极低；避免长连接在多线程下的坑。"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """老库升级：users 加登录账号与密码列（SQLite 只能 ADD COLUMN）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "login_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN login_name TEXT")
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login ON users(login_name)")


def ensure_accounts(conn: sqlite3.Connection) -> None:
    """保证每个用户都能登录：补账号名、补密码哈希；保证 demo 账号存在。

    幂等：只在缺字段时算哈希（PBKDF2 12 万轮不便宜，不能每次启动都跑一遍）。
    """
    from .security import hash_password

    rows = conn.execute(
        "SELECT id, name, email, login_name, password_hash FROM users").fetchall()
    for r in rows:
        login = r["login_name"] or str(r["email"]).split("@")[0]
        if r["login_name"] and r["password_hash"]:
            continue
        pw = r["password_hash"] or hash_password(SEED_PASSWORD)
        conn.execute("UPDATE users SET login_name=?, password_hash=? WHERE id=?",
                     (login, pw, r["id"]))

    demo = conn.execute("SELECT id FROM users WHERE login_name=? OR email=?",
                        (DEMO_LOGIN, DEMO_EMAIL)).fetchone()
    if demo is None:
        conn.execute(
            "INSERT INTO users(id,name,email,role,created_at,login_name,password_hash)"
            " VALUES(?,?,?,?,?,?,?)",
            ("U-DEMO", DEMO_NAME, DEMO_EMAIL, DEMO_ROLE,
             "2026-01-01", DEMO_LOGIN, hash_password(DEMO_PASSWORD)))
    conn.commit()


def init_db(reset: bool = False) -> None:
    """建表 + 必要时灌入演示数据。幂等：已有数据不会重复插入。"""
    global _initialized
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if reset and DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(DB_PATH) + suffix)
            if f.exists():
                f.unlink()

    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        _migrate(conn)
        seeded = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
        if seeded == 0:
            from .seed import seed_all

            seed_all(conn)
            conn.commit()
        ensure_accounts(conn)
    finally:
        conn.close()
    _initialized = True


def rows(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def one(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()):
    return conn.execute(sql, params).fetchone()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple | dict = (), default=None):
    row = one(conn, sql, params)
    if row is None:
        return default
    val = row[0]
    return default if val is None else val
