from __future__ import annotations

from datetime import date, timedelta

MAX_VALUE = 10


def is_done(value: float) -> bool:
    return value > 0


def rollup(children_values: list[float]) -> float:
    """El padre toma la media de los hijos hechos; 0 si no se hizo ninguno."""
    positives = [v for v in children_values if is_done(v)]
    if not positives:
        return 0.0
    return sum(positives) / len(positives)


def done_count(values: list[float]) -> int:
    return sum(1 for v in values if is_done(v))


def completion(values: list[float]) -> float | None:
    if not values:
        return None
    return done_count(values) / len(values)


def mean_positive(values: list[float]) -> float | None:
    positives = [v for v in values if is_done(v)]
    if not positives:
        return None
    return sum(positives) / len(positives)


def format_number(value: float | None) -> str:
    if value is None:
        return "-"
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def day_label(day: date) -> str:
    names = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    return names[day.weekday()]


def streak_days(days: list[dict], today: date) -> int:
    """Dias consecutivos con alguna actividad, contados hacia atras desde hoy."""
    active = {d["day"]: d for d in days}
    count = 0
    cursor = today
    while True:
        row = active.get(cursor.isoformat())
        if row is None or not row.get("has_activity"):
            if cursor == today:
                cursor -= timedelta(days=1)
                continue
            break
        count += 1
        cursor -= timedelta(days=1)
    return count


def last_done_days(days: list[dict], today: date, task_id: int) -> int | None:
    ordered = sorted((d for d in days if d["day"] <= today.isoformat()), key=lambda d: d["day"])
    for offset, row in enumerate(reversed(ordered)):
        if row.get("values", {}).get(task_id, 0) > 0:
            return offset
    return None