"""Exercise HTTP failure semantics and database resource cleanup without a DB."""

import os
import unittest
from unittest.mock import MagicMock, patch

import psycopg2
from fastapi.testclient import TestClient
from pydantic import ValidationError

from neareast_api.core.config import Settings
from neareast_api.core.database import database_is_ready
from neareast_api.main import create_app


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(db_password="test-password")
        self.app = create_app(self.settings)

    def test_liveness_does_not_depend_on_database(self):
        with patch("neareast_api.core.database.psycopg2.connect") as connect:
            with TestClient(self.app) as client:
                response = client.get("/health/live")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})
            connect.assert_not_called()

    def test_readiness_executes_select_and_closes_connection(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        with patch(
            "neareast_api.core.database.psycopg2.connect", return_value=connection
        ) as connect:
            with TestClient(self.app) as client:
                response = client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
        cursor.execute.assert_called_once_with("SELECT 1")
        connection.close.assert_called_once()
        self.assertTrue(connection.autocommit)
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 3)
        self.assertEqual(connect.call_args.kwargs["options"], "-c statement_timeout=3000")

    def test_database_failure_returns_503_without_driver_details(self):
        with patch(
            "neareast_api.core.database.psycopg2.connect",
            side_effect=psycopg2.OperationalError("private-host test-password"),
        ):
            with TestClient(self.app) as client:
                response = client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"status": "error", "database": "unavailable"}
        )
        self.assertNotIn("test-password", response.text)
        self.assertNotIn("private-host", response.text)

    def test_query_failure_still_closes_connection(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = psycopg2.OperationalError("statement timeout")
        with patch("neareast_api.core.database.psycopg2.connect", return_value=connection):
            self.assertFalse(database_is_ready(self.settings))
        connection.close.assert_called_once()

    def test_missing_password_is_unready_without_connection_attempt(self):
        with patch("neareast_api.core.database.psycopg2.connect") as connect:
            with TestClient(create_app(Settings())) as client:
                response = client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        connect.assert_not_called()

    def test_unexpected_query_result_is_not_healthy(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        with patch("neareast_api.core.database.psycopg2.connect", return_value=connection):
            self.assertFalse(database_is_ready(self.settings))
        connection.close.assert_called_once()

    def test_environment_is_validated_and_password_is_redacted(self):
        with patch.dict(
            os.environ,
            {
                "NEAREAST_DB_HOST": "db",
                "NEAREAST_DB_PORT": "5432",
                "NEAREAST_DB_PASSWORD": "p@ss/word'$",
            },
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.db_host, "db")
        self.assertEqual(settings.db_port, 5432)
        self.assertEqual(settings.db_password.get_secret_value(), "p@ss/word'$")
        self.assertNotIn("p@ss/word'$", repr(settings))
        for value in ("0", "65536", "not-a-port"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"NEAREAST_DB_PORT": value}):
                    with self.assertRaises(ValidationError):
                        Settings.from_env()

    def test_openapi_documents_database_failure(self):
        with TestClient(self.app) as client:
            schema = client.get("/openapi.json").json()
        self.assertIn("503", schema["paths"]["/health/ready"]["get"]["responses"])


if __name__ == "__main__":
    unittest.main()
