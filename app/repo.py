from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from .db import clamp_value, iso, now, parse_day
from .rules import completion, done_count, mean_positive, rollup

TASK_COLUMNS = "id, name, area, note, parent_id, start_date, archived_on, repeat, position"


def row_to_task(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def all_tasks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT {TASK_COLUMNS} FROM task ORDER BY parent_id IS NOT NULL, position, id"
    ).fetchall()
    return [row_to_task(r) for r in rows]


def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute(f"SELECT {TASK_COLUMNS} FROM task WHERE id = ?", (task_id,)).fetchone()
    return row_to_task(row) if row else None


def children_of(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT {TASK_COLUMNS} FROM task WHERE parent_id = ? ORDER BY position, id",
        (task_id,),
    ).fetchall()
    return [row_to_task(r) for r in rows]


def areas(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT area FROM task WHERE area IS NOT NULL AND area <> '' ORDER BY area"
    ).fetchall()
    return [r["area"] for r in rows]


def visible_tasks(tasks: list[dict], day: str) -> list[dict]:
    visible = []
    for task in tasks:
        if task["start_date"] > day:
            continue
        if not task.get("repeat", 1) and task["start_date"] != day:
            continue
        if task["archived_on"] is not None and task["archived_on"] <= day:
            continue
        visible.append(task)
    return visible


def entries_for(conn: sqlite3.Connection, day: str) -> dict[int, tuple[int, str]]:
    rows = conn.execute("SELECT task_id, value, note FROM entry WHERE day = ?", (day,)).fetchall()
    return {r["task_id"]: (r["value"], r["note"] or "") for r in rows}


def entries_range(conn: sqlite3.Connection, start: str, end: str) -> dict[str, dict[int, int]]:
    rows = conn.execute(
        "SELECT day, task_id, value FROM entry WHERE day >= ? AND day <= ?", (start, end)
    ).fetchall()
    out: dict[str, dict[int, int]] = defaultdict(dict)
    for r in rows:
        out[r["day"]][r["task_id"]] = r["value"]
    return out


def entry_parts(raw) -> tuple[int, str]:
    if raw is None:
        return 0, ""
    if isinstance(raw, tuple):
        return raw[0], raw[1] or ""
    return raw, ""


def _drop_leaves(item: dict) -> None:
    item.pop("_leaves", None)
    for child in item["children"]:
        _drop_leaves(child)


def compute_day(tasks: list[dict], entries: dict[int, tuple[int, str] | int]) -> dict:
    kids: dict[int, list[dict]] = defaultdict(list)
    for task in tasks:
        if task["parent_id"]:
            kids[task["parent_id"]].append(task)

    def build(task: dict) -> dict:
        children = kids.get(task["id"], [])
        child_items = [build(c) for c in children]
        routine = [c for c in child_items if not c["agenda"]]
        agenda = not task.get("repeat", 1)
        auto = bool(routine)
        own = float(entry_parts(entries.get(task["id"]))[0])
        value = rollup([c["value"] for c in routine]) if auto else own
        _, day_note = entry_parts(entries.get(task["id"]))

        if auto:
            leaves = [leaf for child in routine for leaf in child["_leaves"]]
        elif agenda and not children:
            leaves = []
        else:
            leaves = [own]

        return {
            **task,
            "value": value,
            "done": value > 0,
            "auto": auto,
            "agenda": agenda,
            "day_note": day_note,
            "children": child_items,
            "children_total": len(routine),
            "completion": completion([c["value"] for c in routine]) if routine else None,
            "done_children": done_count([c["value"] for c in routine]),
            "_leaves": leaves,
        }

    items = [build(t) for t in tasks if not t["parent_id"]]

    top = [i["value"] for i in items if not i["agenda"]]
    if not top:
        top = [i["value"] for i in items]

    leaves = [leaf for item in items for leaf in item["_leaves"]]
    if not leaves:
        leaves = [i["value"] for i in items if i["agenda"]]

    for item in items:
        _drop_leaves(item)

    summary = {
        "avg": mean_positive(top),
        "done": done_count(top),
        "total": len(top),
        "completion": completion(top),
        "leaf_done": done_count(leaves),
        "leaf_total": len(leaves),
        "leaf_completion": completion(leaves),
    }
    return {"items": items, "summary": summary}


