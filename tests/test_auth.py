from __future__ import annotations

import json

from app import auth
from app.db import connect

from .conftest import PASSWORD, USERNAME


def test_pages_require_login(anon):
    response = anon.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_page_is_public(anon):
    response = anon.get("/login")
    assert response.status_code == 200
    assert "Este despliegue es privado" in response.text


def test_wrong_password_is_rejected(anon):
    response = anon.post("/login", data={"username": USERNAME, "password": "incorrecta"})
    assert response.status_code == 401
    assert "incorrectos" in response.text


def test_login_sets_httponly_cookie(anon):
    response = anon.post(
        "/login", data={"username": USERNAME, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert anon.get("/").status_code == 200


def test_api_without_session_returns_json_401(anon):
    response = anon.get("/api/resumen", headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert "error" in response.json()


def test_cross_origin_post_is_blocked(client):
    response = client.post("/api/nota-dia", json={"day": "2026-01-01", "content": "x"},
                            headers={"Origin": "https://otro-sitio.example"})
    assert response.status_code == 403


def test_same_origin_post_is_allowed(client):
    response = client.post("/api/nota-dia", json={"day": "2026-01-01", "content": "x"},
                           headers={"Origin": "http://testserver"})
    assert response.status_code == 200


def test_logout_invalidates_the_session(client):
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_throttling_blocks_repeated_failures(anon):
    for _ in range(8):
        anon.post("/login", data={"username": USERNAME, "password": "nope"})
    response = anon.post("/login", data={"username": USERNAME, "password": PASSWORD})
    assert response.status_code == 429


def test_first_run_creates_an_access_only_once(db_file, monkeypatch):
    monkeypatch.setenv("DIARIO_USER", "ana")
    monkeypatch.setenv("DIARIO_PASSWORD", "una-contrasena-larga")
    from app import main

    conn = connect(db_file)
    main.ensure_user(conn)
    main.ensure_user(conn)

    user = auth.get_user(conn, "ana")
    assert user is not None
    assert auth.verify_password("una-contrasena-larga", user["password_hash"])
    assert conn.execute("SELECT COUNT(*) FROM app_user").fetchone()[0] == 1
    conn.close()


def test_password_hashes_are_salted_and_verifiable():
    first = auth.hash_password(PASSWORD)
    second = auth.hash_password(PASSWORD)
    assert first != second
    assert auth.verify_password(PASSWORD, first)
    assert auth.verify_password(PASSWORD, second)
    assert not auth.verify_password("otra", first)
    assert not auth.verify_password(PASSWORD, "basura")


def test_short_passwords_are_refused(conn):
    try:
        auth.create_user(conn, "corto", "1234")
    except auth.AuthError as exc:
        assert "10 caracteres" in str(exc)
    else:
        raise AssertionError("deberia rechazar contrasenas cortas")


def test_pwa_files_are_public(anon):
    assert anon.get("/manifest.webmanifest").status_code == 200
    manifest = anon.get("/manifest.webmanifest").json()
    assert manifest["display"] == "standalone"
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    assert anon.get("/sw.js").status_code == 200
    assert anon.get("/static/icon-192.png").status_code == 200
    assert anon.get("/healthz").status_code == 200


def test_export_json_round_trip(client, conn):
    client.post("/tareas/crear", data={"name": "Casa", "area": "hogar", "start_date": "2026-02-01"})
    task_id = conn.execute("SELECT id FROM task ORDER BY id DESC LIMIT 1").fetchone()[0]
    client.post("/api/entry", json={"task_id": task_id, "day": "2026-02-01", "value": 7})

    export = client.get("/api/export.json")
    assert export.status_code == 200
    payload = json.loads(export.text)
    assert payload["format"] == "diario-export"
    assert payload["tasks"][0]["name"] == "Casa"
    assert payload["entries"][0]["value"] == 7

    conn.execute("DELETE FROM entry")
    conn.execute("DELETE FROM task")
    report = client.post(
        "/api/importar",
        files={"file": ("copia.json", export.text, "application/json")},
        params={"mode": "replace"},
    )
    assert report.status_code == 200
    assert report.json()["tasks_created"] == 1
    assert report.json()["entries_imported"] == 1

    summary = client.get("/api/estado").json()
    assert summary["tareas"] == 1
    assert summary["valores"] == 1


def test_csv_exports(client):
    csv_text = client.get("/api/export.csv").text
    assert csv_text.splitlines()[0] == "dia,tarea,area,valor,nota"
    assert client.get("/api/notas.csv").text.splitlines()[0] == "dia,nota"


def test_backup_directory_is_created(db_file):
    from app import portability

    conn = connect(db_file)
    path = portability.create_backup(conn)
    assert path.exists()
    assert portability.latest_backup_age(portability.backup_dir()).total_seconds() < 60
    conn.close()