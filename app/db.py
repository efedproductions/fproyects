from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "diario.db"
MAX_VALUE = 10

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    area TEXT,
    note TEXT,
    parent_id INTEGER REFERENCES task(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    archived_on TEXT,
    repeat INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE TABLE IF NOT EXISTS entry (
    task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, day),
    CHECK (value BETWEEN 0 AND 10)
);

CREATE TABLE IF NOT EXISTS day_note (
    day TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_parent ON task(parent_id);
CREATE INDEX IF NOT EXISTS idx_task_start ON task(start_date);
CREATE INDEX IF NOT EXISTS idx_entry_day ON entry(day);
CREATE INDEX IF NOT EXISTS idx_session_user ON session(user_id);
"""


def db_path() -> Path:
    import os

    return Path(os.environ.get("DIARIO_DB", DEFAULT_DB_PATH))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task)").fetchall()}
    if "repeat" not in columns:
        conn.execute("ALTER TABLE task ADD COLUMN repeat INTEGER NOT NULL DEFAULT 1")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def iso(day: date | str) -> str:
    return day if isinstance(day, str) else day.isoformat()


def parse_day(value: str | None, fallback: date | None = None) -> date:
    if not value:
        return fallback or date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Fecha invalida: {value!r}, se espera YYYY-MM-DD") from exc


def clamp_value(value) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Valor invalido: {value!r}") from exc
    return max(0, min(MAX_VALUE, number))