def flatten(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        out.append(item)
        out.extend(flatten(item["children"]))
    return out


def day_view(conn: sqlite3.Connection, day: date | str) -> dict:
    day_str = iso(day)
    visible = visible_tasks(all_tasks(conn), day_str)
    computed = compute_day(visible, entries_for(conn, day_str))
    return {"day": day_str, **computed, "note": get_day_note(conn, day_str)}


def get_day_note(conn: sqlite3.Connection, day: str) -> str:
    row = conn.execute("SELECT content FROM day_note WHERE day = ?", (day,)).fetchone()
    return row["content"] if row else ""


def save_day_note(conn: sqlite3.Connection, day: str, content: str) -> None:
    content = content or ""
    existing = conn.execute("SELECT day FROM day_note WHERE day = ?", (day,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE day_note SET content = ?, updated_at = ? WHERE day = ?",
            (content, now(), day),
        )
    else:
        conn.execute(
            "INSERT INTO day_note (day, content, updated_at) VALUES (?, ?, ?)",
            (day, content, now()),
        )


def set_entry(
    conn: sqlite3.Connection,
    task_id: int,
    day: str,
    value,
    note: str | None = None,
) -> None:
    task = get_task(conn, task_id)
    if not task:
        raise ValueError(f"La tarea {task_id} no existe")
    if task["start_date"] > day:
        raise ValueError("La tarea todavia no empieza ese dia")
    if task["archived_on"] is not None and task["archived_on"] <= day:
        raise ValueError("La tarea esta borrada ese dia")
    if not task.get("repeat", 1) and day != task["start_date"]:
        raise ValueError("Eso es una anotacion de agenda: solo existe en su dia")
    active_children = conn.execute(
        """
        SELECT 1 FROM task
        WHERE parent_id = ? AND start_date <= ? AND (archived_on IS NULL OR archived_on > ?)
        LIMIT 1
        """,
        (task_id, day, day),
    ).fetchone()
    if active_children:
        raise ValueError("Esa tarea tiene subtareas: puntua las subtareas, el total se calcula solo")

    number = clamp_value(value)
    current = entries_for(conn, day).get(task_id, (0, ""))[1]
    text = current if note is None else (note or "")

    if number == 0 and not text.strip():
        conn.execute("DELETE FROM entry WHERE task_id = ? AND day = ?", (task_id, day))
    else:
        exists = conn.execute(
            "SELECT 1 FROM entry WHERE task_id = ? AND day = ?", (task_id, day)
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE entry SET value = ?, note = ?, updated_at = ? WHERE task_id = ? AND day = ?",
                (number, text, now(), task_id, day),
            )
        else:
            conn.execute(
                "INSERT INTO entry (task_id, day, value, note, updated_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, day, number, text, now()),
            )


def next_position(conn: sqlite3.Connection, parent_id: int | None) -> int:
    if parent_id is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM task WHERE parent_id IS NULL"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM task WHERE parent_id = ?",
            (parent_id,),
        ).fetchone()
    return row["p"]


def create_task(
    conn: sqlite3.Connection,
    name: str,
    area: str | None = None,
    note: str | None = None,
    parent_id: int | None = None,
    start_day: date | str | None = None,
    repeat: bool = True,
) -> int:
    clean_name = clean_text(name)
    if not clean_name:
        raise ValueError("El nombre de la tarea esta vacio")

    start = iso(start_day) if start_day else date.today().isoformat()

    if parent_id is not None:
        parent = get_task(conn, parent_id)
        if not parent:
            raise ValueError("La tarea padre no existe")
        if parent["parent_id"]:
            raise ValueError("Solo se admiten dos niveles: no puedes anadir un tercer nivel")
        child_start = iso(parent["start_date"])
        if start < child_start:
            start = child_start
        if parent.get("repeat", 1) == 0:
            start = iso(parent["start_date"])
            repeat = False

    cur = conn.execute(
        """
        INSERT INTO task (name, area, note, parent_id, start_date, repeat, position,
                          created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_name,
            clean_text(area),
            clean_text(note),
            parent_id,
            start,
            1 if repeat else 0,
            next_position(conn, parent_id),
            now(),
            now(),
        ),
    )
    return cur.lastrowid


def update_task(
    conn: sqlite3.Connection,
    task_id: int,
    name: str | None = None,
    area: str | None = None,
    note: str | None = None,
) -> None:
    task = get_task(conn, task_id)
    if not task:
        raise ValueError(f"La tarea {task_id} no existe")
    new_name = clean_text(name) if name is not None else task["name"]
    if not new_name:
        raise ValueError("El nombre de la tarea esta vacio")
    new_area = clean_text(area) if area is not None else task["area"]
    new_note = clean_text(note) if note is not None else task["note"]
    conn.execute(
        "UPDATE task SET name = ?, area = ?, note = ?, updated_at = ? WHERE id = ?",
        (new_name, new_area, new_note, now(), task_id),
    )


def archive_task(conn: sqlite3.Connection, task_id: int, from_day: date | str | None = None) -> None:
    task = get_task(conn, task_id)
    if not task:
        raise ValueError(f"La tarea {task_id} no existe")
    day = iso(from_day) if from_day else date.today().isoformat()
    if day < task["start_date"]:
        day = task["start_date"]
    conn.execute("UPDATE task SET archived_on = ?, updated_at = ? WHERE id = ?", (day, now(), task_id))


def restore_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE task SET archived_on = NULL, updated_at = ? WHERE id = ?", (now(), task_id))


def delete_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("DELETE FROM task WHERE id = ?", (task_id,))


def archive_children(conn: sqlite3.Connection, task_id: int, from_day: date | str | None = None) -> None:
    day = iso(from_day) if from_day else date.today().isoformat()
    rows = conn.execute("SELECT id, start_date FROM task WHERE parent_id = ?", (task_id,)).fetchall()
    for row in rows:
        start = max(day, row["start_date"])
        conn.execute("UPDATE task SET archived_on = ?, updated_at = ? WHERE id = ?", (start, now(), row["id"]))


def restore_children(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        "UPDATE task SET archived_on = NULL, updated_at = ? WHERE parent_id = ?", (now(), task_id)
    )


def history(conn: sqlite3.Connection, end_day: date | str, days: int) -> list[dict]:
    end = parse_day(iso(end_day))
    start = end - timedelta(days=max(0, days - 1))
    tasks = all_tasks(conn)
    by_day = entries_range(conn, iso(start), iso(end))

    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_str = day.isoformat()
        visible = visible_tasks(tasks, day_str)
        computed = compute_day(visible, by_day.get(day_str, {}))
        flat = flatten(computed["items"])
        values = {int(item["id"]): item["value"] for item in flat}
        summary = computed["summary"]
        rows.append(
            {
                "day": day_str,
                "values": values,
                "avg": summary["avg"],
                "done": summary["done"],
                "total": summary["total"],
                "completion": summary["completion"],
                "leaf_done": summary["leaf_done"],
                "leaf_total": summary["leaf_total"],
                "has_activity": summary["done"] > 0,
            }
        )
    return rows


def area_breakdown(conn: sqlite3.Connection, end_day: date | str, days: int) -> list[dict]:
    end = parse_day(iso(end_day))
    start = end - timedelta(days=max(0, days - 1))
    tasks = all_tasks(conn)
    by_day = entries_range(conn, iso(start), iso(end))
    buckets: dict[str, list[float]] = defaultdict(list)
    for offset in range(days):
        day_str = (start + timedelta(days=offset)).isoformat()
        visible = visible_tasks(tasks, day_str)
        computed = compute_day(visible, by_day.get(day_str, {}))
        for item in flatten(computed["items"]):
            buckets[item["area"] or "sin area"].append(item["value"])
    out = []
    for area, values in buckets.items():
        positives = [v for v in values if v > 0]
        out.append(
            {
                "area": area,
                "days": len(values),
                "done": done_count(values),
                "avg": mean_positive(values),
            }
        )
    out.sort(key=lambda a: (-(a["avg"] or 0), a["area"]))
    return out


def task_stats(conn: sqlite3.Connection, task_id: int, days: int) -> dict | None:
    task = get_task(conn, task_id)
    if not task:
        return None
    today = date.today()
    rows = history(conn, today, days)
    values = [row["values"].get(task_id, 0) for row in rows if row["values"]]
    positives = [v for v in values if v > 0]
    return {
        "task": task,
        "samples": len(values),
        "done": done_count(values),
        "avg": mean_positive(values),
        "best": max(positives) if positives else None,
        "recent": rows[-28:],
    }