from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import database
from backend.app.adapters import AdapterSnapshot
from backend.app.main import app
from backend.app.models import JobCreate, MaterialCreate, PrinterCreate


class PatchNullValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_directory = database.DATA_DIRECTORY
        self.original_database_path = database.DATABASE_PATH
        self.original_files_directory = database.FILES_DIRECTORY
        database.DATA_DIRECTORY = Path(self.temporary_directory.name)
        database.DATABASE_PATH = database.DATA_DIRECTORY / "fleet.db"
        database.FILES_DIRECTORY = database.DATA_DIRECTORY / "files"
        database.initialize()
        self.token_environment = patch.dict(os.environ, {"MIRDAIN_API_TOKEN": ""})
        self.token_environment.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.token_environment.stop()
        database.DATA_DIRECTORY = self.original_data_directory
        database.DATABASE_PATH = self.original_database_path
        database.FILES_DIRECTORY = self.original_files_directory
        self.temporary_directory.cleanup()

    def test_explicit_nulls_for_required_patch_fields_return_422(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Forge",
                manufacturer="Example",
                model="One",
                supported_materials=["PLA"],
            )
        )
        job = database.create_job(
            JobCreate(
                name="Bracket",
                filename="bracket.gcode",
                target_printer_id=printer.id,
            )
        )
        material = database.create_material(
            MaterialCreate(name="Black PLA", material_type="PLA")
        )

        cases = (
            (f"/api/printers/{printer.id}", "name"),
            (f"/api/printers/{printer.id}", "supported_materials"),
            (f"/api/jobs/{job.id}", "name"),
            (f"/api/jobs/{job.id}", "state"),
            (f"/api/jobs/{job.id}", "filename"),
            (f"/api/materials/{material.id}", "name"),
        )
        for endpoint, field in cases:
            with self.subTest(endpoint=endpoint, field=field):
                response = self.client.patch(endpoint, json={field: None})
                self.assertEqual(response.status_code, 422, response.text)

    def test_explicit_null_can_clear_a_nullable_assignment(self) -> None:
        printer = database.create_printer(
            PrinterCreate(name="Forge", manufacturer="Example", model="One")
        )
        job = database.create_job(
            JobCreate(
                name="Bracket",
                filename="bracket.gcode",
                target_printer_id=printer.id,
            )
        )

        response = self.client.patch(
            f"/api/jobs/{job.id}",
            json={"target_printer_id": None},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["target_printer_id"])
        self.assertIsNone(database.get_job(job.id).target_printer_id)

    def test_generic_patch_cannot_bypass_connection_or_telemetry_endpoints(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Connected",
                manufacturer="Example",
                model="One",
                adapter_type="moonraker",
            )
        )
        database.configure_connection(
            printer.id,
            adapter_type="moonraker",
            host="192.168.1.50",
            credential_id=None,
            capabilities=["telemetry", "file_transfer", "job_control"],
            snapshot=AdapterSnapshot(
                state="ready",
                percent_complete=None,
                active_job=None,
                bed_temp=25,
                nozzle_temp=30,
                note=None,
                seen_at=datetime.now(UTC),
            ),
            connection_endpoint="http://192.168.1.50:80",
        )

        for payload in (
            {"host": "192.168.1.51"},
            {"adapter_type": "octoprint"},
            {"state": "printing"},
            {"percent_complete": 50},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    f"/api/printers/{printer.id}",
                    json=payload,
                )
                self.assertEqual(response.status_code, 409, response.text)

        saved = database.get_printer(printer.id)
        self.assertEqual(saved.host, "192.168.1.50")
        self.assertEqual(saved.adapter_type, "moonraker")
