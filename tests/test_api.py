from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import unquote

from app import repo

TODAY = date.today()
DAY = TODAY.isoformat()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


def create(client, name, **extra):
    data = {"name": name, "start_date": DAY, "area": "", "note": "", "parent_id": ""}
    data.update({k: str(v) for k, v in extra.items() if v is not None})
    response = client.post("/tareas/crear", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response


def task_id(conn, name):
    return next(t["id"] for t in repo.all_tasks(conn) if t["name"] == name)


def value_of(conn, name, day=DAY):
    view = repo.day_view(conn, day)
    return next(i["value"] for i in repo.flatten(view["items"]) if i["name"] == name)


def test_task_appears_every_day_with_zero(client, conn):
    create(client, "Limpieza en casa", area="casa")
    assert value_of(conn, "Limpieza en casa", DAY) == 0
    assert value_of(conn, "Limpieza en casa", TOMORROW) == 0


def test_task_does_not_appear_before_its_start(client, conn):
    create(client, "Terapia", start_date=DAY)
    view = repo.day_view(conn, YESTERDAY)
    assert view["items"] == []


def test_parent_is_the_average_of_children_without_zero(client, conn):
    create(client, "Ejercicio")
    create(client, "Ejercicio de brazos", parent_id=task_id(conn, "Ejercicio"))
    create(client, "Abdominales", parent_id=task_id(conn, "Ejercicio"))
    create(client, "Piernas", parent_id=task_id(conn, "Ejercicio"))

    client.post("/api/entry", json={"task_id": task_id(conn, "Ejercicio de brazos"), "day": DAY, "value": 5})
    client.post("/api/entry", json={"task_id": task_id(conn, "Abdominales"), "day": DAY, "value": 5})

    assert value_of(conn, "Ejercicio de brazos") == 5
    assert value_of(conn, "Piernas") == 0
    assert value_of(conn, "Ejercicio") == 5

    summary = repo.day_view(conn, DAY)["summary"]
    assert summary["avg"] == 5
    assert summary["done"] == 1
    assert summary["total"] == 1
    assert summary["leaf_done"] == 2
    assert summary["leaf_total"] == 3


def test_parent_is_zero_when_no_child_was_done(client, conn):
    create(client, "Ejercicio")
    create(client, "Brazos", parent_id=task_id(conn, "Ejercicio"))
    create(client, "Piernas", parent_id=task_id(conn, "Ejercicio"))
    client.post("/api/entry", json={"task_id": task_id(conn, "Brazos"), "day": DAY, "value": 4})
    client.post("/api/entry", json={"task_id": task_id(conn, "Brazos"), "day": DAY, "value": 0})
    assert value_of(conn, "Ejercicio") == 0


def test_partial_day_is_not_penalised(client, conn):
    create(client, "Limpieza en casa")
    create(client, "Bano", parent_id=task_id(conn, "Limpieza en casa"))
    create(client, "Cocina", parent_id=task_id(conn, "Limpieza en casa"))
    client.post("/api/entry", json={"task_id": task_id(conn, "Bano"), "day": DAY, "value": 8})
    assert value_of(conn, "Limpieza en casa") == 8


def test_third_level_is_rejected(client, conn):
    create(client, "Casa")
    create(client, "Bano", parent_id=task_id(conn, "Casa"))
    response = create(client, "Grifo", parent_id=task_id(conn, "Bano"))
    assert [t["name"] for t in repo.all_tasks(conn)].count("Grifo") == 0
    page = client.get(response.headers["location"], follow_redirects=True)
    assert "tercer nivel" in page.text


def test_deleting_stops_future_days_but_keeps_history(client, conn):
    create(client, "Lectura", start_date=YESTERDAY)
    client.post("/api/entry", json={"task_id": task_id(conn, "Lectura"), "day": YESTERDAY, "value": 7})
    client.post("/tareas/%d/archivar" % task_id(conn, "Lectura"), data={"day": DAY})

    assert value_of(conn, "Lectura", YESTERDAY) == 7
    assert [i["name"] for i in repo.day_view(conn, DAY)["items"]] == []
    assert [i["name"] for i in repo.day_view(conn, TOMORROW)["items"]] == []

    rows = {r["day"]: r for r in repo.history(conn, TODAY, 5)}
    assert rows[YESTERDAY]["values"][task_id(conn, "Lectura")] == 7
    assert rows[DAY]["has_activity"] is False


def test_restore_brings_the_task_back(client, conn):
    create(client, "Lectura")
    client.post("/tareas/%d/archivar" % task_id(conn, "Lectura"), data={"day": DAY})
    client.post("/tareas/%d/restaurar" % task_id(conn, "Lectura"))
    assert value_of(conn, "Lectura", TOMORROW) == 0


def test_entry_on_archived_day_is_rejected(client, conn):
    create(client, "Lectura")
    client.post("/tareas/%d/archivar" % task_id(conn, "Lectura"), data={"day": DAY})
    response = client.post(
        "/api/entry", json={"task_id": task_id(conn, "Lectura"), "day": TOMORROW, "value": 5}
    )
    assert response.status_code == 400


def test_value_out_of_range_is_clamped(client, conn):
    create(client, "Lectura")
    response = client.post(
        "/api/entry", json={"task_id": task_id(conn, "Lectura"), "day": DAY, "value": 42}
    )
    assert response.status_code == 200
    assert value_of(conn, "Lectura") == 10


def test_api_returns_recalculated_values_for_the_whole_day(client, conn):
    create(client, "Casa")
    create(client, "Bano", parent_id=task_id(conn, "Casa"))
    create(client, "Cocina", parent_id=task_id(conn, "Casa"))
    parent = task_id(conn, "Casa")
    bano = task_id(conn, "Bano")
    cocina = task_id(conn, "Cocina")

    payload = client.post("/api/entry", json={"task_id": bano, "day": DAY, "value": 5}).json()
    assert payload["values"][str(parent)] == 5

    payload = client.post("/api/entry", json={"task_id": cocina, "day": DAY, "value": 3}).json()
    assert payload["values"][str(parent)] == 4
    assert payload["summary"]["leaf_done"] == 2


def test_form_fallback_works_without_javascript(client, conn):
    create(client, "Lectura", area="aprender")
    tid = task_id(conn, "Lectura")
    response = client.post(
        "/entrada", data={"task_id": tid, "day": DAY, "quick": "6"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert value_of(conn, "Lectura") == 6

    response = client.post(
        "/entrada", data={"task_id": tid, "day": DAY, "value": ""}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "Falta el valor" in unquote(response.headers["location"])


def test_day_note_is_saved_and_reloaded(client, conn):
    client.post("/nota-dia", data={"day": DAY, "content": "Buen dia"}, follow_redirects=False)
    assert repo.get_day_note(conn, DAY) == "Buen dia"
    client.post("/api/nota-dia", json={"day": DAY, "content": "Corregido"})
    assert repo.get_day_note(conn, DAY) == "Corregido"
    assert "Corregido" in client.get("/").text


def test_day_pages_render(client, conn):
    create(client, "Casa")
    create(client, "Bano", parent_id=task_id(conn, "Casa"))
    for url in ("/", f"/?day={YESTERDAY}", "/tareas", "/estadisticas", "/estadisticas?days=90"):
        assert client.get(url).status_code == 200, url


def test_removing_children_makes_the_parent_editable_again(client, conn):
    create(client, "Ejercicio")
    create(client, "Brazos", parent_id=task_id(conn, "Ejercicio"))
    parent = task_id(conn, "Ejercicio")

    rejected = client.post("/api/entry", json={"task_id": parent, "day": DAY, "value": 9})
    assert rejected.status_code == 400
    assert "subtareas" in rejected.json()["error"]

    client.post(f"/tareas/{parent}/quitar-hijas", data={"from_date": DAY})
    client.post("/api/entry", json={"task_id": parent, "day": DAY, "value": 2})
    assert value_of(conn, "Ejercicio") == 2


def test_history_keeps_daily_values(client, conn):
    create(client, "Lectura", start_date=YESTERDAY)
    tid = task_id(conn, "Lectura")
    client.post("/api/entry", json={"task_id": tid, "day": YESTERDAY, "value": 7})
    client.post("/api/entry", json={"task_id": tid, "day": DAY, "value": 0})
    rows = {r["day"]: r for r in repo.history(conn, TODAY, 3)}
    assert rows[YESTERDAY]["avg"] == 7
    assert rows[DAY]["avg"] is None
    assert rows[DAY]["has_activity"] is False