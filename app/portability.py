from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .db import MAX_VALUE, db_path, iso, now

EXPORT_FORMAT = "diario-export"
EXPORT_VERSION = 1
BACKUP_DIR_NAME = "backups"
KEEP_BACKUPS = 14


def backup_dir(base: Path | None = None) -> Path:
    folder = (base or db_path()).parent / BACKUP_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_backup(conn: sqlite3.Connection, base: Path | None = None, keep: int = KEEP_BACKUPS) -> Path:
    folder = backup_dir(base)
    target = folder / f"diario-{date.today().isoformat()}.db"
    destination = sqlite3.connect(target)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    prune_backups(folder, keep)
    return target


def prune_backups(folder: Path, keep: int = KEEP_BACKUPS) -> None:
    files = sorted(folder.glob("diario-*.db"))
    for stale in files[:-keep] if len(files) > keep else []:
        stale.unlink(missing_ok=True)


def latest_backup_age(folder: Path) -> timedelta | None:
    files = sorted(folder.glob("diario-*.db"))
    if not files:
        return None
    stamp = datetime.fromtimestamp(files[-1].stat().st_mtime, tz=timezone.utc)
    return datetime.now(timezone.utc) - stamp


def backup_if_needed(conn: sqlite3.Connection, base: Path | None = None) -> Path | None:
    age = latest_backup_age(backup_dir(base))
    if age is None or age > timedelta(hours=24):
        return create_backup(conn, base)
    return None


def export_json(conn: sqlite3.Connection) -> dict:
    tasks = [dict(r) for r in conn.execute("SELECT * FROM task ORDER BY id").fetchall()]
    entries = [dict(r) for r in conn.execute("SELECT * FROM entry ORDER BY day, task_id").fetchall()]
    day_notes = [dict(r) for r in conn.execute("SELECT * FROM day_note ORDER BY day").fetchall()]
    users = [r["username"] for r in conn.execute("SELECT username FROM app_user").fetchall()]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": now(),
        "users": users,
        "tasks": tasks,
        "entries": entries,
        "day_notes": day_notes,
    }


def _task_paths(conn: sqlite3.Connection) -> dict[int, dict]:
    tasks = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM task").fetchall()}
    paths: dict[int, dict] = {}

    def resolve(task_id: int, seen: frozenset = frozenset()) -> str:
        task = tasks[task_id]
        parent = task["parent_id"]
        if not parent or parent in seen:
            return task["name"]
        return f"{resolve(parent, seen | {task_id})} > {task['name']}"

    for task_id, task in tasks.items():
        paths[task_id] = {
            **task,
            "path": resolve(task_id),
        }
    return paths


def export_entries_csv(conn: sqlite3.Connection) -> str:
    paths = _task_paths(conn)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["dia", "tarea", "area", "valor", "nota"])
    rows = conn.execute(
        """
        SELECT e.day, e.value, e.note, e.task_id FROM entry e ORDER BY e.day, e.task_id
        """
    ).fetchall()
    for row in rows:
        task = paths.get(row["task_id"], {"path": "?", "area": ""})
        writer.writerow([row["day"], task["path"], task["area"] or "", row["value"], row["note"] or ""])
    return buffer.getvalue()


def export_notes_csv(conn: sqlite3.Connection) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["dia", "nota"])
    for row in conn.execute("SELECT day, content FROM day_note ORDER BY day"):
        writer.writerow([row["day"], row["content"]])
    return buffer.getvalue()


def payload_paths(tasks: list[dict]) -> dict[int, str]:
    by_id = {t["id"]: t for t in tasks}

    def resolve(task_id: int, seen: frozenset = frozenset()) -> str:
        task = by_id[task_id]
        parent = task.get("parent_id")
        if not parent or parent not in by_id or parent in seen:
            return task["name"]
        return f"{resolve(parent, seen | {task_id})} > {task['name']}"

    return {task_id: resolve(task_id) for task_id in by_id}


