"""Trwały magazyn kont, sesji i zadań serwisowych WebApp."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PASSWORD_ITERATIONS = 240_000
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,50}$")
TODO_STATUSES = ("todo", "in_progress", "done")
TODO_SEVERITIES = ("male", "srednie", "duze", "nie_dotyczy")


class StoreError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_username(username: str) -> str:
    normalized = str(username or "").strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise StoreError(
            422,
            "Login musi mieć 3–50 znaków i może zawierać litery, cyfry, _, . oraz -.",
        )
    return normalized


def validate_password(password: str) -> str:
    password = str(password or "")
    if len(password) < 8:
        raise StoreError(422, "Hasło musi mieć co najmniej 8 znaków.")
    if len(password) > 256:
        raise StoreError(422, "Hasło jest zbyt długie.")
    return password


def password_hash(password: str) -> str:
    password = validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(expected_text.encode())
        actual = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode(), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "manager_id": row["manager_id"],
        "created_at": row["created_at"],
    }


class AppStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('manager', 'employee')),
                    manager_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    engine_id TEXT NOT NULL,
                    cylinder INTEGER NOT NULL,
                    n_cylinders INTEGER,
                    fault_label TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('todo', 'in_progress', 'done')),
                    spectrum_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_todos_owner_status ON todos(owner_id, status);
                """
            )

    @staticmethod
    def _display_name(value: str, fallback: str) -> str:
        result = str(value or "").strip() or fallback
        if len(result) > 80:
            raise StoreError(422, "Nazwa wyświetlana może mieć najwyżej 80 znaków.")
        return result

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        role: str = "manager",
        manager_id: int | None = None,
    ) -> dict[str, Any]:
        username = normalize_username(username)
        if role not in {"manager", "employee"}:
            raise StoreError(422, "Nieprawidłowa rola konta.")
        if role == "employee" and manager_id is None:
            raise StoreError(422, "Pracownik musi mieć przełożonego.")
        created_at = utc_now()
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users(
                        username, display_name, password_hash, role, manager_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        self._display_name(display_name, username),
                        password_hash(password),
                        role,
                        manager_id,
                        created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise StoreError(409, "Konto o takim loginie już istnieje.") from error
        return public_user(row)

    def create_employee(
        self, manager: dict[str, Any], username: str, password: str, display_name: str
    ) -> dict[str, Any]:
        if manager["role"] != "manager":
            raise StoreError(403, "Tylko przełożony może dodawać pracowników.")
        return self.create_user(
            username,
            password,
            display_name,
            role="employee",
            manager_id=int(manager["id"]),
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                """
                INSERT INTO sessions(token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self._token_hash(token),
                    int(user_id),
                    utc_now(),
                    now + SESSION_LIFETIME_SECONDS,
                ),
            )
        return token

    def login(self, username: str, password: str) -> dict[str, Any]:
        username = normalize_username(username)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise StoreError(401, "Nieprawidłowy login lub hasło.")
        user = public_user(row)
        return {"token": self.create_session(user["id"]), "user": user}

    def register(
        self, username: str, password: str, display_name: str = ""
    ) -> dict[str, Any]:
        user = self.create_user(username, password, display_name, role="manager")
        return {"token": self.create_session(user["id"]), "user": user}

    def user_for_token(self, token: str) -> dict[str, Any]:
        if not token:
            raise StoreError(401, "Zaloguj się, aby wykonać tę operację.")
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT u.* FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (self._token_hash(token), now),
            ).fetchone()
        if row is None:
            raise StoreError(401, "Sesja wygasła. Zaloguj się ponownie.")
        return public_user(row)

    def logout(self, token: str) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),)
            )

    def list_employees(self, manager: dict[str, Any]) -> list[dict[str, Any]]:
        if manager["role"] != "manager":
            raise StoreError(403, "Lista pracowników jest dostępna dla przełożonego.")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT u.*,
                       COUNT(t.id) AS todo_count,
                       COALESCE(SUM(t.status = 'done'), 0) AS done_count
                FROM users u
                LEFT JOIN todos t ON t.owner_id = u.id
                WHERE u.manager_id = ? AND u.role = 'employee'
                GROUP BY u.id
                ORDER BY u.display_name COLLATE NOCASE, u.username COLLATE NOCASE
                """,
                (manager["id"],),
            ).fetchall()
        return [
            {
                **public_user(row),
                "todo_count": int(row["todo_count"]),
                "done_count": int(row["done_count"]),
            }
            for row in rows
        ]

    def change_employee_password(
        self, manager: dict[str, Any], employee_id: int, new_password: str
    ) -> dict[str, Any]:
        """Zmienia hasło bez ujawniania starego i wylogowuje pracownika."""
        if manager["role"] != "manager":
            raise StoreError(403, "Tylko przełożony może zmienić hasło pracownika.")
        with self.connect() as connection:
            employee = connection.execute(
                """
                SELECT * FROM users
                WHERE id = ? AND role = 'employee' AND manager_id = ?
                """,
                (int(employee_id), int(manager["id"])),
            ).fetchone()
            if employee is None:
                raise StoreError(404, "Nie znaleziono pracownika w Twoim zespole.")
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash(new_password), int(employee_id)),
            )
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ?", (int(employee_id),)
            )
        return public_user(employee)

    def _accessible_owner(
        self, actor: dict[str, Any], owner_id: int | None
    ) -> sqlite3.Row:
        owner_id = int(owner_id or actor["id"])
        with self.connect() as connection:
            owner = connection.execute(
                "SELECT * FROM users WHERE id = ?", (owner_id,)
            ).fetchone()
        allowed = owner is not None and (
            owner_id == actor["id"]
            or (
                actor["role"] == "manager"
                and owner["role"] == "employee"
                and owner["manager_id"] == actor["id"]
            )
        )
        if not allowed:
            raise StoreError(403, "Nie możesz zarządzać zadaniami tego użytkownika.")
        return owner

    @staticmethod
    def _validate_spectrum(value: Any) -> list[float | None]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or len(value) > 21:
            raise StoreError(422, "Miniatura widma musi zawierać najwyżej 21 punktów.")
        result = []
        for item in value:
            if item is None:
                result.append(None)
                continue
            try:
                number = float(item)
            except (TypeError, ValueError) as error:
                raise StoreError(422, "Widmo zawiera nieprawidłową wartość.") from error
            if not (-1_000_000 <= number <= 1_000_000):
                raise StoreError(422, "Wartość widma jest poza dozwolonym zakresem.")
            result.append(number)
        return result

    @staticmethod
    def _todo_text(value: Any, name: str, maximum: int, required: bool = True) -> str:
        result = str(value or "").strip()
        if required and not result:
            raise StoreError(422, f"Pole „{name}” jest wymagane.")
        if len(result) > maximum:
            raise StoreError(422, f"Pole „{name}” jest zbyt długie.")
        return result

    def create_todo(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        owner = self._accessible_owner(actor, payload.get("owner_id"))
        engine_id = self._todo_text(payload.get("engine_id"), "silnik", 100)
        try:
            cylinder = int(payload.get("cylinder"))
        except (TypeError, ValueError) as error:
            raise StoreError(422, "Numer cylindra musi być liczbą.") from error
        if cylinder < 1 or cylinder > 1000:
            raise StoreError(422, "Numer cylindra jest poza dozwolonym zakresem.")
        severity = str(payload.get("severity") or "nie_dotyczy")
        if severity not in TODO_SEVERITIES:
            raise StoreError(422, "Nieprawidłowa powaga usterki.")
        status = str(payload.get("status") or "todo")
        if status not in TODO_STATUSES:
            raise StoreError(422, "Nieprawidłowy stan zadania.")
        spectrum = self._validate_spectrum(payload.get("spectrum"))
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO todos(
                    owner_id, created_by, engine_id, cylinder, n_cylinders,
                    fault_label, severity, note, status, spectrum_json,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner["id"],
                    actor["id"],
                    engine_id,
                    cylinder,
                    int(payload["n_cylinders"]) if payload.get("n_cylinders") else None,
                    self._todo_text(payload.get("fault_label"), "usterka", 100),
                    severity,
                    self._todo_text(payload.get("note"), "notatka", 1000, False),
                    status,
                    json.dumps(spectrum, ensure_ascii=False),
                    now,
                    now,
                    now if status == "done" else None,
                ),
            )
            todo_id = int(cursor.lastrowid)
        return self.get_todo(actor, todo_id)

    @staticmethod
    def _todo_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "owner_id": int(row["owner_id"]),
            "owner_username": row["owner_username"],
            "owner_display_name": row["owner_display_name"],
            "created_by_username": row["created_by_username"],
            "engine_id": row["engine_id"],
            "cylinder": int(row["cylinder"]),
            "n_cylinders": row["n_cylinders"],
            "fault_label": row["fault_label"],
            "severity": row["severity"],
            "note": row["note"],
            "status": row["status"],
            "spectrum": json.loads(row["spectrum_json"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _todo_select() -> str:
        return """
            SELECT t.*, owner.username AS owner_username,
                   owner.display_name AS owner_display_name,
                   creator.username AS created_by_username,
                   owner.manager_id AS owner_manager_id
            FROM todos t
            JOIN users owner ON owner.id = t.owner_id
            LEFT JOIN users creator ON creator.id = t.created_by
        """

    def get_todo(self, actor: dict[str, Any], todo_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                self._todo_select() + " WHERE t.id = ?", (int(todo_id),)
            ).fetchone()
        if row is None:
            raise StoreError(404, "Nie znaleziono zadania.")
        allowed = row["owner_id"] == actor["id"] or (
            actor["role"] == "manager" and row["owner_manager_id"] == actor["id"]
        )
        if not allowed:
            raise StoreError(403, "Nie możesz przeglądać tego zadania.")
        return self._todo_from_row(row)

    def list_todos(
        self,
        actor: dict[str, Any],
        owner_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        if owner_id is not None:
            owner = self._accessible_owner(actor, owner_id)
            where = "t.owner_id = ?"
            parameters.append(owner["id"])
        elif actor["role"] == "manager":
            where = "(t.owner_id = ? OR owner.manager_id = ?)"
            parameters.extend([actor["id"], actor["id"]])
        else:
            where = "t.owner_id = ?"
            parameters.append(actor["id"])
        if status:
            if status not in TODO_STATUSES:
                raise StoreError(422, "Nieprawidłowy filtr stanu.")
            where += " AND t.status = ?"
            parameters.append(status)
        query = self._todo_select() + f" WHERE {where} " + """
            ORDER BY CASE t.status
                WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 ELSE 2 END,
                t.updated_at DESC, t.id DESC
        """
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._todo_from_row(row) for row in rows]

    def update_todo(
        self, actor: dict[str, Any], todo_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        current = self.get_todo(actor, todo_id)
        updates: dict[str, Any] = {}
        if "fault_label" in payload:
            updates["fault_label"] = self._todo_text(
                payload["fault_label"], "usterka", 100
            )
        if "severity" in payload:
            if payload["severity"] not in TODO_SEVERITIES:
                raise StoreError(422, "Nieprawidłowa powaga usterki.")
            updates["severity"] = payload["severity"]
        if "note" in payload:
            updates["note"] = self._todo_text(
                payload["note"], "notatka", 1000, False
            )
        if "status" in payload:
            if payload["status"] not in TODO_STATUSES:
                raise StoreError(422, "Nieprawidłowy stan zadania.")
            updates["status"] = payload["status"]
            updates["completed_at"] = utc_now() if payload["status"] == "done" else None
        if "owner_id" in payload:
            if actor["role"] != "manager":
                raise StoreError(403, "Tylko przełożony może przepisać zadanie.")
            updates["owner_id"] = self._accessible_owner(
                actor, payload["owner_id"]
            )["id"]
        if not updates:
            raise StoreError(422, "Nie przekazano zmian zadania.")
        updates["updated_at"] = utc_now()
        assignment = ", ".join(f"{column} = ?" for column in updates)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE todos SET {assignment} WHERE id = ?",
                [*updates.values(), int(current["id"])],
            )
        return self.get_todo(actor, todo_id)

    def delete_todo(self, actor: dict[str, Any], todo_id: int) -> None:
        current = self.get_todo(actor, todo_id)
        with self.connect() as connection:
            connection.execute("DELETE FROM todos WHERE id = ?", (current["id"],))
