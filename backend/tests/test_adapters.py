from __future__ import annotations

import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.adapters import (
    AdapterError,
    MoonrakerAdapter,
    OctoPrintAdapter,
    PrusaLinkAdapter,
    SimulatorAdapter,
)


class AdapterTests(unittest.TestCase):
    def test_octoprint_snapshot_maps_live_status(self) -> None:
        printer = {
            "state": {"text": "Operational", "flags": {"operational": True, "printing": False, "paused": False}},
            "temperature": {"bed": {"actual": 60.4}, "tool0": {"actual": 214.6}},
        }
        job = {"job": {"file": {"name": "fixture.gcode"}}, "progress": {"completion": 37.8}}
        with patch.object(OctoPrintAdapter, "_json", side_effect=[printer, job]):
            snapshot = OctoPrintAdapter().snapshot("192.168.1.20", {"api_key": "test"})
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.percent_complete, 38)
        self.assertEqual(snapshot.active_job, "fixture.gcode")
        self.assertEqual(snapshot.bed_temp, 60)
        self.assertEqual(snapshot.nozzle_temp, 215)
        self.assertIsNone(snapshot.job_outcome)

    def test_octoprint_full_progress_does_not_claim_trusted_completion(self) -> None:
        printer = {
            "state": {
                "text": "Operational",
                "flags": {
                    "operational": True,
                    "printing": False,
                    "paused": False,
                },
            },
            "temperature": {},
        }
        job = {
            "job": {"file": {"name": "finished.gcode"}},
            "progress": {"completion": 100},
        }
        with patch.object(OctoPrintAdapter, "_json", side_effect=[printer, job]):
            snapshot = OctoPrintAdapter().snapshot("192.168.1.20", {})

        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.percent_complete, 100)
        self.assertIsNone(snapshot.job_outcome)

    def test_moonraker_snapshot_maps_klipper_status(self) -> None:
        response = {
            "result": {
                "status": {
                    "webhooks": {"state": "ready", "state_message": "Printer is ready"},
                    "print_stats": {"state": "printing", "filename": "benchy.gcode", "message": ""},
                    "virtual_sdcard": {"progress": 0.42},
                    "extruder": {"temperature": 210.1},
                    "heater_bed": {"temperature": 59.8},
                }
            }
        }
        with patch.object(MoonrakerAdapter, "_json", return_value=response):
            snapshot = MoonrakerAdapter().snapshot("192.168.1.20", {})
        self.assertEqual(snapshot.state, "printing")
        self.assertEqual(snapshot.percent_complete, 42)
        self.assertEqual(snapshot.active_job, "benchy.gcode")
        self.assertEqual(snapshot.bed_temp, 60)

    def test_prusalink_snapshot_maps_status(self) -> None:
        response = {
            "printer": {"state": "PRINTING", "temp_bed": 60.4, "temp_nozzle": 215.2},
            "job": {"id": 42, "progress": 76.1, "time_printing": 526},
        }
        with patch(
            "backend.app.adapters._get_json_request",
            return_value=response,
        ) as get_json, patch(
            "backend.app.adapters._get_optional_json_request",
            return_value={
                "id": 42,
                "file": {
                    "name": "enclosure.3mf",
                    "path": "/usb/enclosure.3mf",
                },
            },
        ) as get_job:
            snapshot = PrusaLinkAdapter().snapshot(
                "192.168.1.20",
                {"username": "maker", "password": "test"},
            )
        self.assertEqual(snapshot.state, "printing")
        self.assertEqual(snapshot.percent_complete, 76)
        self.assertEqual(snapshot.active_job, "enclosure.3mf")
        self.assertEqual(snapshot.nozzle_temp, 215)
        self.assertEqual(get_json.call_args.args[0], "http://192.168.1.20/api/v1/status")
        self.assertIsNotNone(get_json.call_args.kwargs["auth"])
        self.assertEqual(
            get_job.call_args.args[0],
            "http://192.168.1.20/api/v1/job",
        )
        self.assertIsNotNone(get_job.call_args.kwargs["auth"])

    def test_prusalink_zero_progress_is_not_replaced_by_elapsed_seconds(self) -> None:
        response = {
            "printer": {"state": "PRINTING"},
            "job": {"progress": 0, "time_printing": 526},
        }
        with patch(
            "backend.app.adapters._get_json_request",
            return_value=response,
        ), patch(
            "backend.app.adapters._get_optional_json_request",
            return_value={"id": 42, "file": {"name": "cube.gcode"}},
        ):
            snapshot = PrusaLinkAdapter().snapshot(
                "192.168.1.20",
                {"username": "maker", "password": "test"},
            )

        self.assertEqual(snapshot.percent_complete, 0)

    def test_moonraker_snapshot_reports_an_explicit_completed_job(self) -> None:
        response = {
            "result": {
                "status": {
                    "webhooks": {"state": "ready"},
                    "print_stats": {"state": "complete", "filename": "finished.gcode", "message": ""},
                    "virtual_sdcard": {"progress": 1},
                    "extruder": {"temperature": 30},
                    "heater_bed": {"temperature": 25},
                }
            }
        }
        with patch.object(MoonrakerAdapter, "_json", return_value=response):
            snapshot = MoonrakerAdapter().snapshot("192.168.1.20", {})

        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.job_outcome, "completed")

    def test_moonraker_missing_filename_stays_absent(self) -> None:
        response = {
            "result": {
                "status": {
                    "webhooks": {"state": "ready"},
                    "print_stats": {"state": "standby"},
                    "virtual_sdcard": {"progress": 0},
                    "extruder": {},
                    "heater_bed": {},
                }
            }
        }
        with patch.object(MoonrakerAdapter, "_json", return_value=response):
            snapshot = MoonrakerAdapter().snapshot("192.168.1.20", {})

        self.assertIsNone(snapshot.active_job)

    def test_octoprint_upload_and_start_use_documented_local_endpoints(self) -> None:
        adapter = OctoPrintAdapter()
        with patch(
            "backend.app.adapters._upload_multipart",
            return_value={"files": {"local": {"path": "jobs/cube.gcode"}}},
        ) as upload, patch("backend.app.adapters._post_json") as post:
            path = adapter.upload("192.168.1.20", {"api_key": "test"}, Path("cube.gcode"), "cube.gcode")
            adapter.command("192.168.1.20", {"api_key": "test"}, "start", path)

        self.assertEqual(path, "jobs/cube.gcode")
        upload.assert_called_once()
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "http://192.168.1.20/api/files/local/jobs/cube.gcode")
        self.assertEqual(post.call_args.args[2], {"command": "select", "print": True})

    def test_moonraker_upload_and_controls_use_local_endpoints(self) -> None:
        adapter = MoonrakerAdapter()
        with patch(
            "backend.app.adapters._upload_multipart",
            return_value={"item": {"path": "cube.gcode"}},
        ) as upload, patch("backend.app.adapters._post_json") as post:
            path = adapter.upload("192.168.1.20", {}, Path("cube.gcode"), "cube.gcode")
            adapter.command("192.168.1.20", {}, "start", path)
            adapter.command("192.168.1.20", {}, "pause")
            adapter.command("192.168.1.20", {}, "resume")
            adapter.command("192.168.1.20", {}, "cancel")

        self.assertEqual(path, "cube.gcode")
        self.assertEqual(upload.call_args.args[4], {"root": "gcodes"})
        self.assertEqual(
            [call.args[0] for call in post.call_args_list],
            [
                "http://192.168.1.20/printer/print/start",
                "http://192.168.1.20/printer/print/pause",
                "http://192.168.1.20/printer/print/resume",
                "http://192.168.1.20/printer/print/cancel",
            ],
        )

    def test_prusalink_upload_and_controls_use_official_local_endpoints(self) -> None:
        adapter = PrusaLinkAdapter()
        with patch("backend.app.adapters._put_binary") as upload, patch(
            "backend.app.adapters._request_empty"
        ) as request, patch(
            "backend.app.adapters._get_json_request",
            return_value={"id": 42},
        ):
            credential = {"username": "maker", "password": "test"}
            path = adapter.upload(
                "192.168.1.44",
                credential,
                Path("cube.gcode"),
                "cube.gcode",
            )
            adapter.command("192.168.1.44", credential, "start", path)
            adapter.command("192.168.1.44", credential, "pause")
            adapter.command("192.168.1.44", credential, "resume")
            adapter.command("192.168.1.44", credential, "cancel")

        self.assertEqual(path, "local/cube.gcode")
        self.assertEqual(
            upload.call_args.args[0],
            "http://192.168.1.44/api/v1/files/local/cube.gcode",
        )
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in request.call_args_list],
            [
                ("POST", "http://192.168.1.44/api/v1/files/local/cube.gcode"),
                ("PUT", "http://192.168.1.44/api/v1/job/42/pause"),
                ("PUT", "http://192.168.1.44/api/v1/job/42/resume"),
                ("DELETE", "http://192.168.1.44/api/v1/job/42"),
            ],
        )
        self.assertTrue(all(call.kwargs["auth"] is not None for call in request.call_args_list))

    def test_prusalink_requires_digest_credentials(self) -> None:
        with self.assertRaisesRegex(AdapterError, "username and password"):
            PrusaLinkAdapter().snapshot("192.168.1.20", {"api_key": "not-used"})

    def test_printer_urls_reject_public_network_destinations(self) -> None:
        with self.assertRaisesRegex(AdapterError, "private local-network"):
            OctoPrintAdapter._url("8.8.8.8", "/api/printer")

        self.assertEqual(
            OctoPrintAdapter._url("192.168.1.20", "/api/printer"),
            "http://192.168.1.20/api/printer",
        )
        with patch(
            "backend.app.adapters.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 0))],
        ):
            self.assertEqual(
                OctoPrintAdapter._url("printer.local", "/api/printer"),
                "http://printer.local/api/printer",
            )

        with patch(
            "backend.app.adapters.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
        ):
            with self.assertRaisesRegex(AdapterError, "private local-network"):
                OctoPrintAdapter._url("printer.local", "/api/printer")

    def test_simulator_supports_a_safe_no_hardware_control_cycle(self) -> None:
        adapter = SimulatorAdapter()
        with patch.object(Path, "is_file", return_value=True):
            path = adapter.upload("demo", {}, Path("cube.gcode"), "cube.gcode")
        adapter.command("demo", {}, "start", path)
        printing = adapter.snapshot("demo", {})
        adapter.command("demo", {}, "pause")
        paused = adapter.snapshot("demo", {})
        adapter.command("demo", {}, "resume")
        adapter.command("demo", {}, "cancel")
        cancelled = adapter.snapshot("demo", {})
        reset = adapter.snapshot("demo", {})

        self.assertEqual(path, "simulator/cube.gcode")
        self.assertEqual(printing.state, "printing")
        self.assertEqual(printing.active_job, "cube.gcode")
        self.assertEqual(paused.state, "attention")
        self.assertEqual(cancelled.job_outcome, "cancelled")
        self.assertEqual(reset.state, "ready")
        self.assertIsNone(reset.job_outcome)