def import_json(conn: sqlite3.Connection, payload: dict, mode: str = "replace") -> dict:
    if payload.get("format") != EXPORT_FORMAT:
        raise ValueError("El fichero no es una exportacion del diario")
    tasks = payload.get("tasks") or []
    entries = payload.get("entries") or []
    day_notes = payload.get("day_notes") or []

    if mode == "replace":
        conn.execute("DELETE FROM entry")
        conn.execute("DELETE FROM task")
        conn.execute("DELETE FROM day_note")

    existing = _task_paths(conn)
    by_key: dict[tuple[str, str], int] = {
        (t["path"], t["start_date"]): task_id for task_id, t in existing.items()
    }

    incoming_paths = payload_paths(tasks)
    id_map: dict[int, int] = {}
    created = matched = 0
    for task in sorted(tasks, key=lambda t: t["id"]):
        path = incoming_paths.get(task["id"], task["name"])
        parent_path = None
        name = task["name"]
        if " > " in path:
            parent_path, name = path.rsplit(" > ", 1)
        key = (path, task["start_date"])
        if key in by_key:
            id_map[task["id"]] = by_key[key]
            matched += 1
            continue
        parent_id = id_map.get(task["parent_id"]) if task.get("parent_id") else None
        if task.get("parent_id") and parent_id is None:
            parent_id = by_key.get((parent_path or "", task["start_date"]))
        cursor = conn.execute(
            """
            INSERT INTO task (name, area, note, parent_id, start_date, archived_on, position,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                task.get("area"),
                task.get("note"),
                parent_id,
                task["start_date"],
                task.get("archived_on"),
                task.get("position") or 0,
                task.get("created_at") or now(),
                task.get("updated_at") or now(),
            ),
        )
        id_map[task["id"]] = cursor.lastrowid
        by_key[key] = cursor.lastrowid
        created += 1

    imported_entries = skipped_entries = 0
    for entry in entries:
        task_id = id_map.get(entry["task_id"])
        if not task_id:
            continue
        value = max(0, min(MAX_VALUE, int(entry.get("value") or 0)))
        exists = conn.execute(
            "SELECT 1 FROM entry WHERE task_id = ? AND day = ?", (task_id, entry["day"])
        ).fetchone()
        if exists:
            skipped_entries += 1
            continue
        conn.execute(
            "INSERT INTO entry (task_id, day, value, note, updated_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, entry["day"], value, entry.get("note") or "", entry.get("updated_at") or now()),
        )
        imported_entries += 1

    imported_notes = 0
    for note in day_notes:
        exists = conn.execute("SELECT 1 FROM day_note WHERE day = ?", (note["day"],)).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO day_note (day, content, updated_at) VALUES (?, ?, ?)",
            (note["day"], note.get("content") or "", note.get("updated_at") or now()),
        )
        imported_notes += 1

    return {
        "mode": mode,
        "tasks_created": created,
        "tasks_matched": matched,
        "entries_imported": imported_entries,
        "entries_skipped": skipped_entries,
        "day_notes_imported": imported_notes,
    }


def summary(conn: sqlite3.Connection) -> dict:
    def count(sql: str, params: tuple = ()) -> int:
        return conn.execute(sql, params).fetchone()[0]

    return {
        "tareas": count("SELECT COUNT(*) FROM task"),
        "tareas_activas": count(
            "SELECT COUNT(*) FROM task WHERE archived_on IS NULL OR archived_on > ?",
            (iso(date.today()),),
        ),
        "valores": count("SELECT COUNT(*) FROM entry"),
        "notas_dia": count("SELECT COUNT(*) FROM day_note"),
        "usuarios": count("SELECT COUNT(*) FROM app_user"),
        "ultimo_valor": conn.execute("SELECT MAX(day) AS d FROM entry").fetchone()["d"],
    }


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)