from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

USERNAME = "tester"
PASSWORD = "contrasena-larga"


@pytest.fixture()
def db_file(tmp_path):
    path = tmp_path / "diario-test.db"
    os.environ["DIARIO_DB"] = str(path)
    yield path
    os.environ.pop("DIARIO_DB", None)


@pytest.fixture()
def conn(db_file):
    from app.db import connect

    connection = connect(db_file)
    yield connection
    connection.close()


@pytest.fixture()
def client(db_file):
    from fastapi.testclient import TestClient

    from app import auth, main
    from app.db import connect

    main.throttle = auth.LoginThrottle()

    setup = connect(db_file)
    try:
        if not auth.get_user(setup, USERNAME):
            auth.create_user(setup, USERNAME, PASSWORD)
    finally:
        setup.close()

    with TestClient(main.app) as test_client:
        response = test_client.post(
            "/login",
            data={"username": USERNAME, "password": PASSWORD, "next": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        yield test_client


@pytest.fixture()
def anon(db_file):
    from fastapi.testclient import TestClient

    from app import auth, main
    from app.db import connect

    main.throttle = auth.LoginThrottle()

    setup = connect(db_file)
    try:
        if not auth.get_user(setup, USERNAME):
            auth.create_user(setup, USERNAME, PASSWORD)
    finally:
        setup.close()

    with TestClient(main.app) as test_client:
        yield test_client