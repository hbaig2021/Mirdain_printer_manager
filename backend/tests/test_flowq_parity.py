from __future__ import annotations

import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import database
from backend.app.adapters import AdapterCapabilities, AdapterSnapshot
from backend.app.main import app, dispatch_verified_auto_start, route_candidates
from backend.app.models import (
    FileCreate,
    JobCreate,
    JobUpdate,
    MaterialCreate,
    PrinterCreate,
    PrinterUpdate,
)


class TemporaryDatabaseTest(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.token_environment.stop()
        database.DATA_DIRECTORY = self.original_data_directory
        database.DATABASE_PATH = self.original_database_path
        database.FILES_DIRECTORY = self.original_files_directory
        self.temporary_directory.cleanup()

    def ready_printer(self, name: str, **values: object):
        printer = database.create_printer(
            PrinterCreate(
                name=name,
                manufacturer=str(values.pop("manufacturer", "Example")),
                model=str(values.pop("model", "Model One")),
                adapter_type="simulator",
                **values,
            )
        )
        snapshot = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=None,
            nozzle_temp=None,
            note=None,
            seen_at=datetime.now(UTC),
        )
        database.configure_connection(
            printer.id,
            adapter_type="simulator",
            host=f"{printer.id}.simulator",
            credential_id=None,
            capabilities=["telemetry", "file_transfer", "job_control"],
            snapshot=snapshot,
        )
        ready = database.mark_printer_ready(printer.id)
        if printer.auto_start_enabled:
            ready = database.update_printer(
                printer.id,
                PrinterUpdate(auto_start_enabled=True),
            )
        return ready


