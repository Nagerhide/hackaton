"""Testy kont, hierarchii pracowników i listy serwisowej."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from WebApp.backend.store import AppStore, StoreError


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = AppStore(
            Path(self.temporary_directory.name) / "workspace.sqlite3"
        )
        manager_session = self.store.register(
            "manager", "manager-pass", "Kierownik"
        )
        self.manager = self.store.user_for_token(manager_session["token"])
        self.employee = self.store.create_employee(
            self.manager, "employee", "employee-pass", "Mechanik"
        )
        employee_session = self.store.login("employee", "employee-pass")
        self.employee_token = employee_session["token"]
        self.employee_actor = self.store.user_for_token(employee_session["token"])

    def tearDown(self):
        self.temporary_directory.cleanup()

    def create_employee_todo(self):
        return self.store.create_todo(
            self.manager,
            {
                "owner_id": self.employee["id"],
                "engine_id": "engine_0049",
                "cylinder": 14,
                "n_cylinders": 16,
                "fault_label": "pompa",
                "severity": "male",
                "spectrum": [1.0, 2.0, None, 3.0],
            },
        )

    def test_manager_creates_employee_and_sees_owner_on_todo(self):
        todo = self.create_employee_todo()
        visible = self.store.list_todos(self.manager)

        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["id"], todo["id"])
        self.assertEqual(visible[0]["owner_username"], "employee")
        self.assertEqual(visible[0]["created_by_username"], "manager")

    def test_employee_updates_own_status_and_fault(self):
        todo = self.create_employee_todo()
        updated = self.store.update_todo(
            self.employee_actor,
            todo["id"],
            {"status": "done", "fault_label": "iglica", "note": "Naprawiono"},
        )

        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["fault_label"], "iglica")
        self.assertIsNotNone(updated["completed_at"])
        self.assertEqual(self.store.list_todos(self.employee_actor)[0]["note"], "Naprawiono")

    def test_employee_cannot_see_or_assign_another_users_todos(self):
        second = self.store.create_employee(
            self.manager, "second", "second-pass", "Drugi mechanik"
        )
        todo = self.store.create_todo(
            self.manager,
            {
                "owner_id": second["id"],
                "engine_id": "engine_1",
                "cylinder": 1,
                "fault_label": "kontrola",
                "severity": "nie_dotyczy",
            },
        )

        self.assertEqual(self.store.list_todos(self.employee_actor), [])
        with self.assertRaises(StoreError) as access_error:
            self.store.get_todo(self.employee_actor, todo["id"])
        self.assertEqual(access_error.exception.status_code, 403)
        with self.assertRaises(StoreError) as assignment_error:
            self.store.create_todo(
                self.employee_actor,
                {
                    "owner_id": second["id"],
                    "engine_id": "engine_2",
                    "cylinder": 2,
                    "fault_label": "pompa",
                    "severity": "male",
                },
            )
        self.assertEqual(assignment_error.exception.status_code, 403)

    def test_other_manager_has_no_access(self):
        todo = self.create_employee_todo()
        session = self.store.register("other", "other-pass", "Inny kierownik")
        other_manager = self.store.user_for_token(session["token"])

        self.assertEqual(self.store.list_todos(other_manager), [])
        with self.assertRaises(StoreError) as error:
            self.store.update_todo(other_manager, todo["id"], {"status": "done"})
        self.assertEqual(error.exception.status_code, 403)

    def test_password_is_hashed_and_session_can_be_revoked(self):
        session = self.store.login("manager", "manager-pass")
        with self.store.connect() as connection:
            stored = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (self.manager["id"],)
            ).fetchone()["password_hash"]

        self.assertNotIn("manager-pass", stored)
        self.store.logout(session["token"])
        with self.assertRaises(StoreError) as error:
            self.store.user_for_token(session["token"])
        self.assertEqual(error.exception.status_code, 401)

    def test_manager_changes_employee_password_and_revokes_old_session(self):
        changed = self.store.change_employee_password(
            self.manager, self.employee["id"], "new-employee-pass"
        )

        self.assertEqual(changed["username"], "employee")
        with self.assertRaises(StoreError) as expired:
            self.store.user_for_token(self.employee_token)
        self.assertEqual(expired.exception.status_code, 401)
        with self.assertRaises(StoreError):
            self.store.login("employee", "employee-pass")
        new_session = self.store.login("employee", "new-employee-pass")
        self.assertEqual(new_session["user"]["id"], self.employee["id"])

    def test_employee_cannot_change_another_employee_password(self):
        with self.assertRaises(StoreError) as forbidden:
            self.store.change_employee_password(
                self.employee_actor, self.employee["id"], "not-allowed-pass"
            )
        self.assertEqual(forbidden.exception.status_code, 403)

    def test_manager_cannot_change_password_outside_own_team(self):
        session = self.store.register("other", "other-pass", "Inny kierownik")
        other_manager = self.store.user_for_token(session["token"])
        outsider = self.store.create_employee(
            other_manager, "outsider", "outsider-pass", "Obcy pracownik"
        )

        with self.assertRaises(StoreError) as missing:
            self.store.change_employee_password(
                self.manager, outsider["id"], "not-allowed-pass"
            )
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
