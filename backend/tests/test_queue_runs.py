from __future__ import annotations

import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, get_ident
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import database
from backend.app import main as main_module
from backend.app.adapters import AdapterCapabilities, AdapterSnapshot
from backend.app.main import (
    app,
    dispatch_verified_auto_start,
    reconcile_reported_job_outcome,
    refresh_and_dispatch,
)
from backend.app.models import (
    FileCreate,
    JobCreate,
    JobUpdate,
    PrinterCreate,
    PrinterUpdate,
)


class QueueRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_directory = database.DATA_DIRECTORY
        self.original_database_path = database.DATABASE_PATH
        self.original_files_directory = database.FILES_DIRECTORY
        database.DATA_DIRECTORY = Path(self.temporary_directory.name)
        database.DATABASE_PATH = database.DATA_DIRECTORY / "fleet.db"
        database.FILES_DIRECTORY = database.DATA_DIRECTORY / "files"
        database.initialize()

    def tearDown(self) -> None:
        database.DATA_DIRECTORY = self.original_data_directory
        database.DATABASE_PATH = self.original_database_path
        database.FILES_DIRECTORY = self.original_files_directory
        self.temporary_directory.cleanup()

    def ready_printer(self, name: str, *, auto_start_enabled: bool = False):
        printer = database.create_printer(
            PrinterCreate(
                name=name,
                manufacturer="Example",
                model="Model One",
                adapter_type="simulator",
                auto_start_enabled=auto_start_enabled,
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

    def stored_file(self, name: str = "part.gcode", *, verified: bool = False):
        return database.store_upload(
            FileCreate(
                name=name,
                filename=name,
                file_type="gcode",
                auto_eject_verified=verified,
            ),
            name,
            io.BytesIO(b"G28\n"),
        )

    def test_wal_and_busy_timeout_are_enabled(self) -> None:
        with database.connection() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(busy_timeout, 10_000)

    def test_new_printer_requires_one_confirmed_ready_transition(self) -> None:
        printer = database.create_printer(
            PrinterCreate(
                name="Unknown bed",
                manufacturer="Example",
                model="Model One",
                adapter_type="simulator",
            )
        )
        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=None,
            nozzle_temp=None,
            note=None,
            seen_at=datetime.now(UTC),
        )

        connected = database.configure_connection(
            printer.id,
            adapter_type="simulator",
            host="unknown.simulator",
            credential_id=None,
            capabilities=["telemetry", "file_transfer", "job_control"],
            snapshot=idle,
        )

        self.assertEqual(connected.clearance_state, "unknown")
        self.assertEqual(connected.state, "attention")
        confirmed = database.mark_printer_ready(printer.id)
        self.assertEqual(confirmed.clearance_state, "confirmed")
        self.assertEqual(confirmed.state, "ready")

    def test_restart_invalidates_persisted_clearance_until_ready_is_confirmed(self) -> None:
        printer = self.ready_printer("Restart gate")

        self.assertEqual(database.invalidate_unattended_clearance_on_startup(), 1)
        invalidated = database.get_printer(printer.id)
        self.assertEqual(invalidated.state, "attention")
        self.assertEqual(invalidated.clearance_state, "unknown")

        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        still_gated = database.apply_snapshot(printer.id, idle)
        self.assertEqual(still_gated.state, "attention")
        self.assertEqual(still_gated.clearance_state, "unknown")
        confirmed = database.mark_printer_ready(printer.id)
        self.assertEqual(confirmed.state, "ready")
        self.assertEqual(confirmed.clearance_state, "confirmed")

    def test_monitoring_error_invalidates_clearance_until_reconnect_and_ready(self) -> None:
        printer = self.ready_printer("Monitoring gap")

        failed = database.mark_connection_error(printer.id, "Timed out.")
        self.assertEqual(failed.connection_state, "error")
        self.assertEqual(failed.state, "attention")
        self.assertEqual(failed.clearance_state, "unknown")
        blocked = TestClient(app).post(
            f"/api/printers/{printer.id}/ready",
            json={"confirmation": "READY"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)

        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        reconnected = database.apply_snapshot(printer.id, idle)
        self.assertEqual(reconnected.connection_state, "connected")
        self.assertEqual(reconnected.state, "attention")
        self.assertEqual(reconnected.clearance_state, "unknown")
        confirmed = TestClient(app).post(
            f"/api/printers/{printer.id}/ready",
            json={"confirmation": "READY"},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["state"], "ready")
        self.assertEqual(confirmed.json()["clearance_state"], "confirmed")

    def test_latched_unowned_terminal_outcome_is_acknowledged_by_ready(self) -> None:
        printer = self.ready_printer("External outcome")
        completed = AdapterSnapshot(
            state="ready",
            percent_complete=100,
            active_job="external.gcode",
            bed_temp=30,
            nozzle_temp=35,
            note="complete",
            seen_at=datetime.now(UTC),
            job_outcome="completed",
        )

        detected = database.apply_snapshot(printer.id, completed)
        self.assertEqual(detected.state, "attention")
        self.assertEqual(detected.clearance_state, "awaiting_confirmation")
        database.mark_printer_ready(printer.id)
        same_latched_event = database.apply_snapshot(printer.id, completed)
        self.assertEqual(same_latched_event.state, "ready")
        self.assertEqual(same_latched_event.clearance_state, "confirmed")

        printing = AdapterSnapshot(
            state="printing",
            percent_complete=10,
            active_job="external.gcode",
            bed_temp=60,
            nozzle_temp=205,
            note="printing",
            seen_at=datetime.now(UTC),
        )
        database.apply_snapshot(printer.id, printing)
        later_completion = database.apply_snapshot(printer.id, completed)
        self.assertEqual(later_completion.state, "attention")
        self.assertEqual(later_completion.clearance_state, "awaiting_confirmation")

    def test_concurrent_refreshes_cannot_apply_a_stale_terminal_to_the_next_run(
        self,
    ) -> None:
        source = self.stored_file("repeated.gcode", verified=True)
        printer = self.ready_printer(
            "Serialized refresh",
            auto_start_enabled=True,
        )
        parent = database.create_job(
            JobCreate(
                name="Serialized refresh",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                repetitions=2,
                auto_restart=True,
                auto_clear=True,
            )
        )
        first_run = database.claim_job_for_auto_start(parent.id, printer.id)
        self.assertIsNotNone(first_run)
        database.update_job(first_run.id, JobUpdate(state="running"))
        database.mark_run_start_observed(first_run.id)

        terminal = AdapterSnapshot(
            state="ready",
            percent_complete=100,
            active_job=source.filename,
            bed_temp=30,
            nozzle_temp=35,
            note="complete",
            seen_at=datetime.now(UTC),
            job_outcome="completed",
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
        first_snapshot_entered = Event()
        release_first_snapshot = Event()
        second_refresh_waiting = Event()
        dispatch_lock_finished = Event()
        snapshot_count_lock = Lock()
        snapshot_count = 0
        starts: list[str] = []

        class OrderedPrinterLock:
            def __init__(self) -> None:
                self.lock = Lock()
                self.state_lock = Lock()
                self.context_count = 0
                self.owner: int | None = None

            def context(self):
                with self.state_lock:
                    self.context_count += 1
                    context_number = self.context_count
                ordered = self

                class Context:
                    def __enter__(self):
                        if context_number == 2:
                            second_refresh_waiting.set()
                            if not dispatch_lock_finished.wait(timeout=5):
                                raise AssertionError(
                                    "The newly dispatched run never released "
                                    "the printer command lock."
                                )
                        elif context_number not in {1, 3}:
                            raise AssertionError(
                                "Unexpected printer command-lock context "
                                f"{context_number}."
                            )
                        ordered.lock.acquire()
                        with ordered.state_lock:
                            ordered.owner = get_ident()
                        return self

                    def __exit__(
                        self,
                        _exc_type: object,
                        _exc_value: object,
                        _traceback: object,
                    ) -> None:
                        with ordered.state_lock:
                            ordered.owner = None
                        ordered.lock.release()
                        if context_number == 3:
                            dispatch_lock_finished.set()

                return Context()

            def held_by_current_thread(self) -> bool:
                with self.state_lock:
                    return self.owner == get_ident()

        ordered_lock = OrderedPrinterLock()

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(
                self,
                _host: str,
                _credential: object,
                _path: object,
                remote_name: str,
            ) -> str:
                self.assert_serialized()
                return remote_name

            def snapshot(self, *_: object) -> AdapterSnapshot:
                nonlocal snapshot_count
                self.assert_serialized()
                with snapshot_count_lock:
                    snapshot_count += 1
                    call_number = snapshot_count
                if call_number == 1:
                    first_snapshot_entered.set()
                    if not release_first_snapshot.wait(timeout=5):
                        raise AssertionError(
                            "The first terminal snapshot was not released."
                        )
                    return terminal
                if call_number == 2:
                    return idle
                if call_number == 3:
                    return terminal
                raise AssertionError(
                    f"Unexpected adapter snapshot call {call_number}."
                )

            def command(
                self,
                _host: str,
                _credential: object,
                action: str,
                _path: str | None,
            ) -> None:
                self.assert_serialized()
                starts.append(action)

            @staticmethod
            def assert_serialized() -> None:
                if not ordered_lock.held_by_current_thread():
                    raise AssertionError(
                        "A physical printer operation escaped its command lock."
                    )

        identity_checks = 0
        apply_calls = 0
        reconcile_calls = 0
        real_apply_snapshot = database.apply_snapshot
        real_reconcile = main_module._reconcile_reported_job_outcome_locked

        def verify_identity(*_: object, **_options: object) -> None:
            nonlocal identity_checks
            self.assertTrue(ordered_lock.held_by_current_thread())
            identity_checks += 1

        def apply_serialized(*args: object, **kwargs: object):
            nonlocal apply_calls
            self.assertTrue(ordered_lock.held_by_current_thread())
            apply_calls += 1
            return real_apply_snapshot(*args, **kwargs)

        def reconcile_serialized(*args: object, **kwargs: object):
            nonlocal reconcile_calls
            self.assertTrue(ordered_lock.held_by_current_thread())
            reconcile_calls += 1
            return real_reconcile(*args, **kwargs)

        with patch(
            "backend.app.main.printer_command_lock",
            side_effect=lambda _printer_id: ordered_lock.context(),
        ), patch(
            "backend.app.main.verify_printer_connection_identity",
            side_effect=verify_identity,
        ), patch(
            "backend.app.main.get_adapter",
            return_value=FakeAdapter(),
        ), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), patch(
            "backend.app.main.database.apply_snapshot",
            side_effect=apply_serialized,
        ), patch(
            "backend.app.main._reconcile_reported_job_outcome_locked",
            side_effect=reconcile_serialized,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            first_refresh = pool.submit(refresh_and_dispatch, printer.id)
            second_refresh = None
            try:
                self.assertTrue(first_snapshot_entered.wait(timeout=5))
                second_refresh = pool.submit(refresh_and_dispatch, printer.id)
                self.assertTrue(second_refresh_waiting.wait(timeout=5))
                with snapshot_count_lock:
                    self.assertEqual(snapshot_count, 1)
                self.assertEqual(identity_checks, 1)
            finally:
                release_first_snapshot.set()

            first_refresh.result(timeout=5)
            if second_refresh:
                second_refresh.result(timeout=5)

        self.assertEqual(starts, ["start"])
        self.assertEqual(snapshot_count, 3)
        self.assertGreaterEqual(identity_checks, 5)
        self.assertEqual(apply_calls, 2)
        self.assertEqual(reconcile_calls, 2)
        saved_parent = database.get_job(parent.id)
        self.assertEqual(saved_parent.completed_repetitions, 1)
        self.assertEqual(saved_parent.state, "queued")
        children = sorted(
            (
                job
                for job in database.dashboard().jobs
                if job.parent_job_id == parent.id
            ),
            key=lambda job: job.run_number or 0,
        )
        self.assertEqual(
            [child.state for child in children],
            ["completed", "running"],
        )
        self.assertFalse(children[1].start_observed)
        self.assertEqual(
            database.get_printer(printer.id).active_job_id,
            children[1].id,
        )

    def test_concurrent_enqueues_receive_unique_normalized_positions(self) -> None:
        def enqueue(index: int):
            return database.create_job(
                JobCreate(
                    name=f"Concurrent {index}",
                    filename=f"concurrent-{index}.gcode",
                )
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            created = list(pool.map(enqueue, range(20)))

        positions = [database.get_job(job.id).queue_position for job in created]
        self.assertEqual(sorted(positions), list(range(1, 21)))
        self.assertEqual(len(set(positions)), 20)

    def test_finite_parent_is_claimed_concurrently_without_oversubscription(self) -> None:
        source = self.stored_file()
        parent = database.create_job(
            JobCreate(name="Finite batch", filename=source.filename, file_id=source.id, repetitions=3)
        )
        printers = [self.ready_printer(f"Printer {index}") for index in range(8)]

        with ThreadPoolExecutor(max_workers=len(printers)) as pool:
            results = list(
                pool.map(
                    lambda printer: database.claim_job_for_auto_start(parent.id, printer.id),
                    printers,
                )
            )

        runs = [run for run in results if run is not None]
        self.assertEqual(len(runs), 3)
        self.assertEqual(len({run.id for run in runs}), 3)
        self.assertEqual({run.parent_job_id for run in runs}, {parent.id})
        self.assertEqual({run.run_number for run in runs}, {1, 2, 3})
        self.assertEqual(database.get_job(parent.id).state, "queued")
        self.assertEqual(
            sum(database.get_printer(printer.id).active_job_id is not None for printer in printers),
            3,
        )

    def test_infinite_parent_can_supply_multiple_printers(self) -> None:
        source = self.stored_file()
        parent = database.create_job(
            JobCreate(name="Infinite batch", filename=source.filename, file_id=source.id, repetitions=None)
        )
        printers = [self.ready_printer(f"Infinite {index}") for index in range(4)]

        runs = [
            database.claim_job_for_auto_start(parent.id, printer.id)
            for printer in printers
        ]

        self.assertTrue(all(run is not None for run in runs))
        self.assertEqual(database.get_job(parent.id).state, "queued")
        self.assertEqual(database.get_job(parent.id).completed_repetitions, 0)

    def test_cycle_moves_parent_to_tail_when_assigned(self) -> None:
        source = self.stored_file()
        cycle = database.create_job(
            JobCreate(
                name="Cycle",
                filename=source.filename,
                file_id=source.id,
                repetitions=2,
                loop_mode="cycle",
            )
        )
        other = database.create_job(
            JobCreate(name="Other", filename=source.filename, file_id=source.id)
        )
        printer = self.ready_printer("Cycle printer")
        second_printer = self.ready_printer("Cycle stale scanner")
        expected = database.get_job(cycle.id)

        run = database.claim_job_for_auto_start(
            cycle.id,
            printer.id,
            expected_job=expected,
        )
        stale_claim = database.claim_job_for_auto_start(
            cycle.id,
            second_printer.id,
            expected_job=expected,
        )

        self.assertIsNotNone(run)
        self.assertIsNone(stale_claim)
        self.assertEqual(database.get_job(other.id).queue_position, 1)
        self.assertEqual(database.get_job(cycle.id).queue_position, 2)

    def test_sticky_stays_first_and_child_completion_is_idempotent(self) -> None:
        source = self.stored_file()
        sticky = database.create_job(
            JobCreate(
                name="Sticky",
                filename=source.filename,
                file_id=source.id,
                repetitions=2,
                loop_mode="sticky",
            )
        )
        other = database.create_job(
            JobCreate(name="Other", filename=source.filename, file_id=source.id)
        )
        printers = [self.ready_printer(f"Sticky {index}") for index in range(2)]
        runs = [
            database.claim_job_for_auto_start(sticky.id, printer.id)
            for printer in printers
        ]

        self.assertEqual(database.get_job(sticky.id).queue_position, 1)
        self.assertEqual(database.get_job(other.id).queue_position, 2)
        for run in runs:
            database.update_job(run.id, JobUpdate(state="running"))

        database.complete_job(runs[0].id)
        database.complete_job(runs[0].id)
        self.assertEqual(database.get_job(sticky.id).completed_repetitions, 1)
        self.assertEqual(database.get_job(sticky.id).state, "queued")

        database.complete_job(runs[1].id)
        self.assertEqual(database.get_job(sticky.id).completed_repetitions, 2)
        self.assertEqual(database.get_job(sticky.id).state, "completed")
        self.assertEqual(database.get_job(other.id).queue_position, 1)

    def test_cancel_pauses_parent_and_idle_telemetry_does_not_clear_gate(self) -> None:
        source = self.stored_file()
        parent = database.create_job(
            JobCreate(name="Review batch", filename=source.filename, file_id=source.id, repetitions=2)
        )
        first = self.ready_printer("First")
        second = self.ready_printer("Second")
        run = database.claim_job_for_auto_start(parent.id, first.id)
        database.update_job(run.id, JobUpdate(state="running"))

        database.terminate_job(
            run.id,
            "cancelled",
            "Inspect the cancelled print.",
            release_ownership=True,
        )

        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        self.assertIsNone(database.claim_job_for_auto_start(parent.id, second.id))
        after_cancel = database.get_printer(first.id)
        self.assertEqual(after_cancel.state, "attention")
        self.assertEqual(after_cancel.clearance_state, "awaiting_confirmation")

        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        database.apply_snapshot(first.id, idle)
        after_idle = database.get_printer(first.id)
        self.assertEqual(after_idle.state, "attention")
        self.assertEqual(after_idle.clearance_state, "awaiting_confirmation")

        confirmed = database.mark_printer_ready(first.id)
        self.assertEqual(confirmed.state, "ready")
        self.assertEqual(confirmed.clearance_state, "confirmed")
        database.update_job(parent.id, JobUpdate(dispatch_paused=False))
        replacement = database.claim_job_for_auto_start(parent.id, second.id)
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.parent_job_id, parent.id)

    def test_confirmed_ready_can_fail_closed_a_stale_start_reservation(self) -> None:
        source = self.stored_file()
        parent = database.create_job(
            JobCreate(name="Stale start", filename=source.filename, file_id=source.id)
        )
        printer = self.ready_printer("Stale printer")
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        idle = AdapterSnapshot(
            state="ready",
            percent_complete=None,
            active_job=None,
            bed_temp=25,
            nozzle_temp=30,
            note=None,
            seen_at=datetime.now(UTC),
        )
        database.apply_snapshot(printer.id, idle)

        response = TestClient(app).post(
            f"/api/printers/{printer.id}/ready",
            json={"confirmation": "READY"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.get_job(run.id).state, "failed")
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        self.assertEqual(database.get_printer(printer.id).clearance_state, "confirmed")
        self.assertIsNone(database.get_printer(printer.id).active_job_id)

    def test_start_failure_pauses_parent_and_retains_ownership_for_review(self) -> None:
        source = self.stored_file()
        parent = database.create_job(
            JobCreate(name="Failed start", filename=source.filename, file_id=source.id)
        )
        printer = self.ready_printer("Failed start printer")
        run = database.claim_job_for_auto_start(parent.id, printer.id)

        database.fail_auto_start_job(run.id, "The start request timed out.")

        self.assertEqual(database.get_job(run.id).state, "failed")
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.state, "attention")
        self.assertEqual(saved.clearance_state, "awaiting_confirmation")
        self.assertEqual(saved.active_job_id, run.id)

    def test_trusted_auto_clear_requires_task_flag_and_verified_file(self) -> None:
        cases = (
            (True, True, "ready", "auto_verified"),
            (True, False, "attention", "awaiting_confirmation"),
            (False, True, "attention", "awaiting_confirmation"),
        )
        for index, (auto_clear, verified, state, clearance) in enumerate(cases):
            with self.subTest(auto_clear=auto_clear, verified=verified):
                source = self.stored_file(f"case-{index}.gcode", verified=verified)
                parent = database.create_job(
                    JobCreate(
                        name=f"Case {index}",
                        filename=source.filename,
                        file_id=source.id,
                        auto_clear=auto_clear,
                    )
                )
                printer = self.ready_printer(f"Case printer {index}")
                run = database.claim_job_for_auto_start(parent.id, printer.id)
                database.update_job(run.id, JobUpdate(state="running"))

                database.complete_job(run.id, trusted_auto_clear=True)

                saved = database.get_printer(printer.id)
                self.assertEqual(saved.state, state)
                self.assertEqual(saved.clearance_state, clearance)

    def test_auto_clear_can_be_revoked_while_a_run_is_active(self) -> None:
        source = self.stored_file("revoke-clear.gcode", verified=True)
        printer = self.ready_printer("Revoke clearance")
        parent = database.create_job(
            JobCreate(
                name="Revoke clearance",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                auto_clear=True,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        database.update_job(run.id, JobUpdate(state="running"))
        database.mark_run_start_observed(run.id)

        response = TestClient(app).patch(
            f"/api/jobs/{parent.id}",
            json={"auto_clear": False, "auto_restart": False, "dispatch_paused": True},
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated_parent = database.get_job(parent.id)
        self.assertFalse(updated_parent.auto_clear)
        self.assertFalse(updated_parent.auto_restart)
        self.assertTrue(updated_parent.dispatch_paused)
        self.assertFalse(database.get_job(run.id).auto_clear)
        database.complete_job(run.id, trusted_auto_clear=True)
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.state, "attention")
        self.assertEqual(saved.clearance_state, "awaiting_confirmation")

    def test_manual_complete_does_not_auto_clear_or_redispatch(self) -> None:
        source = self.stored_file("manual.gcode", verified=True)
        printer = self.ready_printer("Manual complete", auto_start_enabled=True)
        first = database.create_job(
            JobCreate(
                name="First",
                filename=source.filename,
                file_id=source.id,
                repetitions=1,
                auto_clear=True,
                auto_restart=True,
            )
        )
        run = database.claim_job_for_auto_start(first.id, printer.id)
        database.update_job(run.id, JobUpdate(state="running"))
        second = database.create_job(
            JobCreate(
                name="Second",
                filename=source.filename,
                file_id=source.id,
                auto_restart=True,
            )
        )

        response = TestClient(app).post(
            f"/api/jobs/{run.id}/complete",
            json={"confirmation": "COMPLETE"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.get_printer(printer.id).state, "attention")
        self.assertEqual(database.get_printer(printer.id).clearance_state, "awaiting_confirmation")
        self.assertFalse(any(job.parent_job_id == second.id for job in database.dashboard().jobs))

    def test_unattended_dispatch_rejects_an_unverified_file_before_upload(self) -> None:
        source = self.stored_file("unverified.gcode", verified=False)
        printer = self.ready_printer("Unverified guard", auto_start_enabled=True)
        parent = database.create_job(
            JobCreate(
                name="Unverified guard",
                filename=source.filename,
                file_id=source.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        calls: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                calls.append("upload")
                return source.filename

            def command(self, *_: object) -> None:
                calls.append("start")

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ):
            started = dispatch_verified_auto_start(printer.id)

        self.assertIsNone(started)
        self.assertEqual(calls, [])
        self.assertFalse(database.get_job(parent.id).dispatch_paused)
        self.assertFalse(
            any(
                job.parent_job_id == parent.id
                for job in database.dashboard().jobs
            )
        )
        self.assertEqual(database.get_printer(printer.id).state, "ready")

    def test_disabling_auto_start_during_upload_prevents_physical_start(self) -> None:
        source = self.stored_file("blocked-upload.gcode", verified=True)
        printer = self.ready_printer("Blocked upload", auto_start_enabled=True)
        parent = database.create_job(
            JobCreate(
                name="Blocked upload",
                filename=source.filename,
                file_id=source.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        upload_started = Event()
        release_upload = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                upload_started.set()
                self.assert_released()
                return source.filename

            def assert_released(self) -> None:
                if not release_upload.wait(timeout=5):
                    raise AssertionError("The upload test was not released.")

            def command(self, _host: str, _credential: object, action: str, _path: str | None) -> None:
                commands.append(action)

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), ThreadPoolExecutor(max_workers=1) as pool:
            dispatch = pool.submit(dispatch_verified_auto_start, printer.id)
            self.assertTrue(upload_started.wait(timeout=5))
            disabled = TestClient(app).patch(
                f"/api/printers/{printer.id}",
                json={"auto_start_enabled": False},
            )
            self.assertEqual(disabled.status_code, 200)
            release_upload.set()
            self.assertIsNone(dispatch.result(timeout=5))

        self.assertEqual(commands, [])
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        child = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent.id
        )
        self.assertEqual(child.state, "cancelled")
        saved_printer = database.get_printer(printer.id)
        self.assertEqual(saved_printer.state, "attention")
        self.assertEqual(
            saved_printer.clearance_state,
            "awaiting_confirmation",
        )

    def test_confirmed_cancel_during_upload_prevents_start_without_remote_cancel(
        self,
    ) -> None:
        source = self.stored_file("cancel-upload.gcode", verified=True)
        printer = self.ready_printer(
            "Cancel during upload",
            auto_start_enabled=True,
        )
        parent = database.create_job(
            JobCreate(
                name="Cancel during upload",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        upload_entered = Event()
        release_upload = Event()
        commands: list[str] = []
        snapshots = 0

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                upload_entered.set()
                if not release_upload.wait(timeout=5):
                    raise AssertionError(
                        "The cancellation upload test was not released."
                    )
                return source.filename

            def snapshot(self, *_: object) -> AdapterSnapshot:
                nonlocal snapshots
                snapshots += 1
                return AdapterSnapshot(
                    state="ready",
                    percent_complete=None,
                    active_job=None,
                    bed_temp=25,
                    nozzle_temp=30,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(
                self,
                _host: str,
                _credential: object,
                action: str,
                _path: str | None,
            ) -> None:
                commands.append(action)

        client = TestClient(app)
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), patch(
            "backend.app.main.verify_printer_connection_identity",
            return_value=None,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            dispatch = pool.submit(dispatch_verified_auto_start, printer.id)
            cancel = None
            try:
                self.assertTrue(upload_entered.wait(timeout=5))
                child = database.active_job_for_printer(printer.id)
                self.assertIsNotNone(child)
                cancel = pool.submit(
                    client.post,
                    f"/api/jobs/{child.id}/control",
                    json={
                        "action": "cancel",
                        "confirmation": "CANCEL",
                    },
                )
                self.assertTrue(
                    main_module.run_cancel_intent(child.id).wait(timeout=5)
                )
                self.assertFalse(cancel.done())
            finally:
                release_upload.set()

            self.assertIsNone(dispatch.result(timeout=5))
            cancel_response = cancel.result(timeout=5) if cancel else None

        self.assertEqual(cancel_response.status_code, 200, cancel_response.text)
        self.assertEqual(cancel_response.json()["state"], "cancelled")
        self.assertEqual(commands, [])
        self.assertEqual(snapshots, 1)
        saved_child = database.get_job(child.id)
        self.assertEqual(saved_child.state, "cancelled")
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        saved_printer = database.get_printer(printer.id)
        self.assertEqual(saved_printer.state, "attention")
        self.assertEqual(
            saved_printer.clearance_state,
            "awaiting_confirmation",
        )

    def test_combined_printer_patch_revokes_auto_start_during_upload(
        self,
    ) -> None:
        source = self.stored_file("combined-disable.gcode", verified=True)
        printer = self.ready_printer(
            "Combined disable",
            auto_start_enabled=True,
        )
        parent = database.create_job(
            JobCreate(
                name="Combined disable",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        upload_entered = Event()
        release_upload = Event()
        patch_completed = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                upload_entered.set()
                if not release_upload.wait(timeout=5):
                    raise AssertionError(
                        "The combined PATCH upload test was not released."
                    )
                return source.filename

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

            def command(
                self,
                _host: str,
                _credential: object,
                action: str,
                _path: str | None,
            ) -> None:
                commands.append(action)

        client = TestClient(app)

        def disable_and_favorite():
            try:
                return client.patch(
                    f"/api/printers/{printer.id}",
                    json={
                        "auto_start_enabled": False,
                        "favorite": True,
                    },
                )
            finally:
                patch_completed.set()

        completed_while_upload_blocked = False
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), patch(
            "backend.app.main.verify_printer_connection_identity",
            return_value=None,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            dispatch = pool.submit(dispatch_verified_auto_start, printer.id)
            combined_patch = None
            try:
                self.assertTrue(upload_entered.wait(timeout=5))
                combined_patch = pool.submit(disable_and_favorite)
                completed_while_upload_blocked = patch_completed.wait(timeout=2)
            finally:
                release_upload.set()

            self.assertIsNone(dispatch.result(timeout=5))
            patch_response = (
                combined_patch.result(timeout=5)
                if combined_patch
                else None
            )

        self.assertTrue(
            completed_while_upload_blocked,
            "The emergency combined PATCH waited for the blocked upload.",
        )
        self.assertEqual(patch_response.status_code, 200, patch_response.text)
        self.assertFalse(patch_response.json()["auto_start_enabled"])
        self.assertTrue(patch_response.json()["favorite"])
        self.assertEqual(commands, [])
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        child = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent.id
        )
        self.assertEqual(child.state, "cancelled")
        saved_printer = database.get_printer(printer.id)
        self.assertFalse(saved_printer.auto_start_enabled)
        self.assertTrue(saved_printer.favorite)
        self.assertEqual(saved_printer.state, "attention")
        self.assertEqual(
            saved_printer.clearance_state,
            "awaiting_confirmation",
        )

    def test_disabling_auto_start_during_fresh_snapshot_prevents_physical_start(
        self,
    ) -> None:
        source = self.stored_file("blocked-snapshot.gcode", verified=True)
        printer = self.ready_printer("Blocked snapshot", auto_start_enabled=True)
        parent = database.create_job(
            JobCreate(
                name="Blocked snapshot",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        snapshot_entered = Event()
        release_snapshot = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                return source.filename

            def snapshot(self, *_: object) -> AdapterSnapshot:
                snapshot_entered.set()
                if not release_snapshot.wait(timeout=5):
                    raise AssertionError("The fresh snapshot test was not released.")
                return AdapterSnapshot(
                    state="ready",
                    percent_complete=None,
                    active_job=None,
                    bed_temp=25,
                    nozzle_temp=30,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(
                self,
                _host: str,
                _credential: object,
                action: str,
                _path: str | None,
            ) -> None:
                commands.append(action)

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), ThreadPoolExecutor(max_workers=1) as pool:
            dispatch = pool.submit(dispatch_verified_auto_start, printer.id)
            try:
                self.assertTrue(snapshot_entered.wait(timeout=5))
                disabled = TestClient(app).patch(
                    f"/api/printers/{printer.id}",
                    json={"auto_start_enabled": False},
                )
                self.assertEqual(disabled.status_code, 200, disabled.text)
            finally:
                release_snapshot.set()
            self.assertIsNone(dispatch.result(timeout=5))

        self.assertEqual(commands, [])
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        child = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent.id
        )
        self.assertEqual(child.state, "cancelled")
        saved_printer = database.get_printer(printer.id)
        self.assertEqual(saved_printer.state, "attention")
        self.assertEqual(
            saved_printer.clearance_state,
            "awaiting_confirmation",
        )

    def test_shutdown_requested_during_fresh_snapshot_prevents_physical_start(
        self,
    ) -> None:
        source = self.stored_file("snapshot-shutdown.gcode", verified=True)
        printer = self.ready_printer(
            "Snapshot shutdown gate",
            auto_start_enabled=True,
        )
        parent = database.create_job(
            JobCreate(
                name="Snapshot shutdown gate",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                auto_restart=True,
            )
        )
        snapshot_entered = Event()
        release_snapshot = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                return source.filename

            def snapshot(self, *_: object) -> AdapterSnapshot:
                snapshot_entered.set()
                if not release_snapshot.wait(timeout=5):
                    raise AssertionError(
                        "The shutdown snapshot test was not released."
                    )
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
                commands.append("start")

        main_module._AUTOMATION_STOP_EVENT.clear()
        main_module._AUTOMATION_WORKER_STATUS["running"] = True
        try:
            with patch(
                "backend.app.main.get_adapter",
                return_value=FakeAdapter(),
            ), patch(
                "backend.app.main.load_secret",
                return_value={},
            ), ThreadPoolExecutor(max_workers=1) as pool:
                dispatch = pool.submit(dispatch_verified_auto_start, printer.id)
                try:
                    self.assertTrue(snapshot_entered.wait(timeout=5))
                    main_module._AUTOMATION_STOP_EVENT.set()
                finally:
                    release_snapshot.set()
                self.assertIsNone(dispatch.result(timeout=5))
        finally:
            main_module._AUTOMATION_WORKER_STATUS["running"] = False
            main_module._AUTOMATION_STOP_EVENT.clear()

        self.assertEqual(commands, [])
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        child = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent.id
        )
        self.assertEqual(child.state, "cancelled")
        saved_printer = database.get_printer(printer.id)
        self.assertEqual(saved_printer.state, "attention")
        self.assertEqual(
            saved_printer.clearance_state,
            "awaiting_confirmation",
        )

    def test_disabling_one_printer_does_not_wait_for_another_printers_start(
        self,
    ) -> None:
        source_a = self.stored_file("printer-a.gcode", verified=True)
        source_b = self.stored_file("printer-b.gcode", verified=True)
        printer_a = self.ready_printer("Printer A", auto_start_enabled=True)
        printer_b = self.ready_printer("Printer B", auto_start_enabled=True)
        database.create_job(
            JobCreate(
                name="Parent A",
                filename=source_a.filename,
                file_id=source_a.id,
                target_printer_id=printer_a.id,
                auto_restart=True,
            )
        )
        parent_b = database.create_job(
            JobCreate(
                name="Parent B",
                filename=source_b.filename,
                file_id=source_b.id,
                target_printer_id=printer_b.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        a_start_entered = Event()
        release_a_start = Event()
        b_snapshot_entered = Event()
        release_b_snapshot = Event()
        disable_completed = Event()
        b_dispatch_completed = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(
                self,
                _host: str,
                _credential: object,
                _path: object,
                remote_name: str,
            ) -> str:
                return remote_name

            def snapshot(self, host: str, *_: object) -> AdapterSnapshot:
                if host == printer_b.host:
                    b_snapshot_entered.set()
                    if not release_b_snapshot.wait(timeout=5):
                        raise AssertionError(
                            "Printer B's fresh snapshot was not released."
                        )
                return AdapterSnapshot(
                    state="ready",
                    percent_complete=None,
                    active_job=None,
                    bed_temp=25,
                    nozzle_temp=30,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(
                self,
                host: str,
                _credential: object,
                action: str,
                _path: str | None,
            ) -> None:
                commands.append("A" if host == printer_a.host else "B")
                if host == printer_a.host:
                    a_start_entered.set()
                    if not release_a_start.wait(timeout=5):
                        raise AssertionError(
                            "Printer A's START command was not released."
                        )
                self.assert_start(action)

            @staticmethod
            def assert_start(action: str) -> None:
                if action != "start":
                    raise AssertionError(f"Unexpected command: {action}")

        client = TestClient(app)

        def disable_printer_b():
            try:
                return client.patch(
                    f"/api/printers/{printer_b.id}",
                    json={"auto_start_enabled": False},
                )
            finally:
                disable_completed.set()

        def dispatch_b():
            try:
                return dispatch_verified_auto_start(printer_b.id)
            finally:
                b_dispatch_completed.set()

        patch_completed_while_a_blocked = False
        b_finished_while_a_blocked = False
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), patch(
            "backend.app.main.verify_printer_connection_identity",
            return_value=None,
        ), ThreadPoolExecutor(max_workers=3) as pool:
            a_dispatch = pool.submit(
                dispatch_verified_auto_start,
                printer_a.id,
            )
            b_dispatch = None
            disable = None
            try:
                self.assertTrue(a_start_entered.wait(timeout=5))
                b_dispatch = pool.submit(dispatch_b)
                self.assertTrue(b_snapshot_entered.wait(timeout=5))
                disable = pool.submit(disable_printer_b)
                patch_completed_while_a_blocked = disable_completed.wait(
                    timeout=2
                )
                release_b_snapshot.set()
                b_finished_while_a_blocked = b_dispatch_completed.wait(
                    timeout=2
                )
            finally:
                release_b_snapshot.set()
                release_a_start.set()

            started_a = a_dispatch.result(timeout=5)
            stopped_b = b_dispatch.result(timeout=5) if b_dispatch else None
            disable_response = disable.result(timeout=5) if disable else None

        self.assertTrue(
            patch_completed_while_a_blocked,
            "Disabling Printer B waited for Printer A's blocked START.",
        )
        self.assertTrue(
            b_finished_while_a_blocked,
            "Printer B remained blocked behind Printer A's START.",
        )
        self.assertEqual(disable_response.status_code, 200, disable_response.text)
        self.assertIsNotNone(started_a)
        self.assertIsNone(stopped_b)
        self.assertEqual(commands, ["A"])
        self.assertTrue(database.get_job(parent_b.id).dispatch_paused)
        child_b = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent_b.id
        )
        self.assertEqual(child_b.state, "cancelled")
        saved_b = database.get_printer(printer_b.id)
        self.assertEqual(saved_b.state, "attention")
        self.assertEqual(
            saved_b.clearance_state,
            "awaiting_confirmation",
        )

    def test_parent_revocation_intent_wins_before_its_database_patch(
        self,
    ) -> None:
        source = self.stored_file("shared-parent.gcode", verified=True)
        printer_a = self.ready_printer(
            "Shared parent A",
            auto_start_enabled=True,
        )
        printer_b = self.ready_printer(
            "Shared parent B",
            auto_start_enabled=True,
        )
        parent = database.create_job(
            JobCreate(
                name="Shared parent",
                filename=source.filename,
                file_id=source.id,
                repetitions=2,
                auto_restart=True,
            )
        )
        a_start_entered = Event()
        release_a_start = Event()
        b_waiting_for_parent_gate = Event()
        patch_waiting_for_parent_gate = Event()
        b_acquired_parent_gate = Event()
        release_b_inside_parent_gate = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(
                self,
                _host: str,
                _credential: object,
                _path: object,
                remote_name: str,
            ) -> str:
                return remote_name

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

            def command(
                self,
                host: str,
                _credential: object,
                _action: str,
                _path: str | None,
            ) -> None:
                commands.append("A" if host == printer_a.host else "B")
                if host == printer_a.host:
                    a_start_entered.set()
                    if not release_a_start.wait(timeout=5):
                        raise AssertionError(
                            "Printer A's shared-parent START was not released."
                        )

        class OrderedParentGate:
            def __init__(self) -> None:
                self.lock = Lock()
                self.sequence_lock = Lock()
                self.enter_calls = 0

            def __enter__(self):
                with self.sequence_lock:
                    self.enter_calls += 1
                    call_number = self.enter_calls
                if call_number == 2:
                    b_waiting_for_parent_gate.set()
                elif call_number == 3:
                    patch_waiting_for_parent_gate.set()
                    if not b_acquired_parent_gate.wait(timeout=5):
                        raise AssertionError(
                            "Printer B never acquired the parent gate."
                        )
                elif call_number != 1:
                    raise AssertionError(
                        f"Unexpected parent-gate entry {call_number}."
                    )
                self.lock.acquire()
                if call_number == 2:
                    b_acquired_parent_gate.set()
                    if not release_b_inside_parent_gate.wait(timeout=5):
                        raise AssertionError(
                            "Printer B was not released inside the parent gate."
                        )
                return self

            def __exit__(
                self,
                _exc_type: object,
                _exc_value: object,
                _traceback: object,
            ) -> None:
                self.lock.release()

        ordered_parent_gate = OrderedParentGate()
        client = TestClient(app)

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), patch(
            "backend.app.main.verify_printer_connection_identity",
            return_value=None,
        ), patch(
            "backend.app.main.job_automation_start_gate",
            return_value=ordered_parent_gate,
        ), ThreadPoolExecutor(max_workers=3) as pool:
            a_dispatch = pool.submit(
                dispatch_verified_auto_start,
                printer_a.id,
            )
            b_dispatch = None
            revoke = None
            try:
                self.assertTrue(a_start_entered.wait(timeout=5))
                b_dispatch = pool.submit(
                    dispatch_verified_auto_start,
                    printer_b.id,
                )
                self.assertTrue(b_waiting_for_parent_gate.wait(timeout=5))
                revoke = pool.submit(
                    client.patch,
                    f"/api/jobs/{parent.id}",
                    json={
                        "dispatch_paused": True,
                        "auto_restart": False,
                    },
                )
                self.assertTrue(
                    patch_waiting_for_parent_gate.wait(timeout=5)
                )
                self.assertTrue(
                    main_module.job_automation_revocation(parent.id).is_set()
                )

                release_a_start.set()
                self.assertTrue(b_acquired_parent_gate.wait(timeout=5))
                self.assertFalse(revoke.done())
                before_patch = database.get_job(parent.id)
                self.assertTrue(before_patch.auto_restart)
                self.assertFalse(before_patch.dispatch_paused)
                release_b_inside_parent_gate.set()
            finally:
                release_a_start.set()
                release_b_inside_parent_gate.set()

            started_a = a_dispatch.result(timeout=5)
            stopped_b = b_dispatch.result(timeout=5) if b_dispatch else None
            revoke_response = revoke.result(timeout=5) if revoke else None

        self.assertIsNotNone(started_a)
        self.assertIsNone(stopped_b)
        self.assertEqual(commands, ["A"])
        self.assertEqual(revoke_response.status_code, 200, revoke_response.text)
        updated_parent = database.get_job(parent.id)
        self.assertFalse(updated_parent.auto_restart)
        self.assertTrue(updated_parent.dispatch_paused)
        children = sorted(
            (
                job
                for job in database.dashboard().jobs
                if job.parent_job_id == parent.id
            ),
            key=lambda job: job.run_number or 0,
        )
        self.assertEqual(
            [child.state for child in children],
            ["running", "cancelled"],
        )
        saved_b = database.get_printer(printer_b.id)
        self.assertEqual(saved_b.state, "attention")
        self.assertEqual(
            saved_b.clearance_state,
            "awaiting_confirmation",
        )

    def test_shutdown_requested_during_upload_prevents_physical_start(self) -> None:
        source = self.stored_file("shutdown.gcode", verified=True)
        printer = self.ready_printer("Shutdown gate", auto_start_enabled=True)
        parent = database.create_job(
            JobCreate(
                name="Shutdown gate",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                auto_restart=True,
            )
        )
        upload_entered = Event()
        release_upload = Event()
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                upload_entered.set()
                if not release_upload.wait(timeout=5):
                    raise AssertionError("The shutdown upload test was not released.")
                return source.filename

            def command(self, *_: object) -> None:
                commands.append("start")

        main_module._AUTOMATION_STOP_EVENT.clear()
        main_module._AUTOMATION_WORKER_STATUS["running"] = True
        try:
            with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
                "backend.app.main.load_secret",
                return_value={},
            ), ThreadPoolExecutor(max_workers=1) as pool:
                dispatch = pool.submit(dispatch_verified_auto_start, printer.id)
                self.assertTrue(upload_entered.wait(timeout=5))
                main_module._AUTOMATION_STOP_EVENT.set()
                release_upload.set()
                self.assertIsNone(dispatch.result(timeout=5))
        finally:
            main_module._AUTOMATION_WORKER_STATUS["running"] = False
            main_module._AUTOMATION_STOP_EVENT.clear()

        self.assertEqual(commands, [])
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        child = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent.id
        )
        self.assertEqual(child.state, "cancelled")

    def test_manual_upload_serializes_against_auto_upload_and_start(self) -> None:
        source = self.stored_file("serialized-upload.gcode", verified=True)
        printer = self.ready_printer("Serialized upload", auto_start_enabled=True)
        manual = database.create_job(
            JobCreate(
                name="Manual delivery",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        database.create_job(
            JobCreate(
                name="Automatic delivery",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                auto_restart=True,
            )
        )
        first_upload_entered = Event()
        release_first_upload = Event()
        uploads = 0
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                nonlocal uploads
                uploads += 1
                if uploads == 1:
                    first_upload_entered.set()
                    if not release_first_upload.wait(timeout=5):
                        raise AssertionError("The serialized upload test was not released.")
                return source.filename

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
                commands.append("start")

        client = TestClient(app)
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), ThreadPoolExecutor(max_workers=2) as pool:
            manual_delivery = pool.submit(
                client.post,
                f"/api/jobs/{manual.id}/deliver",
            )
            self.assertTrue(first_upload_entered.wait(timeout=5))
            automatic = pool.submit(dispatch_verified_auto_start, printer.id)
            self.assertFalse(automatic.done())
            self.assertEqual(uploads, 1)
            self.assertEqual(commands, [])
            release_first_upload.set()
            self.assertEqual(manual_delivery.result(timeout=5).status_code, 200)
            self.assertIsNotNone(automatic.result(timeout=5))

        self.assertEqual(uploads, 2)
        self.assertEqual(commands, ["start"])

    def test_fresh_terminal_during_upload_blocks_unattended_start(self) -> None:
        source = self.stored_file("fresh-terminal.gcode", verified=True)
        printer = self.ready_printer("Fresh terminal", auto_start_enabled=True)
        parent = database.create_job(
            JobCreate(
                name="Fresh terminal",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                auto_restart=True,
            )
        )
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def upload(self, *_: object) -> str:
                return source.filename

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return AdapterSnapshot(
                    state="ready",
                    percent_complete=100,
                    active_job="external-print.gcode",
                    bed_temp=30,
                    nozzle_temp=35,
                    note="complete",
                    seen_at=datetime.now(UTC),
                    job_outcome="completed",
                )

            def command(self, *_: object) -> None:
                commands.append("start")

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ):
            self.assertIsNone(dispatch_verified_auto_start(printer.id))

        self.assertEqual(commands, [])
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        child = next(
            job
            for job in database.dashboard().jobs
            if job.parent_job_id == parent.id
        )
        self.assertEqual(child.state, "cancelled")
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.state, "attention")
        self.assertEqual(saved.clearance_state, "awaiting_confirmation")

    def test_manual_start_and_cancel_commands_are_serialized(self) -> None:
        source = self.stored_file("serialized.gcode")
        printer = self.ready_printer("Serialized commands")
        parent = database.create_job(
            JobCreate(
                name="Serialized commands",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        database.mark_job_delivered(parent.id, source.filename)
        start_entered = Event()
        release_start = Event()
        commands: list[str] = []
        started = False

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def command(self, _host: str, _credential: object, action: str, _path: str | None) -> None:
                nonlocal started
                commands.append(action)
                if action == "start":
                    started = True
                    start_entered.set()
                    if not release_start.wait(timeout=5):
                        raise AssertionError("The start command test was not released.")

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return AdapterSnapshot(
                    state="printing" if started else "ready",
                    percent_complete=1 if started else None,
                    active_job=source.filename if started else None,
                    bed_temp=60 if started else 25,
                    nozzle_temp=205 if started else 30,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

        client = TestClient(app)
        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ), ThreadPoolExecutor(max_workers=2) as pool:
            start_response = pool.submit(
                client.post,
                f"/api/jobs/{parent.id}/control",
                json={"action": "start", "confirmation": "START"},
            )
            self.assertTrue(start_entered.wait(timeout=5))
            child = database.active_job_for_printer(printer.id)
            self.assertIsNotNone(child)
            cancel_response = pool.submit(
                client.post,
                f"/api/jobs/{child.id}/control",
                json={"action": "cancel", "confirmation": "CANCEL"},
            )
            self.assertFalse(cancel_response.done())
            release_start.set()
            self.assertEqual(start_response.result(timeout=5).status_code, 200)
            self.assertEqual(cancel_response.result(timeout=5).status_code, 200)

        self.assertEqual(commands, ["start", "cancel"])
        self.assertEqual(database.get_job(child.id).state, "cancelled")
        self.assertTrue(database.get_job(parent.id).dispatch_paused)

    def test_manual_start_requires_fresh_idle_telemetry_before_claiming_a_run(self) -> None:
        unsafe_snapshots = (
            ("printing", None, None),
            ("ready", "external-print.gcode", None),
            ("ready", None, "completed"),
        )
        client = TestClient(app)

        for index, (state, active_job, job_outcome) in enumerate(unsafe_snapshots):
            with self.subTest(
                state=state,
                active_job=active_job,
                job_outcome=job_outcome,
            ):
                source = self.stored_file(f"fresh-manual-{index}.gcode")
                printer = self.ready_printer(f"Fresh manual {index}")
                parent = database.create_job(
                    JobCreate(
                        name=f"Fresh manual {index}",
                        filename=source.filename,
                        file_id=source.id,
                        target_printer_id=printer.id,
                    )
                )
                database.mark_job_delivered(parent.id, source.filename)
                snapshots = 0
                commands: list[str] = []

                class FakeAdapter:
                    capabilities = AdapterCapabilities(
                        telemetry=True,
                        file_transfer=True,
                        job_control=True,
                        camera=False,
                    )

                    def snapshot(self, *_: object) -> AdapterSnapshot:
                        nonlocal snapshots
                        snapshots += 1
                        return AdapterSnapshot(
                            state=state,
                            percent_complete=None,
                            active_job=active_job,
                            bed_temp=25,
                            nozzle_temp=30,
                            note=None,
                            seen_at=datetime.now(UTC),
                            job_outcome=job_outcome,
                        )

                    def command(self, *_: object) -> None:
                        commands.append("start")

                with patch(
                    "backend.app.main.get_adapter",
                    return_value=FakeAdapter(),
                ), patch(
                    "backend.app.main.load_secret",
                    return_value={},
                ):
                    response = client.post(
                        f"/api/jobs/{parent.id}/control",
                        json={"action": "start", "confirmation": "START"},
                    )

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(snapshots, 1)
                self.assertEqual(commands, [])
                self.assertEqual(database.get_job(parent.id).state, "queued")
                self.assertFalse(
                    any(
                        job.parent_job_id == parent.id
                        for job in database.dashboard().jobs
                    )
                )
                self.assertIsNone(database.get_printer(printer.id).active_job_id)

    def test_manual_start_reserves_a_child_and_conflicts_return_409(self) -> None:
        source = self.stored_file("manual-start.gcode")
        printer = self.ready_printer("Manual start")
        parent = database.create_job(
            JobCreate(
                name="Manual start",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        database.mark_job_delivered(parent.id, "manual-start.gcode")

        client = TestClient(app)
        started = client.post(
            f"/api/jobs/{parent.id}/control",
            json={"action": "start", "confirmation": "START"},
        )

        self.assertEqual(started.status_code, 200)
        child_id = started.json()["id"]
        self.assertEqual(started.json()["parent_job_id"], parent.id)
        self.assertEqual(database.get_job(parent.id).state, "queued")
        self.assertEqual(database.get_printer(printer.id).active_job_id, child_id)

        self.assertEqual(
            client.patch(
                f"/api/jobs/{parent.id}",
                json={"filename": "changed.gcode"},
            ).status_code,
            409,
        )
        self.assertEqual(client.delete(f"/api/jobs/{parent.id}").status_code, 409)
        self.assertEqual(
            client.patch(
                f"/api/jobs/{child_id}",
                json={"name": "Changed run"},
            ).status_code,
            409,
        )
        self.assertEqual(client.delete(f"/api/jobs/{child_id}").status_code, 409)

    def test_active_printer_blocks_retarget_connection_and_deletion(self) -> None:
        source = self.stored_file("locked-printer.gcode")
        printer = self.ready_printer("Locked printer")
        parent = database.create_job(
            JobCreate(
                name="Locked run",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        self.assertIsNotNone(run)
        client = TestClient(app)

        self.assertEqual(
            client.patch(
                f"/api/printers/{printer.id}",
                json={"host": "other-printer.local"},
            ).status_code,
            409,
        )
        self.assertEqual(
            client.post(
                f"/api/printers/{printer.id}/connection",
                json={"adapter_type": "simulator", "host": "other-simulator"},
            ).status_code,
            409,
        )
        self.assertEqual(
            client.delete(f"/api/printers/{printer.id}").status_code,
            409,
        )
        renamed = client.patch(
            f"/api/printers/{printer.id}",
            json={"name": "Locked printer renamed"},
        )
        self.assertEqual(renamed.status_code, 200)
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.host, printer.host)
        self.assertEqual(saved.active_job_id, run.id)

    def test_cancel_recovers_an_idle_starting_reservation_without_remote_command(self) -> None:
        source = self.stored_file("recover-idle.gcode")
        printer = self.ready_printer("Recover idle")
        parent = database.create_job(
            JobCreate(
                name="Recover idle",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return AdapterSnapshot(
                    state="ready",
                    percent_complete=None,
                    active_job=None,
                    bed_temp=None,
                    nozzle_temp=None,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(
                self,
                _host: str,
                _credential: dict[str, str],
                action: str,
                _remote_path: str | None,
            ) -> None:
                commands.append(action)

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ):
            response = TestClient(app).post(
                f"/api/jobs/{run.id}/control",
                json={"action": "cancel", "confirmation": "CANCEL"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(commands, [])
        self.assertEqual(database.get_job(run.id).state, "cancelled")
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.state, "attention")
        self.assertEqual(saved.clearance_state, "awaiting_confirmation")
        self.assertIsNone(saved.active_job_id)

    def test_cancel_starting_run_commands_matching_live_print_before_release(self) -> None:
        source = self.stored_file("recover-printing.gcode")
        printer = self.ready_printer("Recover printing")
        parent = database.create_job(
            JobCreate(
                name="Recover printing",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return AdapterSnapshot(
                    state="printing",
                    percent_complete=4,
                    active_job=run.filename,
                    bed_temp=60,
                    nozzle_temp=205,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(
                self,
                _host: str,
                _credential: dict[str, str],
                action: str,
                _remote_path: str | None,
            ) -> None:
                commands.append(action)

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ):
            response = TestClient(app).post(
                f"/api/jobs/{run.id}/control",
                json={"action": "cancel", "confirmation": "CANCEL"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(commands, ["cancel"])
        self.assertEqual(database.get_job(run.id).state, "cancelled")
        self.assertTrue(database.get_job(parent.id).dispatch_paused)
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.clearance_state, "awaiting_confirmation")
        self.assertIsNone(saved.active_job_id)

    def test_cancel_blocks_when_fresh_telemetry_reports_another_file(self) -> None:
        source = self.stored_file("owned.gcode")
        printer = self.ready_printer("Fresh control gate")
        parent = database.create_job(
            JobCreate(
                name="Fresh control gate",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        database.update_job(run.id, JobUpdate(state="running"))
        commands: list[str] = []

        class FakeAdapter:
            capabilities = AdapterCapabilities(
                telemetry=True,
                file_transfer=True,
                job_control=True,
                camera=False,
            )

            def snapshot(self, *_: object) -> AdapterSnapshot:
                return AdapterSnapshot(
                    state="printing",
                    percent_complete=25,
                    active_job="someone-elses-file.gcode",
                    bed_temp=60,
                    nozzle_temp=205,
                    note=None,
                    seen_at=datetime.now(UTC),
                )

            def command(self, *_: object) -> None:
                commands.append("cancel")

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ):
            response = TestClient(app).post(
                f"/api/jobs/{run.id}/control",
                json={"action": "cancel", "confirmation": "CANCEL"},
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(commands, [])
        self.assertEqual(database.get_job(run.id).state, "running")

    def test_reconcile_promotes_starting_run_when_printer_reports_matching_print(self) -> None:
        source = self.stored_file("reconcile-start.gcode")
        printer = self.ready_printer("Reconcile start")
        parent = database.create_job(
            JobCreate(
                name="Reconcile start",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        snapshot = AdapterSnapshot(
            state="printing",
            percent_complete=7,
            active_job=run.filename,
            bed_temp=60,
            nozzle_temp=205,
            note=None,
            seen_at=datetime.now(UTC),
        )

        class FakeAdapter:
            def snapshot(self, *_: object) -> AdapterSnapshot:
                return snapshot

        with patch("backend.app.main.get_adapter", return_value=FakeAdapter()), patch(
            "backend.app.main.load_secret",
            return_value={},
        ):
            refreshed = refresh_and_dispatch(printer.id)

        self.assertEqual(database.get_job(run.id).state, "running")
        self.assertTrue(database.get_job(run.id).start_observed)
        self.assertEqual(refreshed.state, "printing")
        self.assertEqual(refreshed.clearance_state, "occupied")
        self.assertEqual(refreshed.active_job_id, run.id)

    def test_stale_terminal_event_is_ignored_until_the_current_run_is_observed(self) -> None:
        source = self.stored_file("repeated.gcode", verified=True)
        printer = self.ready_printer("Repeated run")
        parent = database.create_job(
            JobCreate(
                name="Repeated run",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                repetitions=2,
                auto_clear=True,
            )
        )
        first = database.claim_job_for_auto_start(parent.id, printer.id)
        database.update_job(first.id, JobUpdate(state="running"))
        database.mark_run_start_observed(first.id)
        database.complete_job(first.id, trusted_auto_clear=True)
        second = database.claim_job_for_auto_start(parent.id, printer.id)

        stale_completion = AdapterSnapshot(
            state="ready",
            percent_complete=100,
            active_job=second.filename,
            bed_temp=30,
            nozzle_temp=35,
            note=None,
            seen_at=datetime.now(UTC),
            job_outcome="completed",
        )
        self.assertIsNone(
            reconcile_reported_job_outcome(printer.id, stale_completion)
        )
        self.assertEqual(database.get_job(second.id).state, "starting")
        self.assertFalse(database.get_job(second.id).start_observed)
        self.assertEqual(database.get_job(parent.id).completed_repetitions, 1)

        printing = AdapterSnapshot(
            state="printing",
            percent_complete=4,
            active_job=second.filename,
            bed_temp=60,
            nozzle_temp=205,
            note=None,
            seen_at=datetime.now(UTC),
        )
        observed = reconcile_reported_job_outcome(printer.id, printing)
        self.assertEqual(observed.state, "running")
        self.assertTrue(observed.start_observed)

        completed = AdapterSnapshot(
            state="ready",
            percent_complete=100,
            active_job=second.filename,
            bed_temp=30,
            nozzle_temp=35,
            note=None,
            seen_at=datetime.now(UTC),
            job_outcome="completed",
        )
        result = reconcile_reported_job_outcome(printer.id, completed)
        self.assertEqual(result.state, "completed")
        self.assertEqual(database.get_job(parent.id).completed_repetitions, 2)

    def test_terminal_without_file_identity_never_completes_an_observed_run(self) -> None:
        source = self.stored_file("identity-required.gcode", verified=True)
        printer = self.ready_printer("Identity required")
        parent = database.create_job(
            JobCreate(
                name="Identity required",
                filename=source.filename,
                file_id=source.id,
                target_printer_id=printer.id,
                auto_clear=True,
            )
        )
        run = database.claim_job_for_auto_start(parent.id, printer.id)
        database.update_job(run.id, JobUpdate(state="running"))
        database.mark_run_start_observed(run.id)
        ambiguous = AdapterSnapshot(
            state="ready",
            percent_complete=100,
            active_job=None,
            bed_temp=30,
            nozzle_temp=35,
            note="complete",
            seen_at=datetime.now(UTC),
            job_outcome="completed",
        )

        self.assertIsNone(reconcile_reported_job_outcome(printer.id, ambiguous))
        self.assertEqual(database.get_job(run.id).state, "running")
        saved = database.get_printer(printer.id)
        self.assertEqual(saved.active_job_id, run.id)
        self.assertNotEqual(saved.clearance_state, "auto_verified")
