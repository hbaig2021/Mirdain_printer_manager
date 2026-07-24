from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import database
from backend.app.main import app


class RequestBoundaryTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_directory = database.DATA_DIRECTORY
        self.original_database_path = database.DATABASE_PATH
        self.original_files_directory = database.FILES_DIRECTORY
        database.DATA_DIRECTORY = Path(self.temporary_directory.name)
        database.DATABASE_PATH = database.DATA_DIRECTORY / "fleet.db"
        database.FILES_DIRECTORY = database.DATA_DIRECTORY / "files"
        database.initialize()
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def tearDown(self) -> None:
        self.client.close()
        database.DATA_DIRECTORY = self.original_data_directory
        database.DATABASE_PATH = self.original_database_path
        database.FILES_DIRECTORY = self.original_files_directory
        self.temporary_directory.cleanup()

    def test_foreign_host_is_rejected_before_api_data_is_returned(self) -> None:
        response = self.client.get(
            "/api/dashboard",
            headers={"Host": "attacker.example:8765"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not allowed", response.json()["detail"])

    def test_exact_configured_lan_host_is_allowed(self) -> None:
        with patch.dict(
            os.environ,
            {"MIRDAIN_ALLOWED_HOSTS": "farm-hub.local 192.168.10.20"},
        ):
            response = self.client.get(
                "/api/health",
                headers={"Host": "farm-hub.local:8080"},
            )

        self.assertEqual(response.status_code, 200)

    def test_wildcard_host_configuration_is_ignored(self) -> None:
        with patch.dict(os.environ, {"MIRDAIN_ALLOWED_HOSTS": "*"}):
            response = self.client.get(
                "/api/health",
                headers={"Host": "attacker.example"},
            )

        self.assertEqual(response.status_code, 400)

    def test_cross_origin_browser_control_request_is_rejected(self) -> None:
        response = self.client.post(
            "/api/printers/missing/refresh",
            headers={
                "Host": "127.0.0.1:8765",
                "Origin": "https://attacker.example",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Cross-origin control requests are blocked.",
        )

    def test_same_host_on_a_different_origin_port_is_rejected(self) -> None:
        response = self.client.post(
            "/api/printers/missing/refresh",
            headers={
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:9999",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_same_origin_proxy_control_request_reaches_the_route(self) -> None:
        response = self.client.post(
            "/api/printers/missing/refresh",
            headers={
                "Host": "127.0.0.1:15190",
                "Origin": "http://127.0.0.1:15190",
            },
        )

        self.assertEqual(response.status_code, 404)