class FlowQParityDatabaseTests(TemporaryDatabaseTest):
    def test_initialize_migrates_a_legacy_printer_database_in_place(self) -> None:
        legacy_path = database.DATA_DIRECTORY / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE printers (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              manufacturer TEXT NOT NULL,
              model TEXT NOT NULL,
              adapter_type TEXT NOT NULL,
              host TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL,
              percent_complete INTEGER,
              active_job TEXT,
              last_seen_at TEXT,
              note TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO printers (
              id, name, manufacturer, model, adapter_type, host, state, note
            ) VALUES (
              'legacy-printer', 'Legacy', 'Example', 'One', 'manual', '', 'setup', ''
            );
            """
        )
        connection.commit()
        connection.close()

        with patch.object(database, "DATABASE_PATH", legacy_path):
            database.initialize()
            migrated = database.get_printer("legacy-printer")

        self.assertEqual(migrated.nozzle_diameter, 0.4)
        self.assertEqual(migrated.tags, [])
        self.assertEqual(migrated.filament_system, "single")
        self.assertEqual(migrated.loaded_filaments, [])
        self.assertEqual(migrated.clearance_state, "unknown")

    def test_new_compatibility_fields_round_trip_as_canonical_json(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Tagged",
                manufacturer="Prusa",
                model="MK4S",
                nozzle_diameter=0.6,
                tags=["Enclosed", "Production"],
                filament_system="multi",
                loaded_filaments=["PETG Black", "PLA White"],
            )
        )
        file = database.create_file(
            FileCreate(
                name="Bracket",
                filename="bracket.gcode",
                printer_manufacturer="Prusa",
                printer_model="MK4S",
                nozzle_diameter=0.6,
                filament_profiles=["PETG Black", "PLA White"],
            )
        )
        job = database.create_job(
            JobCreate(
                name="Bracket",
                filename="bracket.gcode",
                file_id=file.id,
                required_printer_manufacturer="Prusa",
                required_printer_model="MK4S",
                required_nozzle_diameter=0.6,
                required_filaments=["PETG Black", "PLA White"],
                required_tags=["Production"],
                tag_match_mode="all",
                allowed_printer_ids=[printer.id],
                auto_switch_filament=True,
                override_existing_file=True,
            )
        )

        saved_printer = database.get_printer(printer.id)
        saved_file = database.get_file(file.id)
        saved_job = database.get_job(job.id)

        self.assertEqual(saved_printer.tags, ["Enclosed", "Production"])
        self.assertEqual(saved_printer.loaded_filaments, ["PETG Black", "PLA White"])
        self.assertEqual(saved_file.printer_manufacturer, "Prusa")
        self.assertEqual(saved_file.filament_profiles, ["PETG Black", "PLA White"])
        self.assertEqual(saved_job.required_printer_manufacturer, "Prusa")
        self.assertEqual(saved_job.allowed_printer_ids, [printer.id])
        self.assertTrue(saved_job.auto_switch_filament)
        self.assertTrue(saved_job.override_existing_file)
        with database.connection() as connection:
            row = connection.execute("SELECT tags, loaded_filaments FROM printers WHERE id = ?", (printer.id,)).fetchone()
        self.assertEqual(json.loads(row["tags"]), ["Enclosed", "Production"])
        self.assertEqual(json.loads(row["loaded_filaments"]), ["PETG Black", "PLA White"])

    def test_plain_list_values_from_older_databases_are_decoded(self) -> None:
        printer = database.create_printer(
            PrinterCreate(name="Legacy", manufacturer="Example", model="One")
        )
        with database.connection() as connection:
            connection.execute(
                "UPDATE printers SET tags = ?, loaded_filaments = ? WHERE id = ?",
                ("fast, enclosed", "PLA Red, PETG Black", printer.id),
            )

        saved = database.get_printer(printer.id)

        self.assertEqual(saved.tags, ["fast", "enclosed"])
        self.assertEqual(saved.loaded_filaments, ["PLA Red", "PETG Black"])

    def test_routing_enforces_make_model_nozzle_tags_and_allowed_ids(self) -> None:
        matching = self.ready_printer(
            "Matching",
            manufacturer="PrUsA",
            model="MK4S",
            nozzle_diameter=0.6,
            tags=["Enclosed", "Fast"],
            material="PETG Black",
            supported_materials=["PETG"],
        )
        self.ready_printer(
            "Wrong make",
            manufacturer="Bambu Lab",
            model="MK4S",
            nozzle_diameter=0.6,
            tags=["Enclosed", "Fast"],
            material="PETG Black",
            supported_materials=["PETG"],
        )
        job = database.create_job(
            JobCreate(
                name="Fixture",
                filename="fixture.gcode",
                material="PETG Black",
                required_printer_manufacturer="prusa",
                required_printer_model="mk4s",
                required_nozzle_diameter=0.6,
                required_tags=["enclosed"],
                allowed_printer_ids=[matching.id],
            )
        )

        candidates = route_candidates(job)

        self.assertEqual([candidate.printer_id for candidate in candidates], [matching.id])
        self.assertIn("manufacturer prusa", candidates[0].reason)
        self.assertIn("0.6 mm nozzle", candidates[0].reason)

    def test_tag_match_modes_are_all_any_and_exact_set_semantics(self) -> None:
        printer = self.ready_printer("Tagged", tags=["Enclosed", "Fast"])
        job = database.create_job(
            JobCreate(name="Tagged task", filename="tagged.gcode", required_tags=["enclosed"])
        )
        self.assertEqual([item.printer_id for item in route_candidates(job)], [printer.id])

        any_job = database.update_job(
            job.id,
            JobUpdate(required_tags=["other", "FAST"], tag_match_mode="any"),
        )
        self.assertEqual([item.printer_id for item in route_candidates(any_job)], [printer.id])

        exact_job = database.update_job(
            job.id,
            JobUpdate(required_tags=["enclosed"], tag_match_mode="exactly"),
        )
        self.assertEqual(route_candidates(exact_job), [])

    def test_multislot_profiles_require_exact_loaded_order(self) -> None:
        single = self.ready_printer(
            "Single",
            filament_system="single",
            loaded_filaments=["PLA Red", "PETG Black"],
        )
        ams = self.ready_printer(
            "AMS",
            filament_system="ams",
            loaded_filaments=["PLA Red", "PETG Black"],
        )
        job = database.create_job(
            JobCreate(
                name="Two color",
                filename="two-color.3mf",
                required_filaments=["PETG Black", "PLA Red"],
                auto_switch_filament=True,
                allowed_printer_ids=[ams.id],
            )
        )

        self.assertEqual(route_candidates(job), [])
        ordered = database.update_job(
            job.id,
            JobUpdate(
                auto_switch_filament=False,
                required_filaments=["PLA Red", "PETG Black"],
            ),
        )
        self.assertEqual(
            [item.printer_id for item in route_candidates(ordered)],
            [ams.id],
        )
        self.assertNotEqual(single.id, ams.id)

    def test_file_metadata_supplies_unspecified_job_requirements(self) -> None:
        matching = self.ready_printer(
            "File match",
            manufacturer="Prusa",
            model="XL",
            nozzle_diameter=0.4,
            loaded_filaments=["PLA Generic"],
        )
        file = database.create_file(
            FileCreate(
                name="Plate",
                filename="plate.bgcode",
                printer_manufacturer="prusa",
                printer_model="xl",
                nozzle_diameter=0.4,
                filament_profiles=["PLA Generic"],
            )
        )
        job = database.create_job(JobCreate(name="Plate", filename="plate.bgcode", file_id=file.id))

        self.assertEqual([item.printer_id for item in route_candidates(job)], [matching.id])

    def test_auto_dispatch_scans_top_to_bottom_and_skips_incompatible_tasks(self) -> None:
        printer = self.ready_printer(
            "Auto",
            model="Eligible",
            auto_start_enabled=True,
            loaded_filaments=["PLA"],
        )
        file = database.store_upload(
            FileCreate(
                name="Safe",
                filename="safe.gcode",
                auto_eject_verified=True,
                filament_profiles=["PLA"],
            ),
            "safe.gcode",
            io.BytesIO(b"G28\n"),
        )
        first = database.create_job(
            JobCreate(
                name="First incompatible",
                filename="safe.gcode",
                file_id=file.id,
                auto_restart=True,
                required_printer_model="Different",
            )
        )
        second = database.create_job(
            JobCreate(
                name="Second eligible",
                filename="safe.gcode",
                file_id=file.id,
                auto_restart=True,
                required_printer_model="Eligible",
                override_existing_file=True,
                target_printer_id=printer.id,
            )
        )
        database.mark_job_delivered(second.id, "already-there.gcode")

        class FakeAdapter:
            capabilities = AdapterCapabilities(telemetry=True, file_transfer=True, job_control=True, camera=False)

            def __init__(self) -> None:
                self.uploads = 0

            def upload(self, *_: object) -> str:
                self.uploads += 1
                return "safe.gcode"

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return AdapterSnapshot(
                    state="ready",
                    percent_complete=None,
                    active_job=None,
                    bed_temp=25,
                    nozzle_temp=30,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(self, *_: object) -> None:
                return None

        adapter = FakeAdapter()
        with patch("backend.app.main.get_adapter", return_value=adapter), patch(
            "backend.app.main.load_secret", return_value={}
        ):
            started = dispatch_verified_auto_start(printer.id)

        self.assertEqual(started.parent_job_id, second.id)
        self.assertNotEqual(started.id, second.id)
        self.assertEqual(database.get_job(first.id).state, "queued")
        self.assertEqual(database.get_job(first.id).queue_position, 1)
        self.assertEqual(adapter.uploads, 1)

    def test_completion_requires_manual_ready_unless_auto_clear_is_trusted_and_verified(self) -> None:
        printer = database.create_printer(
            PrinterCreate(name="Clearance", manufacturer="Example", model="One")
        )
        manual_file = database.create_file(FileCreate(name="Manual", filename="manual.gcode"))
        manual_job = database.create_job(
            JobCreate(
                name="Manual",
                filename="manual.gcode",
                file_id=manual_file.id,
                target_printer_id=printer.id,
            )
        )
        database.update_job(manual_job.id, JobUpdate(state="running"))

        database.complete_job(manual_job.id)

        after_manual = database.get_printer(printer.id)
        self.assertEqual(after_manual.state, "attention")
        self.assertIsNone(after_manual.active_job_id)
        self.assertEqual(database.mark_printer_ready(printer.id).state, "ready")

        safe_file = database.create_file(
            FileCreate(name="Auto clear", filename="auto.gcode", auto_eject_verified=True)
        )
        safe_job = database.create_job(
            JobCreate(
                name="Auto clear",
                filename="auto.gcode",
                file_id=safe_file.id,
                target_printer_id=printer.id,
                auto_clear=True,
            )
        )
        database.update_job(safe_job.id, JobUpdate(state="running"))

        database.complete_job(safe_job.id, trusted_auto_clear=True)

        self.assertEqual(database.get_printer(printer.id).state, "ready")

    def test_cancelled_and_failed_jobs_leave_the_printer_needing_review(self) -> None:
        for terminal_state in ("cancelled", "failed"):
            with self.subTest(state=terminal_state):
                printer = database.create_printer(
                    PrinterCreate(name=f"Printer {terminal_state}", manufacturer="Example", model="One")
                )
                job = database.create_job(
                    JobCreate(
                        name=f"Job {terminal_state}",
                        filename="part.gcode",
                        target_printer_id=printer.id,
                    )
                )
                database.update_job(job.id, JobUpdate(state="running"))

                database.update_job(job.id, JobUpdate(state=terminal_state))

                saved = database.get_printer(printer.id)
                self.assertEqual(saved.state, "attention")
                self.assertIsNone(saved.active_job_id)


class PublicApiTests(TemporaryDatabaseTest):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app)

    def test_public_aliases_and_confirmed_ready_contract(self) -> None:
        printer = self.ready_printer("Idle")
        database.create_material(MaterialCreate(name="PLA", material_type="PLA"))
        file = database.create_file(FileCreate(name="Cube", filename="cube.gcode"))
        database.create_job(JobCreate(name="Cube", filename="cube.gcode", file_id=file.id))

        self.assertEqual(self.client.get("/api/public/v1/ping").json()["api_version"], "v1")
        self.assertEqual(len(self.client.get("/api/public/v1/inventory/filament").json()), 1)
        self.assertEqual(len(self.client.get("/api/public/v1/printer/idle").json()), 1)
        self.assertEqual(self.client.get("/api/public/v1/printer/active").json(), [])
        self.assertEqual(len(self.client.get("/api/public/v1/queue/files").json()), 1)
        self.assertEqual(len(self.client.get("/api/public/v1/queue/tasks").json()), 1)

        enqueued = self.client.post(
            "/api/public/v1/queue/enqueue",
            json={
                "name": "API task",
                "filename": "api.gcode",
                "required_printer_manufacturer": "Example",
                "required_printer_model": "Model One",
                "allowed_printer_ids": [printer.id],
            },
        )
        self.assertEqual(enqueued.status_code, 201)
        self.assertEqual(enqueued.json()["required_printer_manufacturer"], "Example")

        database.update_printer(printer.id, PrinterUpdate(state="attention"))
        invalid = self.client.post(
            "/api/public/v1/printer/ready",
            json={"printer_id": printer.id, "confirmation": "yes"},
        )
        ready = self.client.post(
            "/api/public/v1/printer/ready",
            json={"printer_id": printer.id, "confirmation": "READY"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["state"], "ready")

    def test_optional_token_protects_only_the_public_api(self) -> None:
        with patch.dict(os.environ, {"MIRDAIN_API_TOKEN": "test-token"}):
            self.assertEqual(self.client.get("/api/public/v1/ping").status_code, 401)
            self.assertEqual(
                self.client.get(
                    "/api/public/v1/ping",
                    headers={"Authorization": "Bearer test-token"},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    "/api/public/v1/ping",
                    headers={"X-API-Key": "test-token"},
                ).status_code,
                200,
            )
            self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_prusalink_connection_saves_digest_credentials_without_echoing_them(self) -> None:
        printer = database.create_printer(
            PrinterCreate(name="Prusa", manufacturer="Prusa", model="MK4S")
        )
        snapshot = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )

        class FakePrusaLinkAdapter:
            capabilities = AdapterCapabilities(telemetry=True, file_transfer=True, job_control=True, camera=False)
            label = "PrusaLink"

            def snapshot(self, _host: str, credentials: dict[str, str]) -> AdapterSnapshot:
                self.credentials = credentials
                return snapshot

        adapter = FakePrusaLinkAdapter()
        with patch("backend.app.main.get_adapter", return_value=adapter), patch(
            "backend.app.main.save_secret"
        ) as save_secret, patch("backend.app.main.load_secret") as load_secret:
            response = self.client.post(
                f"/api/printers/{printer.id}/connection",
                json={
                    "adapter_type": "prusalink",
                    "host": "192.168.1.43",
                    "username": "maker",
                    "password": "local-secret",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(adapter.credentials, {"username": "maker", "password": "local-secret"})
        save_secret.assert_called_once()
        saved_credential_id, saved_payload = save_secret.call_args.args
        self.assertTrue(saved_credential_id.startswith(f"printer:{printer.id}:"))
        self.assertEqual(
            saved_payload,
            {"username": "maker", "password": "local-secret"},
        )
        self.assertEqual(
            database.credential_id_for_printer(printer.id),
            saved_credential_id,
        )
        load_secret.assert_not_called()
        self.assertNotIn("local-secret", response.text)

    def test_connection_rejects_a_duplicate_physical_endpoint(self) -> None:
        first = database.create_printer(
            PrinterCreate(name="First", manufacturer="Example", model="One")
        )
        second = database.create_printer(
            PrinterCreate(name="Second", manufacturer="Example", model="Two")
        )
        snapshot = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )
            label = "Moonraker"

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return snapshot

        payload = {
            "adapter_type": "moonraker",
            "host": "http://192.168.1.40/",
            "api_key": "",
        }
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.adapters.socket.getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("192.168.1.40", 0),
                )
            ],
        ):
            connected = self.client.post(
                f"/api/printers/{first.id}/connection",
                json=payload,
            )
            duplicate = self.client.post(
                f"/api/printers/{second.id}/connection",
                json={**payload, "host": "octoprint.local"},
            )

        self.assertEqual(connected.status_code, 200, connected.text)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertIn("already connected", duplicate.text)

    def test_connection_rejects_ambiguous_base_paths_before_snapshot(self) -> None:
        snapshot = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        snapshot_hosts: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )
            label = "Moonraker"

            def snapshot(
                self,
                host: str,
                _credentials: dict[str, str],
            ) -> AdapterSnapshot:
                snapshot_hosts.append(host)
                return snapshot

        ambiguous_hosts = (
            "http://192.168.1.81/.",
            "http://192.168.1.82/%2e",
            "http://192.168.1.83//",
            "http://192.168.1.84/foo/../bar",
        )
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()):
            for index, host in enumerate(ambiguous_hosts):
                with self.subTest(host=host):
                    printer = database.create_printer(
                        PrinterCreate(
                            name=f"Ambiguous path {index}",
                            manufacturer="Example",
                            model="One",
                        )
                    )
                    snapshots_before = len(snapshot_hosts)
                    rejected = self.client.post(
                        f"/api/printers/{printer.id}/connection",
                        json={
                            "adapter_type": "moonraker",
                            "host": host,
                            "api_key": "",
                        },
                    )
                    self.assertEqual(rejected.status_code, 422, rejected.text)
                    self.assertEqual(len(snapshot_hosts), snapshots_before)

            canonical = database.create_printer(
                PrinterCreate(
                    name="Canonical slash",
                    manufacturer="Example",
                    model="One",
                )
            )
            accepted = self.client.post(
                f"/api/printers/{canonical.id}/connection",
                json={
                    "adapter_type": "moonraker",
                    "host": "http://192.168.1.85/",
                    "api_key": "",
                },
            )

        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(snapshot_hosts, ["http://192.168.1.85/"])

    def test_automation_requires_confirmed_arm_and_is_disarmed_fail_closed(
        self,
    ) -> None:
        confirmation = {"confirmation": "ARM STATIC ENDPOINT"}
        ready = self.ready_printer("Armable simulator")

        generic_patch = self.client.patch(
            f"/api/printers/{ready.id}",
            json={"auto_start_enabled": True},
        )
        invalid_confirmation = self.client.post(
            f"/api/printers/{ready.id}/automation/arm",
            json={"confirmation": "ARM"},
        )
        self.assertEqual(generic_patch.status_code, 409, generic_patch.text)
        self.assertEqual(invalid_confirmation.status_code, 422)
        self.assertFalse(database.get_printer(ready.id).auto_start_enabled)

        unsupported = database.create_printer(
            PrinterCreate(
                name="Manual record",
                manufacturer="Example",
                model="One",
                adapter_type="manual",
            )
        )
        unconnected = database.create_printer(
            PrinterCreate(
                name="Unconnected simulator",
                manufacturer="Example",
                model="One",
                adapter_type="simulator",
            )
        )
        not_ready = database.create_printer(
            PrinterCreate(
                name="Unconfirmed simulator",
                manufacturer="Example",
                model="One",
                adapter_type="simulator",
            )
        )
        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        database.configure_connection(
            not_ready.id,
            adapter_type="simulator",
            host="unconfirmed.simulator",
            credential_id=None,
            capabilities=["telemetry", "file_transfer", "job_control"],
            snapshot=idle,
        )

        unsupported_response = self.client.post(
            f"/api/printers/{unsupported.id}/automation/arm",
            json=confirmation,
        )
        unconnected_response = self.client.post(
            f"/api/printers/{unconnected.id}/automation/arm",
            json=confirmation,
        )
        not_ready_response = self.client.post(
            f"/api/printers/{not_ready.id}/automation/arm",
            json=confirmation,
        )
        armed = self.client.post(
            f"/api/printers/{ready.id}/automation/arm",
            json=confirmation,
        )

        self.assertEqual(
            unsupported_response.status_code,
            422,
            unsupported_response.text,
        )
        self.assertEqual(
            unconnected_response.status_code,
            409,
            unconnected_response.text,
        )
        self.assertEqual(
            not_ready_response.status_code,
            409,
            not_ready_response.text,
        )
        self.assertEqual(armed.status_code, 200, armed.text)
        self.assertTrue(armed.json()["auto_start_enabled"])

        database.invalidate_unattended_clearance_on_startup()
        self.assertFalse(database.get_printer(ready.id).auto_start_enabled)

        error_printer = self.ready_printer("Connection error disarm")
        error_armed = self.client.post(
            f"/api/printers/{error_printer.id}/automation/arm",
            json=confirmation,
        )
        self.assertEqual(error_armed.status_code, 200, error_armed.text)
        self.assertTrue(database.get_printer(error_printer.id).auto_start_enabled)
        database.mark_connection_error(error_printer.id, "Offline.")
        self.assertFalse(
            database.get_printer(error_printer.id).auto_start_enabled
        )

        endpoint_printer = self.ready_printer("Endpoint change disarm")
        endpoint_armed = self.client.post(
            f"/api/printers/{endpoint_printer.id}/automation/arm",
            json=confirmation,
        )
        self.assertEqual(endpoint_armed.status_code, 200, endpoint_armed.text)
        self.assertTrue(
            database.get_printer(endpoint_printer.id).auto_start_enabled
        )

        class FakeSimulator:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )
            label = "Simulator"

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return idle

        with patch(
            "backend.app.main.get_adapter",
            return_value=FakeSimulator(),
        ):
            changed_endpoint = self.client.post(
                f"/api/printers/{endpoint_printer.id}/connection",
                json={
                    "adapter_type": "simulator",
                    "host": "replacement.simulator",
                    "api_key": "",
                },
            )

        self.assertEqual(
            changed_endpoint.status_code,
            200,
            changed_endpoint.text,
        )
        changed = database.get_printer(endpoint_printer.id)
        self.assertFalse(changed.auto_start_enabled)
        self.assertEqual(changed.state, "attention")
        self.assertEqual(changed.clearance_state, "unknown")

    def test_dns_drift_invalidates_the_original_and_rejects_a_duplicate(self) -> None:
        original = database.create_printer(
            PrinterCreate(name="Alias", manufacturer="Example", model="One")
        )
        duplicate = database.create_printer(
            PrinterCreate(name="Drift target", manufacturer="Example", model="Two")
        )
        snapshot = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        resolved_address = {"printer.local": "192.168.1.70"}

        def resolve(
            hostname: str,
            *_: object,
            **__: object,
        ) -> list[tuple[object, ...]]:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (resolved_address[hostname], 0),
                )
            ]

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )
            label = "Moonraker"

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return snapshot

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.adapters.socket.getaddrinfo",
            side_effect=resolve,
        ):
            connected = self.client.post(
                f"/api/printers/{original.id}/connection",
                json={
                    "adapter_type": "moonraker",
                    "host": "printer.local",
                    "api_key": "",
                },
            )
            self.assertEqual(connected.status_code, 200, connected.text)

            resolved_address["printer.local"] = "192.168.1.71"
            rejected = self.client.post(
                f"/api/printers/{duplicate.id}/connection",
                json={
                    "adapter_type": "moonraker",
                    "host": "192.168.1.71",
                    "api_key": "",
                },
            )

        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("already connected", rejected.text)
        invalidated = database.get_printer(original.id)
        self.assertEqual(invalidated.connection_state, "unconfigured")
        self.assertEqual(invalidated.state, "attention")
        self.assertEqual(invalidated.clearance_state, "unknown")
        self.assertEqual(invalidated.capabilities, [])

    def test_auto_restart_scope_rejects_unsupported_unattended_printers(
        self,
    ) -> None:
        source = database.store_upload(
            FileCreate(
                name="Scope guard",
                filename="scope-guard.gcode",
                auto_eject_verified=True,
            ),
            "scope-guard.gcode",
            io.BytesIO(b"G28\n"),
        )
        unsupported = {
            adapter_type: database.create_printer(
                PrinterCreate(
                    name=f"{adapter_type} record",
                    manufacturer="Example",
                    model="Model One",
                    adapter_type=adapter_type,
                )
            )
            for adapter_type in ("octoprint", "manual", "bambu_lan")
        }
        simulator = database.create_printer(
            PrinterCreate(
                name="Simulator",
                manufacturer="Example",
                model="Model One",
                adapter_type="simulator",
            )
        )
        base_payload = {
            "name": "Scoped restart",
            "filename": source.filename,
            "file_id": source.id,
            "auto_restart": True,
        }

        for adapter_type, printer in unsupported.items():
            with self.subTest(adapter_type=adapter_type):
                rejected = self.client.post(
                    "/api/jobs",
                    json={
                        **base_payload,
                        "target_printer_id": printer.id,
                    },
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertIn("Automatic restart", rejected.text)

        unsupported_list = self.client.post(
            "/api/jobs",
            json={
                **base_payload,
                "allowed_printer_ids": [
                    printer.id for printer in unsupported.values()
                ],
            },
        )
        supported = self.client.post(
            "/api/jobs",
            json={
                **base_payload,
                "target_printer_id": simulator.id,
            },
        )

        self.assertEqual(unsupported_list.status_code, 422, unsupported_list.text)
        self.assertEqual(supported.status_code, 201, supported.text)
        self.assertEqual(supported.json()["target_printer_id"], simulator.id)

    def test_new_physical_endpoint_requires_fresh_ready_confirmation(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Retarget",
                manufacturer="Example",
                model="One",
                adapter_type="moonraker",
            )
        )
        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        database.configure_connection(
            printer.id,
            adapter_type="moonraker",
            host="192.168.1.41",
            credential_id=None,
            capabilities=["telemetry", "file_transfer", "job_control"],
            snapshot=idle,
            connection_endpoint="http://192.168.1.41:80",
        )
        database.mark_printer_ready(printer.id)

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )
            label = "Moonraker"

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return idle

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()):
            response = self.client.post(
                f"/api/printers/{printer.id}/connection",
                json={
                    "adapter_type": "moonraker",
                    "host": "192.168.1.42",
                    "api_key": "",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["printer"]["state"], "attention")
        self.assertEqual(
            response.json()["printer"]["clearance_state"],
            "unknown",
        )

    def test_legacy_endpoint_without_resolved_keys_fails_closed_until_reconnected(self) -> None:
        legacy = database.create_printer(
            PrinterCreate(
                name="Legacy alias",
                manufacturer="Example",
                model="One",
                adapter_type="octoprint",
                host="octoprint.local",
            )
        )
        second = database.create_printer(
            PrinterCreate(name="IP record", manufacturer="Example", model="Two")
        )
        with database.connection() as connection:
            connection.execute(
                """UPDATE printers SET connection_state = 'connected',
                  state = 'ready', clearance_state = 'confirmed',
                  connection_endpoint = 'http://octoprint.local:80',
                  connection_endpoint_keys = '[]'
                  WHERE id = ?""",
                (legacy.id,),
            )

        database.initialize()
        gated = database.get_printer(legacy.id)
        self.assertEqual(gated.connection_state, "unconfigured")
        self.assertEqual(gated.clearance_state, "unknown")

        snapshot = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )
            label = "OctoPrint"

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return snapshot

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.adapters.socket.getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("192.168.1.60", 0),
                )
            ],
        ):
            reconnected = self.client.post(
                f"/api/printers/{legacy.id}/connection",
                json={
                    "adapter_type": "octoprint",
                    "host": "octoprint.local",
                    "api_key": "",
                },
            )
            duplicate = self.client.post(
                f"/api/printers/{second.id}/connection",
                json={
                    "adapter_type": "octoprint",
                    "host": "192.168.1.60",
                    "api_key": "",
                },
            )

        self.assertEqual(reconnected.status_code, 200, reconnected.text)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
