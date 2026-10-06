from __future__ import annotations

import sqlite3
from datetime import date

from . import repo
from .db import iso

BASE_TASKS = [
    {"name": "Trabajo", "area": "trabajo", "hint": "jornada, tareas, pendientes", "default": True},
    {"name": "Estudio", "area": "estudios", "hint": "clases, curso, oposiciones", "default": True},
    {"name": "Ejercicio", "area": "salud", "hint": "dividelo en brazos, piernas...", "default": True},
    {"name": "Cocina", "area": "casa", "hint": "comida del dia, planificar", "default": True},
    {"name": "Entretenimiento", "area": "ocio", "hint": "series, videojuegos, leer", "default": True},
    {"name": "Socializar", "area": "social", "hint": "familia, amigos, llamar a alguien", "default": True},
    {"name": "Limpieza de casa", "area": "casa", "hint": "dividelo por habitaciones", "default": False},
    {"name": "Descanso y sueno", "area": "descanso", "hint": "horas, siesta, desconectar", "default": False},
    {"name": "Salud", "area": "salud", "hint": "citas, medicina, autocuidado", "default": False},
    {"name": "Compras", "area": "casa", "hint": "super, farmacia, encargos", "default": False},
    {"name": "Finanzas", "area": "dinero", "hint": "gastos, ahorro, papeleo", "default": False},
    {"name": "Proyecto personal", "area": "proyecto", "hint": "algo tuyo a largo plazo", "default": False},
]


def default_names() -> list[str]:
    return [item["name"] for item in BASE_TASKS if item["default"]]


def split_extra(text: str) -> list[tuple[str, str | None]]:
    parsed = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, area = (part.strip() for part in line.partition("|"))
        if name:
            parsed.append((name, area or None))
    return parsed


def load_base(
    conn: sqlite3.Connection,
    selected: list[str] | None = None,
    extra: str = "",
    start_day: date | str | None = None,
) -> dict:
    selected = set(selected or [])
    existing = {t["name"].strip().lower() for t in repo.all_tasks(conn)}
    created: list[str] = []
    skipped: list[str] = []

    def add(name: str, area: str | None) -> None:
        key = name.strip().lower()
        if not key or key in existing:
            skipped.append(name)
            return
        repo.create_task(conn, name, area, start_day=start_day, repeat=True)
        existing.add(key)
        created.append(name)

    for item in BASE_TASKS:
        if item["name"] in selected:
            add(item["name"], item["area"])

    for name, area in split_extra(extra):
        add(name, area)

    return {
        "created": created,
        "skipped": skipped,
        "total": len(repo.all_tasks(conn)),
        "day": iso(start_day) if start_day else None,
    }