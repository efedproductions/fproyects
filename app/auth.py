from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import now

COOKIE_NAME = "diario_session"
SESSION_DAYS = 30
ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 260_000
MIN_PASSWORD_LENGTH = 10
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class AuthError(Exception):
    pass


def random_password(length: int = 16) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def hash_password(password: str, salt: str | None = None, iterations: int = ITERATIONS) -> str:
    if not password:
        raise AuthError("La contraseña no puede estar vacía")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return f"{ALGORITHM}${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        computed = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        ).hex()
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(computed, digest)


def get_user(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM app_user WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM app_user").fetchone()["n"]


def create_user(conn: sqlite3.Connection, username: str, password: str) -> dict:
    username = (username or "").strip()
    if not username:
        raise AuthError("El usuario no puede estar vacío")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"La contraseña necesita al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    if get_user(conn, username):
        raise AuthError(f"El usuario «{username}» ya existe")
    conn.execute(
        "INSERT INTO app_user (username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (username, hash_password(password), now(), now()),
    )
    return get_user(conn, username)


def set_password(conn: sqlite3.Connection, username: str, password: str) -> dict:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"La contraseña necesita al menos {MIN_PASSWORD_LENGTH} caracteres")
    user = get_user(conn, username)
    if not user:
        raise AuthError(f"El usuario «{username}» no existe")
    conn.execute(
        "UPDATE app_user SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(password), now(), user["id"]),
    )
    delete_sessions(conn, user_id=user["id"])
    return get_user(conn, username)


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM app_user WHERE username = ?", (username,))


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    user = get_user(conn, username)
    if not user:
        hash_password(password or "")
        return None
    if not verify_password(password or "", user["password_hash"]):
        return None
    return user


def create_session(
    conn: sqlite3.Connection, user_id: int, user_agent: str | None = None
) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(days=SESSION_DAYS)
    conn.execute(
        "INSERT INTO session (token, user_id, created_at, expires_at, user_agent) VALUES (?, ?, ?, ?, ?)",
        (token, user_id, created.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"),
         (user_agent or "")[:200]),
    )
    return token, expires.isoformat(timespec="seconds")


def user_for_token(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT u.id, u.username, s.expires_at
        FROM session s JOIN app_user u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
        """,
        (token, now()),
    ).fetchone()
    return dict(row) if row else None


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM session WHERE token = ?", (token,))


def delete_sessions(conn: sqlite3.Connection, user_id: int | None = None) -> None:
    if user_id is None:
        conn.execute("DELETE FROM session")
    else:
        conn.execute("DELETE FROM session WHERE user_id = ?", (user_id,))


def purge_expired_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM session WHERE expires_at <= ?", (now(),))


class LoginThrottle:
    """Limita intentos fallidos por IP para frenar fuerza bruta."""

    def __init__(self, max_attempts: int = 8, window_minutes: int = 10) -> None:
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)
        self.attempts: dict[str, list[datetime]] = {}

    def _prune(self, key: str) -> list[datetime]:
        moment = datetime.now(timezone.utc)
        recent = [t for t in self.attempts.get(key, []) if moment - t < self.window]
        self.attempts[key] = recent
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max_attempts

    def fail(self, key: str) -> None:
        self._prune(key).append(datetime.now(timezone.utc))

    def succeed(self, key: str) -> None:
        self.attempts.pop(key, None)