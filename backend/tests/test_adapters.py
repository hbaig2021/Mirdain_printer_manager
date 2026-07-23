from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.adapters import MoonrakerAdapter, OctoPrintAdapter, PrusaLinkAdapter


class AdapterTests(unittest.TestCase):
    def test_octoprint_snapshot_maps_live_status(self) -> None:
        printer = {
            "state": {"text": "Operational", "flags": {"operational": True, "printing": False, "paused": False}},
            "temperature": {"bed": {"actual": 60.4}, "tool0": {"actual": 214.6}},
        }
        job = {"job": {"file": {"name": "fixture.gcode"}}, "progress": {"completion": 37.8}}
        with patch.object(OctoPrintAdapter, "_json", side_effect=[printer, job]):
            snapshot = OctoPrintAdapter().snapshot("printer.local", {"api_key": "test"})
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.percent_complete, 38)
        self.assertEqual(snapshot.active_job, "fixture.gcode")
        self.assertEqual(snapshot.bed_temp, 60)
        self.assertEqual(snapshot.nozzle_temp, 215)

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
            snapshot = MoonrakerAdapter().snapshot("printer.local", {})
        self.assertEqual(snapshot.state, "printing")
        self.assertEqual(snapshot.percent_complete, 42)
        self.assertEqual(snapshot.active_job, "benchy.gcode")
        self.assertEqual(snapshot.bed_temp, 60)

    def test_prusalink_snapshot_maps_status(self) -> None:
        response = {
            "printer": {"state": "PRINTING", "temp_bed": 60.4, "temp_nozzle": 215.2},
            "job": {"progress": 76.1, "file": {"name": "enclosure.3mf"}},
        }
        with patch.object(PrusaLinkAdapter, "_json", return_value=response):
            snapshot = PrusaLinkAdapter().snapshot("printer.local", {"api_key": "test"})
        self.assertEqual(snapshot.state, "printing")
        self.assertEqual(snapshot.percent_complete, 76)
        self.assertEqual(snapshot.active_job, "enclosure.3mf")
        self.assertEqual(snapshot.nozzle_temp, 215)
