from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from backend.app import database
from backend.app.adapters import AdapterSnapshot
from backend.app.models import JobCreate, PrinterCreate


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_directory = database.DATA_DIRECTORY
        self.original_database_path = database.DATABASE_PATH
        database.DATA_DIRECTORY = Path(self.temporary_directory.name)
        database.DATABASE_PATH = database.DATA_DIRECTORY / "fleet.db"
        database.initialize()

    def tearDown(self) -> None:
        database.DATA_DIRECTORY = self.original_data_directory
        database.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_printer_and_job_are_persisted_to_the_local_queue(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Forge North",
                manufacturer="Bambu Lab",
                model="P1S",
                adapter_type="bambu_lan",
                host="192.168.1.88",
            )
        )
        job = database.create_job(
            JobCreate(
                name="Fixture arm",
                filename="fixture-arm.3mf",
                target_printer_id=printer.id,
                material="PETG · Orange",
                estimated_minutes=90,
            )
        )

        dashboard = database.dashboard()

        self.assertEqual([item.id for item in dashboard.printers], [printer.id])
        self.assertEqual([item.id for item in dashboard.jobs], [job.id])
        self.assertEqual(dashboard.jobs[0].queue_position, 1)
        self.assertEqual(len(dashboard.activity), 2)

    def test_connection_snapshot_is_saved_without_storing_a_secret(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Forge West",
                manufacturer="Prusa",
                model="MK4",
                adapter_type="prusalink",
                host="printer.local",
            )
        )
        snapshot = AdapterSnapshot(
            state="printing",
            percent_complete=24,
            active_job="enclosure.3mf",
            bed_temp=60,
            nozzle_temp=215,
            note="PRINTING",
            seen_at=datetime.now(UTC),
        )
        saved = database.configure_connection(
            printer.id,
            adapter_type="prusalink",
            host="printer.local",
            credential_id="printer:test",
            capabilities=["telemetry", "file_transfer", "job_control"],
            snapshot=snapshot,
        )
        self.assertIsNotNone(saved)
        self.assertEqual(saved.connection_state, "connected")
        self.assertEqual(saved.active_job, "enclosure.3mf")
        self.assertEqual(saved.capabilities, ["telemetry", "file_transfer", "job_control"])
        self.assertEqual(database.credential_id_for_printer(printer.id), "printer:test")
