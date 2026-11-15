"""Lưu trữ SQLite: dự án, lần chạy (runs), các bước (steps), cài đặt."""
import logging
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS projects(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    provider        TEXT NOT NULL,
    repo_url        TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT 'main',
    username        TEXT NOT NULL DEFAULT '',
    token_enc       TEXT NOT NULL DEFAULT '',
    deploy_dir      TEXT NOT NULL,
    commands        TEXT NOT NULL DEFAULT '',
    poll_interval   INTEGER NOT NULL DEFAULT 60,
    step_timeout    INTEGER NOT NULL DEFAULT 1800,
    enabled         INTEGER NOT NULL DEFAULT 1,
    webhook_secret  TEXT NOT NULL,
    last_sha        TEXT,
    last_poll_at    REAL,
    last_poll_error TEXT,
    created_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger       TEXT NOT NULL,
    status        TEXT NOT NULL,          -- running | success | failed
    commit_sha    TEXT,
    commit_msg    TEXT,
    failed_step   TEXT,
    error_summary TEXT,
    started_at    REAL NOT NULL,
    finished_at   REAL
);
CREATE TABLE IF NOT EXISTS steps(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    command     TEXT NOT NULL,
    status      TEXT NOT NULL,            -- pending | running | success | failed | skipped
    exit_code   INTEGER,
    started_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, idx);
"""


@contextmanager
def db():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with db() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        # Nâng cấp database cũ: thêm cột kiểu xác thực (basic | bearer)
        cols = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
        for col, ddl in (("auth_mode", "TEXT NOT NULL DEFAULT 'basic'"),
                         ("python_venv", "INTEGER NOT NULL DEFAULT 1"),   # tự tạo .venv + cài requirements
                         ("python_cmd", "TEXT NOT NULL DEFAULT ''"),      # Python dùng để tạo .venv ('' = tự động)
                         ("run_cmd", "TEXT NOT NULL DEFAULT ''"),         # lệnh chạy app (mỗi dòng 1 tiến trình)
                         ("run_mode", "TEXT NOT NULL DEFAULT 'service'"), # service (chạy nền) | once (chạy 1 lần)
                         ("run_wait", "INTEGER NOT NULL DEFAULT 8"),      # giây theo dõi sau khi khởi động
                         ("run_health_url", "TEXT NOT NULL DEFAULT ''"),  # URL kiểm tra app đã sẵn sàng
                         ("app_autostart", "INTEGER NOT NULL DEFAULT 1"), # tự chạy app khi CICD App khởi động
                         ("run_detect", "INTEGER NOT NULL DEFAULT 1"),    # tự nhận diện lệnh chạy app khi để trống
                         ("app_procs", "TEXT NOT NULL DEFAULT '[]'")):    # các tiến trình app đang chạy (JSON)
            if col not in cols:
                c.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
        # Tên project là duy nhất (không phân biệt hoa/thường vì tên cũng là tên thư mục trên Windows)
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_name ON projects(name COLLATE NOCASE)")
        except sqlite3.IntegrityError:
            logging.getLogger("cicd.store").warning(
                "Đang có các project trùng tên trong database - hãy đổi tên để bật ràng buộc tên duy nhất.")
        # Các run đang "running" khi app bị tắt -> đánh dấu thất bại
        now = time.time()
        c.execute(
            "UPDATE runs SET status='failed', finished_at=?, failed_step=COALESCE(failed_step,'Bị gián đoạn'),"
            " error_summary=COALESCE(error_summary,'CICD App bị dừng/khởi động lại trong khi run đang chạy.')"
            " WHERE status='running'",
            (now,),
        )
        c.execute("UPDATE steps SET status='failed', finished_at=? WHERE status='running'", (now,))
        c.execute("UPDATE steps SET status='skipped' WHERE status='pending'")


def query(sql: str, args=()) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def query_one(sql: str, args=()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=()) -> int:
    with db() as c:
        return c.execute(sql, args).lastrowid


# ---- settings ----
def get_setting(key: str) -> str | None:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ---- projects ----
def get_project(pid: int) -> dict | None:
    return query_one("SELECT * FROM projects WHERE id=?", (pid,))


def find_project_by_name(name: str, exclude_id: int | None = None) -> dict | None:
    return query_one("SELECT * FROM projects WHERE name = ? COLLATE NOCASE AND id != ?", (name, exclude_id or 0))


def list_projects() -> list[dict]:
    return query(
        """SELECT p.*, r.id AS last_run_id, r.status AS last_run_status, r.started_at AS last_run_at
           FROM projects p
           LEFT JOIN runs r ON r.id = (SELECT MAX(id) FROM runs WHERE project_id = p.id)
           ORDER BY p.name COLLATE NOCASE"""
    )


# ---- runs / steps ----
def get_run(rid: int) -> dict | None:
    return query_one(
        "SELECT r.*, p.name AS project_name, p.branch AS branch FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.id=?",
        (rid,),
    )


def list_runs(pid: int, limit: int = 50) -> list[dict]:
    return query("SELECT * FROM runs WHERE project_id=? ORDER BY id DESC LIMIT ?", (pid, limit))


def list_steps(rid: int) -> list[dict]:
    return query("SELECT * FROM steps WHERE run_id=? ORDER BY idx", (rid,))


def add_step(rid: int, idx: int, name: str, command: str) -> int:
    return execute("INSERT INTO steps(run_id,idx,name,command,status) VALUES(?,?,?,?,'pending')", (rid, idx, name, command))
