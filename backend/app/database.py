from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .adapters import AdapterSnapshot
from .models import (
    ActivityRecord,
    DashboardResponse,
    FileCreate,
    FileRecord,
    JobCreate,
    JobRecord,
    JobUpdate,
    MaterialCreate,
    MaterialRecord,
    MaterialUpdate,
    PrinterCreate,
    PrinterRecord,
    PrinterUpdate,
)


DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
DATABASE_PATH = DATA_DIRECTORY / "fleet.db"
FILES_DIRECTORY = DATA_DIRECTORY / "files"
ACTIVE_RUN_STATES = ("assigned", "starting", "running", "paused")


class QueueConflictError(RuntimeError):
    """Raised when a queue mutation would violate an active-run invariant."""


def _printer_is_control_locked(printer: sqlite3.Row) -> bool:
    """Return whether changing this printer's control identity would be unsafe."""
    return bool(printer["active_job_id"]) or printer["state"] in {"starting", "printing"}


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE_PATH, timeout=10)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    database.execute("PRAGMA busy_timeout = 10000")
    try:
        yield database
        database.commit()
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


def _columns(database: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(database: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
    if name not in _columns(database, table):
        database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def initialize() -> None:
    with connection() as database:
        database.execute("PRAGMA journal_mode = WAL")
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS printers (
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
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              filename TEXT NOT NULL,
              target_printer_id TEXT,
              material TEXT NOT NULL DEFAULT '',
              estimated_minutes INTEGER NOT NULL DEFAULT 60,
              file_id TEXT,
              state TEXT NOT NULL,
              created_at TEXT NOT NULL,
              queue_position INTEGER,
              delivery_state TEXT NOT NULL DEFAULT 'not_ready',
              remote_file_path TEXT,
              delivered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS activity (
              id TEXT PRIMARY KEY,
              occurred_at TEXT NOT NULL,
              level TEXT NOT NULL,
              message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS materials (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              material_type TEXT NOT NULL,
              brand TEXT NOT NULL DEFAULT '',
              color TEXT NOT NULL DEFAULT '#8aaec9',
              remaining_percent INTEGER NOT NULL DEFAULT 100,
              spool_grams INTEGER NOT NULL DEFAULT 1000,
              assigned_printer_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              filename TEXT NOT NULL,
              file_type TEXT NOT NULL DEFAULT 'gcode',
              material TEXT NOT NULL DEFAULT '',
              estimated_minutes INTEGER,
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              stored_filename TEXT,
              size_bytes INTEGER,
              sha256 TEXT
            );
            """
        )
        for name, declaration in (
            ("location", "TEXT NOT NULL DEFAULT ''"),
            ("camera_url", "TEXT NOT NULL DEFAULT ''"),
            ("material", "TEXT NOT NULL DEFAULT ''"),
            ("supported_materials", "TEXT NOT NULL DEFAULT '[]'"),
            ("nozzle_diameter", "REAL NOT NULL DEFAULT 0.4"),
            ("tags", "TEXT NOT NULL DEFAULT '[]'"),
            ("filament_system", "TEXT NOT NULL DEFAULT 'single'"),
            ("loaded_filaments", "TEXT NOT NULL DEFAULT '[]'"),
            ("auto_start_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("material_color", "TEXT NOT NULL DEFAULT '#8aaec9'"),
            ("bed_temp", "INTEGER"),
            ("nozzle_temp", "INTEGER"),
            ("run_hours", "INTEGER NOT NULL DEFAULT 0"),
            ("maintenance_due_hours", "INTEGER NOT NULL DEFAULT 250"),
            ("favorite", "INTEGER NOT NULL DEFAULT 0"),
            ("credential_id", "TEXT"),
            ("connection_state", "TEXT NOT NULL DEFAULT 'unconfigured'"),
            ("connection_message", "TEXT"),
            ("last_refreshed_at", "TEXT"),
            ("capabilities", "TEXT NOT NULL DEFAULT '[]'"),
            ("active_job_id", "TEXT"),
            ("clearance_state", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("last_terminal_fingerprint", "TEXT"),
            ("acknowledged_terminal_fingerprint", "TEXT"),
            ("connection_endpoint", "TEXT"),
            ("connection_endpoint_keys", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            _add_column_if_missing(database, "printers", name, declaration)
        job_columns = _columns(database, "jobs")
        for name, declaration in (
            ("file_id", "TEXT"),
            ("delivery_state", "TEXT NOT NULL DEFAULT 'not_ready'"),
            ("remote_file_path", "TEXT"),
            ("delivered_at", "TEXT"),
            ("repetitions", "INTEGER"),
            ("loop_mode", "TEXT NOT NULL DEFAULT 'sticky'"),
            ("auto_restart", "INTEGER NOT NULL DEFAULT 0"),
            ("auto_clear", "INTEGER NOT NULL DEFAULT 0"),
            ("dispatch_paused", "INTEGER NOT NULL DEFAULT 0"),
            ("completed_repetitions", "INTEGER NOT NULL DEFAULT 0"),
            ("parent_job_id", "TEXT"),
            ("run_number", "INTEGER"),
            ("start_observed", "INTEGER NOT NULL DEFAULT 0"),
            ("required_printer_manufacturer", "TEXT NOT NULL DEFAULT ''"),
            ("required_printer_model", "TEXT NOT NULL DEFAULT ''"),
            ("required_nozzle_diameter", "REAL"),
            ("required_filaments", "TEXT NOT NULL DEFAULT '[]'"),
            ("required_tags", "TEXT NOT NULL DEFAULT '[]'"),
            ("tag_match_mode", "TEXT NOT NULL DEFAULT 'all'"),
            ("allowed_printer_ids", "TEXT NOT NULL DEFAULT '[]'"),
            ("auto_switch_filament", "INTEGER NOT NULL DEFAULT 0"),
            ("override_existing_file", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in job_columns:
                _add_column_if_missing(database, "jobs", name, declaration)
                if name == "repetitions":
                    # Existing jobs predate the looping feature. Keep their
                    # original one-run behavior instead of treating NULL as
                    # an intentional infinite loop.
                    database.execute("UPDATE jobs SET repetitions = 1 WHERE repetitions IS NULL")
        for name, declaration in (
            ("stored_filename", "TEXT"),
            ("size_bytes", "INTEGER"),
            ("sha256", "TEXT"),
            ("auto_eject_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("estimated_filament_grams", "REAL"),
            ("printer_manufacturer", "TEXT NOT NULL DEFAULT ''"),
            ("printer_model", "TEXT NOT NULL DEFAULT ''"),
            ("nozzle_diameter", "REAL"),
            ("filament_profiles", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            _add_column_if_missing(database, "files", name, declaration)
        # Older builds did not persist resolved endpoint identities. Fail
        # closed on upgrade: these network records must pass verified
        # connection setup once before polling, READY, or commands resume.
        database.execute(
            """UPDATE printers SET connection_state = 'unconfigured',
              state = 'attention', clearance_state = 'unknown',
              capabilities = '[]', auto_start_enabled = 0,
              connection_message = ?
              WHERE adapter_type IN ('octoprint', 'moonraker', 'prusalink')
              AND host != ''
              AND COALESCE(connection_endpoint_keys, '[]') IN ('', '[]')""",
            (
                "Reconnect once to establish a verified unique physical endpoint identity.",
            ),
        )
        database.execute("CREATE INDEX IF NOT EXISTS jobs_position_idx ON jobs(state, queue_position)")
        database.execute("CREATE INDEX IF NOT EXISTS jobs_parent_state_idx ON jobs(parent_job_id, state)")
        database.execute("CREATE INDEX IF NOT EXISTS activity_time_idx ON activity(occurred_at DESC)")


def invalidate_unattended_clearance_on_startup() -> int:
    """Require one physical READY check after every hub restart.

    A print can begin and finish while this process is offline, and polling
    APIs do not all expose a durable event cursor. Persisted clearance is
    therefore not safe to reuse across a monitoring outage.
    """
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            "UPDATE printers SET auto_start_enabled = 0 WHERE auto_start_enabled = 1"
        )
        disarmed = int(database.execute("SELECT changes()").fetchone()[0])
        database.execute(
            """UPDATE printers SET state = 'attention', clearance_state = 'unknown',
              connection_message = ?
              WHERE active_job_id IS NULL
              AND clearance_state IN ('confirmed', 'auto_verified')""",
            (
                "Mirdain restarted. Inspect the bed and confirm READY before unattended dispatch.",
            ),
        )
        changed = int(database.execute("SELECT changes()").fetchone()[0])
        if changed or disarmed:
            _activity(
                database,
                "warning",
                (
                    f"Mirdain restarted; {changed} connected printer(s) need a "
                    f"fresh READY confirmation and {disarmed} automation "
                    "switch(es) were disarmed."
                ),
            )
        return changed


def _now() -> datetime:
    return datetime.now(UTC)


def _activity(database: sqlite3.Connection, level: str, message: str) -> None:
    database.execute(
        "INSERT INTO activity (id, occurred_at, level, message) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), _now().isoformat(), level, message),
    )


_JSON_LIST_FIELDS = {
    "allowed_printer_ids",
    "capabilities",
    "filament_profiles",
    "loaded_filaments",
    "required_filaments",
    "required_tags",
    "supported_materials",
    "tags",
}


def _decode_json_list(value: Any) -> list[str]:
    """Decode list columns written by this and pre-list schema versions.

    Early development databases sometimes contain a plain comma-separated
    value. Accepting it here makes migrations non-destructive while all new
    writes continue to use canonical JSON arrays.
    """
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, list):
        return [str(item) for item in decoded if item is not None]
    if isinstance(decoded, str):
        return [decoded]
    raw = str(value).strip()
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else []


def _encode_json_lists(values: dict[str, Any]) -> None:
    for name in _JSON_LIST_FIELDS.intersection(values):
        values[name] = json.dumps(values[name] or [], ensure_ascii=False)


def _record(row: sqlite3.Row | None, model: type[Any]) -> Any:
    if not row:
        return None
    values = dict(row)
    for name in (
        "favorite",
        "auto_start_enabled",
        "auto_restart",
        "auto_clear",
        "dispatch_paused",
        "start_observed",
        "auto_eject_verified",
        "auto_switch_filament",
        "override_existing_file",
    ):
        if name in values:
            values[name] = bool(values[name])
    for name in _JSON_LIST_FIELDS.intersection(values):
        values[name] = _decode_json_list(values[name])
    return model(**values)


def create_printer(payload: PrinterCreate) -> PrinterRecord:
    record = PrinterRecord(id=str(uuid.uuid4()), state="setup", **payload.model_dump())
    with connection() as database:
        database.execute(
            """INSERT INTO printers (
              id, name, manufacturer, model, adapter_type, host, camera_url, location, state,
              material, material_color, supported_materials, nozzle_diameter, tags, filament_system,
              loaded_filaments, auto_start_enabled, run_hours, maintenance_due_hours, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.manufacturer, record.model, record.adapter_type,
                record.host, record.camera_url, record.location, record.state, record.material, record.material_color,
                json.dumps(record.supported_materials), record.nozzle_diameter, json.dumps(record.tags),
                record.filament_system, json.dumps(record.loaded_filaments), int(record.auto_start_enabled),
                record.run_hours, record.maintenance_due_hours, record.note,
            ),
        )
        _activity(database, "info", f"{record.name} was added and is awaiting local connection setup.")
    return record


def get_printer(printer_id: str) -> PrinterRecord | None:
    with connection() as database:
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def update_printer(printer_id: str, payload: PrinterUpdate) -> PrinterRecord | None:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return get_printer(printer_id)
    if {"host", "adapter_type"}.intersection(changes):
        raise QueueConflictError(
            "Printer connection identity must be changed through verified connection setup."
        )
    if changes.get("nozzle_diameter") is None and "nozzle_diameter" in changes:
        changes["nozzle_diameter"] = 0.4
    if changes.get("filament_system") is None and "filament_system" in changes:
        changes["filament_system"] = "single"
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        control_fields = {
            "active_job",
            "adapter_type",
            "filament_system",
            "host",
            "loaded_filaments",
            "manufacturer",
            "material",
            "model",
            "nozzle_diameter",
            "percent_complete",
            "state",
            "supported_materials",
            "tags",
        }
        if _printer_is_control_locked(current) and control_fields.intersection(changes):
            raise QueueConflictError(
                "This printer owns active work; finish or cancel it before changing control or connection fields."
            )
        current_values = dict(current)
        next_values = {**current_values, **changes}
        if "favorite" in next_values:
            next_values["favorite"] = int(bool(next_values["favorite"]))
        if "auto_start_enabled" in changes:
            next_values["auto_start_enabled"] = int(bool(next_values["auto_start_enabled"]))
        encoded_changes = dict(changes)
        _encode_json_lists(encoded_changes)
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [encoded_changes.get(field, next_values[field]) for field in changes]
        database.execute(f"UPDATE printers SET {assignments} WHERE id = ?", (*values, printer_id))
        if "state" in changes and changes["state"] != current_values["state"]:
            _activity(database, "warning" if changes["state"] == "attention" else "info", f"{next_values['name']} is now {changes['state']}.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def mark_printer_ready(printer_id: str) -> PrinterRecord | None:
    """Apply an operator-confirmed ready transition without releasing live work."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        active = None
        if current["active_job_id"]:
            active = database.execute(
                "SELECT state FROM jobs WHERE id = ?",
                (current["active_job_id"],),
            ).fetchone()
        if current["state"] in {"starting", "printing"} or (
            active and active["state"] in {"starting", "running", "paused"}
        ):
            return _record(current, PrinterRecord)
        database.execute(
            """UPDATE printers SET state = 'ready', percent_complete = NULL,
              active_job = NULL, active_job_id = NULL, connection_message = NULL,
              clearance_state = 'confirmed',
              acknowledged_terminal_fingerprint = last_terminal_fingerprint
              WHERE id = ?""",
            (printer_id,),
        )
        if current["state"] != "ready" or current["active_job"] or current["active_job_id"]:
            _activity(database, "success", f"{current['name']} was confirmed ready by an operator.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def _snapshot_projection(
    current: sqlite3.Row,
    snapshot: AdapterSnapshot,
) -> tuple[str, str, str | None, str | None]:
    """Project hardware telemetry without letting generic idle clear the bed gate."""
    clearance = str(current["clearance_state"] or "unknown")
    state = snapshot.state
    message = snapshot.note
    active_job = snapshot.active_job or (
        str(current["active_job"]) if current["active_job_id"] and current["active_job"] else None
    )
    if snapshot.state in {"starting", "printing"}:
        clearance = "occupied"
    elif snapshot.state == "ready":
        if current["active_job_id"]:
            state = "attention"
            message = "The printer still owns an active run; review its outcome before marking READY."
        elif (
            snapshot.job_outcome in {"completed", "cancelled", "failed"}
            and _terminal_fingerprint(snapshot)
            != current["acknowledged_terminal_fingerprint"]
        ):
            state = "attention"
            clearance = "awaiting_confirmation"
            message = "An unowned print outcome was detected; inspect the bed and confirm READY."
        elif clearance not in {"confirmed", "auto_verified"}:
            state = "attention"
            message = "Remove the print, inspect the bed, and confirm READY before another job."
    return state, clearance, active_job, message


def _terminal_fingerprint(snapshot: AdapterSnapshot) -> str | None:
    if snapshot.job_outcome not in {"completed", "cancelled", "failed"}:
        return None
    filename = Path(snapshot.active_job).name.casefold() if snapshot.active_job else ""
    return f"{snapshot.job_outcome}:{filename}"


def _snapshot_terminal_values(
    current: sqlite3.Row,
    snapshot: AdapterSnapshot,
) -> tuple[str | None, str | None]:
    last = current["last_terminal_fingerprint"]
    acknowledged = current["acknowledged_terminal_fingerprint"]
    terminal = _terminal_fingerprint(snapshot)
    if terminal:
        last = terminal
    if snapshot.state in {"starting", "printing"}:
        acknowledged = None
    return last, acknowledged


def delete_printer(printer_id: str) -> str | None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        printer = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not printer:
            return None
        if _printer_is_control_locked(printer):
            raise QueueConflictError(
                "This printer owns active work; finish or cancel it before removing the printer."
            )
        database.execute("UPDATE jobs SET target_printer_id = NULL WHERE target_printer_id = ?", (printer_id,))
        database.execute("UPDATE materials SET assigned_printer_id = NULL WHERE assigned_printer_id = ?", (printer_id,))
        database.execute("DELETE FROM printers WHERE id = ?", (printer_id,))
        _activity(database, "info", f"{printer['name']} was removed from the fleet.")
        return str(printer["name"])


def credential_id_for_printer(printer_id: str) -> str | None:
    with connection() as database:
        row = database.execute("SELECT credential_id FROM printers WHERE id = ?", (printer_id,)).fetchone()
        return str(row["credential_id"]) if row and row["credential_id"] else None


def network_connection_identities() -> list[dict[str, Any]]:
    with connection() as database:
        rows = database.execute(
            """SELECT id, name, adapter_type, host, connection_endpoint_keys
              FROM printers
              WHERE adapter_type IN ('octoprint', 'moonraker', 'prusalink')
              AND host != ''"""
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "adapter_type": str(row["adapter_type"]),
                "host": str(row["host"]),
                "keys": _decode_json_list(row["connection_endpoint_keys"]),
            }
            for row in rows
        ]


def invalidate_connection_identity(printer_id: str, message: str) -> None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            """UPDATE printers SET connection_state = 'unconfigured',
              state = 'attention',
              auto_start_enabled = 0,
              clearance_state = CASE
                WHEN active_job_id IS NULL THEN 'unknown'
                ELSE 'occupied'
              END,
              capabilities = '[]', connection_message = ?
              WHERE id = ?""",
            (message, printer_id),
        )


def connection_identity_matches(
    printer_id: str,
    endpoint_keys: list[str],
) -> bool:
    """Compare resolved identity without performing the caller's safety write."""
    current_keys = set(endpoint_keys)
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute(
            """SELECT connection_endpoint_keys FROM printers WHERE id = ?""",
            (printer_id,),
        ).fetchone()
        if not current:
            return False
        stored_keys = set(_decode_json_list(current["connection_endpoint_keys"]))
        collision = False
        if current_keys:
            for other in database.execute(
                """SELECT connection_endpoint_keys FROM printers
                  WHERE id != ?""",
                (printer_id,),
            ).fetchall():
                if current_keys.intersection(
                    _decode_json_list(other["connection_endpoint_keys"])
                ):
                    collision = True
                    break
        return stored_keys == current_keys and bool(current_keys) and not collision


def configure_connection(
    printer_id: str,
    *,
    adapter_type: str,
    host: str,
    credential_id: str | None,
    capabilities: list[str],
    snapshot: AdapterSnapshot,
    connection_endpoint: str | None = None,
    connection_endpoint_keys: list[str] | None = None,
) -> PrinterRecord | None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        if _printer_is_control_locked(current):
            raise QueueConflictError(
                "This printer owns active work; finish or cancel it before changing its connection."
            )
        endpoint_keys = set(connection_endpoint_keys or [])
        if endpoint_keys:
            duplicate = None
            for other in database.execute(
                """SELECT name, connection_endpoint, connection_endpoint_keys
                  FROM printers WHERE id != ?""",
                (printer_id,),
            ).fetchall():
                other_keys = set(
                    _decode_json_list(other["connection_endpoint_keys"])
                )
                if other["connection_endpoint"]:
                    other_keys.add(str(other["connection_endpoint"]))
                if endpoint_keys.intersection(other_keys):
                    duplicate = other
                    break
            if duplicate:
                raise QueueConflictError(
                    f"This physical endpoint is already connected as {duplicate['name']}."
                )
        identity_changed = (
            current["adapter_type"] != adapter_type
            or current["host"] != host
            or current["credential_id"] != credential_id
        )
        physical_endpoint_changed = (
            current["adapter_type"] != adapter_type
            or current["host"] != host
            or current["connection_endpoint"] != connection_endpoint
            or set(_decode_json_list(current["connection_endpoint_keys"]))
            != endpoint_keys
        )
        if physical_endpoint_changed:
            active_job = snapshot.active_job
            last_terminal = _terminal_fingerprint(snapshot)
            acknowledged_terminal = None
            if snapshot.state in {"starting", "printing"}:
                projected_state = snapshot.state
                clearance = "occupied"
                message = snapshot.note
            else:
                projected_state = "attention"
                clearance = "unknown"
                message = (
                    "This is a new physical endpoint. Inspect the bed and confirm READY."
                )
        else:
            projected_state, clearance, active_job, message = _snapshot_projection(current, snapshot)
            last_terminal, acknowledged_terminal = _snapshot_terminal_values(current, snapshot)
        database.execute(
            """UPDATE printers SET
              adapter_type = ?, host = ?, credential_id = ?, connection_state = 'connected',
              auto_start_enabled = CASE WHEN ? THEN 0 ELSE auto_start_enabled END,
              connection_message = ?, last_refreshed_at = ?, capabilities = ?, state = ?,
              percent_complete = ?, active_job = ?, bed_temp = ?, nozzle_temp = ?, last_seen_at = ?,
              note = ?, clearance_state = ?, last_terminal_fingerprint = ?,
              acknowledged_terminal_fingerprint = ?, connection_endpoint = ?
              , connection_endpoint_keys = ?
              WHERE id = ?""",
            (
                adapter_type, host, credential_id, int(physical_endpoint_changed),
                message, snapshot.seen_at.isoformat(),
                json.dumps(capabilities), projected_state, snapshot.percent_complete, active_job,
                snapshot.bed_temp, snapshot.nozzle_temp, snapshot.seen_at.isoformat(), snapshot.note or "",
                clearance, last_terminal, acknowledged_terminal,
                connection_endpoint, json.dumps(sorted(endpoint_keys)), printer_id,
            ),
        )
        if identity_changed:
            database.execute(
                """UPDATE jobs SET
                  delivery_state = CASE WHEN file_id IS NULL THEN 'not_ready' ELSE 'ready' END,
                  remote_file_path = NULL, delivered_at = NULL
                  WHERE target_printer_id = ? AND parent_job_id IS NULL
                  AND state != 'completed'""",
                (printer_id,),
            )
        _activity(database, "success", f"{current['name']} connected through {adapter_type}.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def apply_snapshot(printer_id: str, snapshot: AdapterSnapshot) -> PrinterRecord | None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        projected_state, clearance, active_job, message = _snapshot_projection(current, snapshot)
        last_terminal, acknowledged_terminal = _snapshot_terminal_values(current, snapshot)
        database.execute(
            """UPDATE printers SET connection_state = 'connected', connection_message = ?,
              last_refreshed_at = ?, state = ?, percent_complete = ?, active_job = ?,
              bed_temp = ?, nozzle_temp = ?, last_seen_at = ?, note = ?,
              clearance_state = ?, last_terminal_fingerprint = ?,
              acknowledged_terminal_fingerprint = ? WHERE id = ?""",
            (
                message, snapshot.seen_at.isoformat(), projected_state, snapshot.percent_complete,
                active_job, snapshot.bed_temp, snapshot.nozzle_temp, snapshot.seen_at.isoformat(),
                snapshot.note or "", clearance, last_terminal,
                acknowledged_terminal, printer_id,
            ),
        )
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def prestart_snapshot_is_safe(
    printer_id: str,
    job_id: str,
    snapshot: AdapterSnapshot,
    *,
    baseline_active_file: str | None,
) -> bool:
    """Validate fresh hardware state while a starting child owns the record."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        printer = database.execute(
            "SELECT * FROM printers WHERE id = ?",
            (printer_id,),
        ).fetchone()
        job = database.execute(
            "SELECT state FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if (
            not printer
            or not job
            or job["state"] != "starting"
            or printer["active_job_id"] != job_id
        ):
            return False
        terminal = _terminal_fingerprint(snapshot)
        terminal_is_new = bool(
            terminal
            and terminal != printer["acknowledged_terminal_fingerprint"]
        )
        reported_changed = False
        if snapshot.active_job and not terminal:
            reported_changed = (
                not baseline_active_file
                or Path(snapshot.active_job).name.casefold()
                != Path(baseline_active_file).name.casefold()
            )
        safe = (
            snapshot.state == "ready"
            and not terminal_is_new
            and not reported_changed
        )
        database.execute(
            """UPDATE printers SET connection_state = 'connected',
              last_refreshed_at = ?, last_seen_at = ?, bed_temp = ?,
              nozzle_temp = ?, note = ?, last_terminal_fingerprint = ?
              WHERE id = ? AND active_job_id = ?""",
            (
                snapshot.seen_at.isoformat(),
                snapshot.seen_at.isoformat(),
                snapshot.bed_temp,
                snapshot.nozzle_temp,
                snapshot.note or "",
                terminal or printer["last_terminal_fingerprint"],
                printer_id,
                job_id,
            ),
        )
        if not safe:
            database.execute(
                """UPDATE printers SET state = 'attention',
                  clearance_state = 'awaiting_confirmation',
                  connection_message = ?
                  WHERE id = ? AND active_job_id = ?""",
                (
                    "Fresh pre-start telemetry changed; inspect the bed and confirm READY.",
                    printer_id,
                    job_id,
                ),
            )
        return safe


def mark_connection_error(printer_id: str, message: str) -> PrinterRecord | None:
    with connection() as database:
        current = database.execute(
            """SELECT name, connection_state, connection_message, active_job_id
              FROM printers WHERE id = ?""",
            (printer_id,),
        ).fetchone()
        if not current:
            return None
        stored_message = message
        if current["active_job_id"]:
            database.execute(
                """UPDATE printers SET connection_state = 'error',
                  auto_start_enabled = 0, connection_message = ? WHERE id = ?""",
                (message, printer_id),
            )
        else:
            stored_message = (
                f"{message} Monitoring was interrupted; inspect the bed and confirm READY after reconnecting."
            )
            database.execute(
                """UPDATE printers SET connection_state = 'error', state = 'attention',
                  auto_start_enabled = 0, clearance_state = 'unknown',
                  connection_message = ? WHERE id = ?""",
                (
                    stored_message,
                    printer_id,
                ),
            )
        if (
            current["connection_state"] != "error"
            or current["connection_message"] != stored_message
        ):
            _activity(database, "warning", f"{current['name']} connection needs attention.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def connected_printers() -> list[PrinterRecord]:
    with connection() as database:
        rows = database.execute(
            """SELECT * FROM printers
              WHERE connection_state IN ('connected', 'error')
              AND adapter_type NOT IN ('manual', 'bambu_lan')
              AND host != ''
              ORDER BY name COLLATE NOCASE"""
        )
        return [_record(row, PrinterRecord) for row in rows]


def active_job_for_printer(printer_id: str) -> JobRecord | None:
    with connection() as database:
        printer = database.execute("SELECT active_job_id FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not printer or not printer["active_job_id"]:
            return None
        return _record(
            database.execute("SELECT * FROM jobs WHERE id = ?", (printer["active_job_id"],)).fetchone(),
            JobRecord,
        )


def mark_run_start_observed(job_id: str) -> JobRecord | None:
    """Persist that telemetry observed this specific child actively printing."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        if current["state"] not in {"starting", "running", "paused"}:
            return _record(current, JobRecord)
        database.execute(
            "UPDATE jobs SET start_observed = 1 WHERE id = ?",
            (job_id,),
        )
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def _normalize_queue(database: sqlite3.Connection) -> None:
    queued = database.execute(
        """SELECT id FROM jobs
          WHERE state = 'queued' AND parent_job_id IS NULL
          ORDER BY queue_position, created_at, id"""
    ).fetchall()
    for position, row in enumerate(queued, start=1):
        database.execute("UPDATE jobs SET queue_position = ? WHERE id = ?", (position, row["id"]))


def job_has_active_runs(job_id: str) -> bool:
    placeholders = ", ".join("?" for _ in ACTIVE_RUN_STATES)
    with connection() as database:
        count = database.execute(
            f"SELECT COUNT(*) FROM jobs WHERE parent_job_id = ? AND state IN ({placeholders})",
            (job_id, *ACTIVE_RUN_STATES),
        ).fetchone()[0]
        return bool(count)


def create_job(payload: JobCreate) -> JobRecord:
    created_at = _now()
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        position = database.execute(
            """SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs
              WHERE state = 'queued' AND parent_job_id IS NULL"""
        ).fetchone()[0]
        record = JobRecord(id=str(uuid.uuid4()), state="queued", created_at=created_at, queue_position=position, **payload.model_dump())
        database.execute(
            """INSERT INTO jobs (
              id, name, filename, target_printer_id, material, estimated_minutes, file_id,
              repetitions, loop_mode, auto_restart, auto_clear, dispatch_paused,
              completed_repetitions, parent_job_id, run_number,
              required_printer_manufacturer, required_printer_model,
              required_nozzle_diameter, required_filaments,
              required_tags, tag_match_mode, allowed_printer_ids, auto_switch_filament,
              override_existing_file,
              state, created_at, queue_position, delivery_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.filename, record.target_printer_id, record.material,
                record.estimated_minutes, record.file_id, record.repetitions, record.loop_mode,
                int(record.auto_restart), int(record.auto_clear), int(record.dispatch_paused),
                record.completed_repetitions, None, None,
                record.required_printer_manufacturer, record.required_printer_model,
                record.required_nozzle_diameter,
                json.dumps(record.required_filaments), json.dumps(record.required_tags),
                record.tag_match_mode, json.dumps(record.allowed_printer_ids),
                int(record.auto_switch_filament), int(record.override_existing_file),
                record.state, record.created_at.isoformat(), record.queue_position,
                "ready" if record.file_id else "not_ready",
            ),
        )
        _activity(database, "info", f"{record.name} was added to the production queue.")
    return record


def get_job(job_id: str) -> JobRecord | None:
    with connection() as database:
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def _job_auto_eject_verified(database: sqlite3.Connection, job: dict[str, Any]) -> bool:
    file_id = job.get("file_id")
    if not file_id:
        return False
    row = database.execute("SELECT auto_eject_verified FROM files WHERE id = ?", (file_id,)).fetchone()
    return bool(row and row["auto_eject_verified"])


def _active_child_count(database: sqlite3.Connection, parent_job_id: str) -> int:
    placeholders = ", ".join("?" for _ in ACTIVE_RUN_STATES)
    return int(
        database.execute(
            f"SELECT COUNT(*) FROM jobs WHERE parent_job_id = ? AND state IN ({placeholders})",
            (parent_job_id, *ACTIVE_RUN_STATES),
        ).fetchone()[0]
    )


RUN_DEFINITION_FIELDS = {
    "allowed_printer_ids",
    "auto_clear",
    "auto_restart",
    "auto_switch_filament",
    "estimated_minutes",
    "file_id",
    "filename",
    "loop_mode",
    "material",
    "name",
    "override_existing_file",
    "repetitions",
    "required_filaments",
    "required_nozzle_diameter",
    "required_printer_manufacturer",
    "required_printer_model",
    "required_tags",
    "tag_match_mode",
    "target_printer_id",
}


def update_job(job_id: str, payload: JobUpdate) -> JobRecord | None:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        with connection() as database:
            return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)
    for name in ("required_printer_manufacturer", "required_printer_model"):
        if changes.get(name) is None and name in changes:
            changes[name] = ""
    if changes.get("tag_match_mode") is None and "tag_match_mode" in changes:
        changes["tag_match_mode"] = "all"
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        current_values = dict(current)
        parent_job_id = current_values.get("parent_job_id")
        if parent_job_id:
            non_state_changes = set(changes) - {"state"}
            if non_state_changes:
                raise QueueConflictError("Individual run records are immutable; update the queued parent task instead.")
            if changes.get("state") in {"queued", "completed", "failed", "cancelled"}:
                raise QueueConflictError("Use the confirmed run controls to finish or cancel an individual run.")
        else:
            active_children = _active_child_count(database, job_id)
            if active_children:
                definition_changes = RUN_DEFINITION_FIELDS.intersection(changes)
                revocation_fields = {"auto_clear", "auto_restart"}
                safe_revocation = (
                    definition_changes.issubset(revocation_fields)
                    and all(changes[field] is False for field in definition_changes)
                )
                if (
                    {"queue_position", "state"}.intersection(changes)
                    or (definition_changes and not safe_revocation)
                ):
                    raise QueueConflictError(
                        "This queue task has active runs and cannot be changed yet. "
                        "Automatic restart/clearance may only be revoked while it runs."
                    )
        next_values = {**current_values, **changes}
        if (
            ("file_id" in changes and changes["file_id"] != current_values.get("file_id"))
            or ("target_printer_id" in changes and changes["target_printer_id"] != current_values.get("target_printer_id"))
        ):
            next_values["delivery_state"] = "ready" if next_values.get("file_id") else "not_ready"
            next_values["remote_file_path"] = None
            next_values["delivered_at"] = None
            changes.update({
                "delivery_state": next_values["delivery_state"],
                "remote_file_path": None,
                "delivered_at": None,
            })
        for name in (
            "auto_restart",
            "auto_clear",
            "dispatch_paused",
            "auto_switch_filament",
            "override_existing_file",
        ):
            if name in changes:
                next_values[name] = int(bool(next_values[name]))
        old_state = current_values["state"]
        new_state = next_values["state"]
        if (
            not parent_job_id
            and new_state == "queued"
            and "repetitions" in changes
            and next_values["repetitions"] is not None
            and int(current_values.get("completed_repetitions") or 0) >= int(next_values["repetitions"])
        ):
            new_state = "completed"
            next_values["state"] = new_state
            changes["state"] = new_state
        if new_state == "queued" and old_state != "queued":
            next_values["queue_position"] = database.execute(
                """SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs
                  WHERE state = 'queued' AND parent_job_id IS NULL"""
            ).fetchone()[0]
            changes["queue_position"] = next_values["queue_position"]
        if new_state != "queued":
            next_values["queue_position"] = None
            changes["queue_position"] = None
        if new_state == "queued" and "queue_position" in changes:
            desired = max(1, int(next_values["queue_position"] or 1))
            database.execute(
                """UPDATE jobs SET queue_position = queue_position + 1
                  WHERE state = 'queued' AND parent_job_id IS NULL
                  AND id != ? AND queue_position >= ?""",
                (job_id, desired),
            )
            next_values["queue_position"] = desired
            changes["queue_position"] = desired
        encoded_values = {field: next_values[field] for field in changes}
        _encode_json_lists(encoded_values)
        assignments = ", ".join(f"{field} = ?" for field in changes)
        database.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*[encoded_values[field] for field in changes], job_id))
        if not parent_job_id and changes.get("auto_clear") is False:
            database.execute(
                """UPDATE jobs SET auto_clear = 0
                  WHERE parent_job_id = ?
                  AND state IN ('assigned', 'starting', 'running', 'paused')""",
                (job_id,),
            )
        _normalize_queue(database)
        printer_id = next_values.get("target_printer_id")
        if printer_id and new_state == "running":
            database.execute(
                """UPDATE printers SET state = 'printing', active_job = ?, active_job_id = ?,
                  percent_complete = 0, clearance_state = 'occupied'
                  WHERE id = ? AND (active_job_id IS NULL OR active_job_id = ?)""",
                (next_values["filename"], job_id, printer_id, job_id),
            )
            if database.execute("SELECT changes()").fetchone()[0] != 1:
                raise QueueConflictError("Another active run already owns this printer.")
            _activity(database, "success", f"{next_values['name']} was marked as printing.")
        elif printer_id and new_state == "paused":
            database.execute(
                """UPDATE printers SET state = 'attention', active_job_id = COALESCE(active_job_id, ?),
                  clearance_state = 'occupied' WHERE id = ?
                  AND (active_job_id IS NULL OR active_job_id = ?)""",
                (job_id, printer_id, job_id),
            )
            if database.execute("SELECT changes()").fetchone()[0] != 1:
                raise QueueConflictError("Another active run already owns this printer.")
            _activity(database, "warning", f"{next_values['name']} was paused.")
        elif printer_id and old_state in {"running", "paused"} and new_state in {"completed", "failed", "cancelled"}:
            database.execute(
                """UPDATE printers SET state = 'attention', active_job = NULL, active_job_id = NULL,
                  percent_complete = NULL, connection_message = ?,
                  clearance_state = 'awaiting_confirmation'
                  WHERE id = ? AND (active_job_id IS NULL OR active_job_id = ?)""",
                (
                    "Remove the print, inspect the printer, and confirm READY before another job.",
                    printer_id,
                    job_id,
                ),
            )
            _activity(database, "success" if new_state == "completed" else "warning", f"{next_values['name']} was marked {new_state}.")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def delete_job(job_id: str) -> str | None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        job = database.execute("SELECT name, parent_job_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return None
        if job["parent_job_id"]:
            return None
        if database.execute("SELECT 1 FROM jobs WHERE parent_job_id = ? LIMIT 1", (job_id,)).fetchone():
            return None
        database.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        _normalize_queue(database)
        _activity(database, "info", f"{job['name']} was removed from the queue.")
        return str(job["name"])


def create_material(payload: MaterialCreate) -> MaterialRecord:
    now = _now()
    record = MaterialRecord(id=str(uuid.uuid4()), created_at=now, updated_at=now, **payload.model_dump())
    with connection() as database:
        database.execute(
            """INSERT INTO materials (id, name, material_type, brand, color, remaining_percent, spool_grams, assigned_printer_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.name, record.material_type, record.brand, record.color, record.remaining_percent, record.spool_grams, record.assigned_printer_id, now.isoformat(), now.isoformat()),
        )
        _activity(database, "success", f"{record.name} was added to materials.")
    return record


def update_material(material_id: str, payload: MaterialUpdate) -> MaterialRecord | None:
    changes = payload.model_dump(exclude_unset=True)
    with connection() as database:
        current = database.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        if not current:
            return None
        if changes:
            changes["updated_at"] = _now().isoformat()
            assignments = ", ".join(f"{field} = ?" for field in changes)
            database.execute(f"UPDATE materials SET {assignments} WHERE id = ?", (*changes.values(), material_id))
        return _record(database.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone(), MaterialRecord)


def delete_material(material_id: str) -> str | None:
    with connection() as database:
        material = database.execute("SELECT name FROM materials WHERE id = ?", (material_id,)).fetchone()
        if not material:
            return None
        database.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        _activity(database, "info", f"{material['name']} was removed from materials.")
        return str(material["name"])


def create_file(
    payload: FileCreate,
    *,
    stored_filename: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> FileRecord:
    now = _now()
    record = FileRecord(
        id=str(uuid.uuid4()), created_at=now, stored_filename=stored_filename,
        size_bytes=size_bytes, sha256=sha256, **payload.model_dump(),
    )
    with connection() as database:
        database.execute(
            """INSERT INTO files (
              id, name, filename, file_type, material, estimated_minutes, notes,
              auto_eject_verified, estimated_filament_grams, printer_manufacturer,
              printer_model, nozzle_diameter,
              filament_profiles, created_at, stored_filename, size_bytes, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.filename, record.file_type, record.material,
                record.estimated_minutes, record.notes, int(record.auto_eject_verified), record.estimated_filament_grams,
                record.printer_manufacturer, record.printer_model, record.nozzle_diameter,
                json.dumps(record.filament_profiles),
                now.isoformat(), record.stored_filename, record.size_bytes, record.sha256,
            ),
        )
        _activity(database, "info", f"{record.name} was added to the file library.")
    return record


def get_file(file_id: str) -> FileRecord | None:
    with connection() as database:
        return _record(database.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone(), FileRecord)


_FILAMENT_GRAM_PATTERNS = (
    re.compile(r"^\s*;\s*(?:total\s+)?filament\s+used\s*\[g\]\s*=\s*([^\r\n]+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*;\s*(?:total\s+)?filament\s+used\s*[:=]\s*([0-9][0-9.,\s]*)g\b", re.IGNORECASE | re.MULTILINE),
)


def estimate_filament_grams(path: Path, file_type: str) -> float | None:
    """Read common slicer comments without attempting to interpret G-code.

    This is an estimate from the source file, not a hardware measurement. It
    deliberately ignores project archives and unrecognised binary formats.
    """
    if file_type.lower() not in {"gcode", "g", "gc", "bgcode"}:
        return None
    try:
        sample = path.read_bytes()[:4 * 1024 * 1024].decode("utf-8", errors="ignore")
    except OSError:
        return None
    estimates: list[float] = []
    for pattern in _FILAMENT_GRAM_PATTERNS:
        for match in pattern.finditer(sample):
            values = re.findall(r"[0-9]+(?:\.[0-9]+)?", match.group(1))
            if values:
                estimates.append(sum(float(value) for value in values))
    estimates = [estimate for estimate in estimates if 0 <= estimate <= 100_000]
    if not estimates:
        return None
    return round(max(estimates), 2)


def store_upload(payload: FileCreate, original_filename: str, source: BinaryIO) -> FileRecord:
    extension = Path(original_filename).suffix.lower()
    stored_filename = f"{uuid.uuid4()}{extension}"
    FILES_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = _safe_storage_path(stored_filename)
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with destination.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > 512 * 1024 * 1024:
                    raise ValueError("Files larger than 512 MB are not supported yet.")
                digest.update(chunk)
                output.write(chunk)
        estimate = payload.estimated_filament_grams
        if estimate is None:
            estimate = estimate_filament_grams(destination, payload.file_type)
        return create_file(
            payload.model_copy(update={"estimated_filament_grams": estimate}),
            stored_filename=stored_filename,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _safe_storage_path(stored_filename: str) -> Path:
    root = FILES_DIRECTORY.resolve()
    candidate = (root / stored_filename).resolve()
    if candidate.parent != root:
        raise ValueError("Invalid local file storage path.")
    return candidate


def stored_file(file_id: str) -> tuple[FileRecord, Path] | None:
    with connection() as database:
        row = database.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        record = _record(row, FileRecord)
    if not record or not record.stored_filename:
        return None
    path = _safe_storage_path(record.stored_filename)
    if not path.is_file():
        return None
    return record, path


def mark_job_delivered(job_id: str, remote_file_path: str) -> JobRecord | None:
    with connection() as database:
        current = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        delivered_at = _now()
        database.execute(
            "UPDATE jobs SET delivery_state = 'uploaded', remote_file_path = ?, delivered_at = ? WHERE id = ?",
            (remote_file_path, delivered_at.isoformat(), job_id),
        )
        _activity(database, "success", f"{current['name']} was uploaded to its assigned printer.")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def mark_job_delivered_if_current(
    job_id: str,
    remote_file_path: str,
    *,
    printer_id: str,
    file_id: str,
    adapter_type: str,
    host: str,
    credential_id: str | None,
    expected_delivery_state: str,
    expected_remote_file_path: str | None,
) -> JobRecord | None:
    """Bind an upload only if its task and printer connection are unchanged."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        printer = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        source_file = database.execute(
            "SELECT id FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
        if (
            not current
            or current["parent_job_id"] is not None
            or current["state"] != "queued"
            or current["target_printer_id"] != printer_id
            or current["file_id"] != file_id
            or current["delivery_state"] != expected_delivery_state
            or current["remote_file_path"] != expected_remote_file_path
            or _active_child_count(database, job_id)
            or not source_file
            or not printer
            or printer["adapter_type"] != adapter_type
            or printer["host"] != host
            or printer["credential_id"] != credential_id
            or printer["connection_state"] != "connected"
        ):
            return None
        delivered_at = _now()
        database.execute(
            """UPDATE jobs SET delivery_state = 'uploaded', remote_file_path = ?,
              delivered_at = ? WHERE id = ? AND state = 'queued'""",
            (remote_file_path, delivered_at.isoformat(), job_id),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return None
        _activity(database, "success", f"{current['name']} was uploaded to its assigned printer.")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def mark_claimed_run_delivered(
    job_id: str,
    printer_id: str,
    remote_file_path: str,
) -> JobRecord | None:
    """Record upload only while the same starting child still owns the printer."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        printer = database.execute(
            "SELECT active_job_id FROM printers WHERE id = ?",
            (printer_id,),
        ).fetchone()
        if (
            not current
            or current["parent_job_id"] is None
            or current["state"] != "starting"
            or current["target_printer_id"] != printer_id
            or not printer
            or printer["active_job_id"] != job_id
        ):
            return None
        delivered_at = _now()
        database.execute(
            """UPDATE jobs SET delivery_state = 'uploaded', remote_file_path = ?,
              delivered_at = ? WHERE id = ? AND state = 'starting'""",
            (remote_file_path, delivered_at.isoformat(), job_id),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return None
        _activity(database, "success", f"{current['name']} was uploaded to its assigned printer.")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def mark_job_delivery_error(job_id: str, message: str) -> JobRecord | None:
    with connection() as database:
        current = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        database.execute("UPDATE jobs SET delivery_state = 'error' WHERE id = ?", (job_id,))
        _activity(database, "warning", f"{current['name']} could not be uploaded: {message}")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def claim_job_for_auto_start(
    job_id: str,
    printer_id: str,
    *,
    expected_job: JobRecord | None = None,
) -> JobRecord | None:
    """Atomically reserve one repetition as a child job before touching hardware."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if (
            not current
            or current["parent_job_id"] is not None
            or current["state"] != "queued"
            or bool(current["dispatch_paused"])
            or not current["file_id"]
        ):
            return None
        current_record = _record(current, JobRecord)
        if expected_job:
            # Assignment and completion counters may legitimately change when
            # several printers claim the same parent. Definition and pause
            # changes must invalidate a stale route decision.
            revision_fields = RUN_DEFINITION_FIELDS | {"dispatch_paused", "state"}
            if any(getattr(current_record, field) != getattr(expected_job, field) for field in revision_fields):
                return None
            if (
                expected_job.loop_mode == "cycle"
                and current_record.queue_position != expected_job.queue_position
            ):
                return None
        printer = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if (
            not printer
            or printer["state"] != "ready"
            or printer["connection_state"] != "connected"
            or printer["active_job_id"] is not None
            or printer["clearance_state"] not in {"confirmed", "auto_verified"}
        ):
            return None
        assigned_printer_id = current["target_printer_id"]
        if assigned_printer_id not in (None, printer_id):
            return None
        allowed_printers = set(_decode_json_list(current["allowed_printer_ids"]))
        if allowed_printers and printer_id not in allowed_printers:
            return None
        active_runs = _active_child_count(database, job_id)
        repetitions = current["repetitions"]
        completed = int(current["completed_repetitions"] or 0)
        if repetitions is not None and completed + active_runs >= int(repetitions):
            return None
        run_number = int(
            database.execute(
                "SELECT COALESCE(MAX(run_number), 0) + 1 FROM jobs WHERE parent_job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        run_id = str(uuid.uuid4())
        database.execute(
            """UPDATE printers SET state = 'starting', active_job = ?, active_job_id = ?,
              percent_complete = 0, clearance_state = 'occupied'
              WHERE id = ? AND state = 'ready' AND connection_state = 'connected'
              AND active_job_id IS NULL AND clearance_state IN ('confirmed', 'auto_verified')""",
            (current["filename"], run_id, printer_id),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return None
        inherited_delivery = (
            assigned_printer_id == printer_id
            and current["delivery_state"] == "uploaded"
            and bool(current["remote_file_path"])
        )
        delivery_state = "uploaded" if inherited_delivery else "ready"
        database.execute(
            """INSERT INTO jobs (
              id, name, filename, target_printer_id, material, estimated_minutes, file_id,
              repetitions, loop_mode, auto_restart, auto_clear, dispatch_paused,
              completed_repetitions, parent_job_id, run_number,
              required_printer_manufacturer, required_printer_model,
              required_nozzle_diameter, required_filaments,
              required_tags, tag_match_mode, allowed_printer_ids, auto_switch_filament,
              override_existing_file, state, created_at, queue_position, delivery_state,
              remote_file_path, delivered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, current["name"], current["filename"], printer_id, current["material"],
                current["estimated_minutes"], current["file_id"], 1, current["loop_mode"], 0,
                current["auto_clear"], 0, 0, job_id, run_number,
                current["required_printer_manufacturer"], current["required_printer_model"],
                current["required_nozzle_diameter"], current["required_filaments"],
                current["required_tags"], current["tag_match_mode"], current["allowed_printer_ids"],
                current["auto_switch_filament"], current["override_existing_file"], "starting",
                _now().isoformat(), None, delivery_state,
                current["remote_file_path"] if inherited_delivery else None,
                current["delivered_at"] if inherited_delivery else None,
            ),
        )
        if current["loop_mode"] == "cycle":
            tail = database.execute(
                """SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs
                  WHERE state = 'queued' AND parent_job_id IS NULL"""
            ).fetchone()[0]
            database.execute("UPDATE jobs SET queue_position = ? WHERE id = ?", (tail, job_id))
        _normalize_queue(database)
        _activity(
            database,
            "info",
            f"{current['name']} run {run_number} was reserved on {printer['name']}.",
        )
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (run_id,)).fetchone(), JobRecord)


def fail_auto_start_job(job_id: str, message: str) -> JobRecord | None:
    """Stop an unattended retry after a delivery or start error.

    Operators can explicitly requeue the failed job after inspecting the
    printer. This is safer than repeatedly issuing physical start commands.
    """
    return terminate_job(
        job_id,
        "failed",
        f"Auto-start outcome needs review before another job can run: {message}",
        release_ownership=False,
        delivery_error=True,
    )


def abandon_unstarted_run(
    job_id: str,
    message: str,
    *,
    require_ready: bool = False,
) -> JobRecord | None:
    """Cancel a reserved child before any physical start command was issued."""
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        if current["state"] != "starting" or not current["parent_job_id"]:
            return _record(current, JobRecord)
        printer_id = current["target_printer_id"]
        printer = (
            database.execute(
                "SELECT active_job_id, clearance_state FROM printers WHERE id = ?",
                (printer_id,),
            ).fetchone()
            if printer_id
            else None
        )
        if not printer or printer["active_job_id"] != job_id:
            return _record(current, JobRecord)
        database.execute(
            "UPDATE jobs SET state = 'cancelled', queue_position = NULL WHERE id = ? AND state = 'starting'",
            (job_id,),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)
        database.execute(
            "UPDATE jobs SET dispatch_paused = 1 WHERE id = ? AND parent_job_id IS NULL",
            (current["parent_job_id"],),
        )
        clearance = (
            "awaiting_confirmation"
            if require_ready
            else (
                printer["clearance_state"]
                if printer["clearance_state"] in {"confirmed", "auto_verified"}
                else "confirmed"
            )
        )
        state = "attention" if require_ready else "ready"
        database.execute(
            """UPDATE printers SET state = ?, active_job = NULL,
              active_job_id = NULL, percent_complete = NULL,
              connection_message = ?, clearance_state = ?
              WHERE id = ? AND active_job_id = ?""",
            (state, message, clearance, printer_id, job_id),
        )
        _activity(database, "warning", f"{current['name']} was stopped before its start command: {message}")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def terminate_job(
    job_id: str,
    terminal_state: str,
    message: str,
    *,
    release_ownership: bool = True,
    delivery_error: bool = False,
) -> JobRecord | None:
    """Finish a failed/cancelled run and pause its parent without reopening the bed."""
    if terminal_state not in {"failed", "cancelled"}:
        raise ValueError("Only failed and cancelled terminal states are supported.")
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        if current["state"] in {"failed", "cancelled", "completed"}:
            return _record(current, JobRecord)
        if current["state"] not in ACTIVE_RUN_STATES:
            return _record(current, JobRecord)
        delivery_sql = ", delivery_state = 'error'" if delivery_error else ""
        database.execute(
            f"""UPDATE jobs SET state = ?, queue_position = NULL{delivery_sql}
              WHERE id = ? AND state IN ('assigned', 'starting', 'running', 'paused')""",
            (terminal_state, job_id),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)
        if current["parent_job_id"]:
            database.execute(
                "UPDATE jobs SET dispatch_paused = 1 WHERE id = ? AND parent_job_id IS NULL",
                (current["parent_job_id"],),
            )
        printer_id = current["target_printer_id"]
        if printer_id:
            ownership_sql = "active_job = NULL, active_job_id = NULL," if release_ownership else ""
            database.execute(
                f"""UPDATE printers SET state = 'attention', {ownership_sql}
                  connection_message = ?, percent_complete = NULL,
                  clearance_state = 'awaiting_confirmation'
                  WHERE id = ? AND active_job_id = ?""",
                (message, printer_id, job_id),
            )
        _activity(database, "warning", f"{current['name']} was marked {terminal_state}: {message}")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def log_job_control(job_id: str, action: str) -> None:
    with connection() as database:
        job = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job:
            _activity(database, "info", f"{job['name']} received a confirmed {action} command.")


def log_automation_event(level: str, message: str) -> None:
    with connection() as database:
        _activity(database, level, message)


def _material_type(value: str) -> str:
    normalized = value.strip().upper()
    for separator in ("\u00b7", "-", "/", ","):
        normalized = normalized.split(separator, 1)[0].strip()
    return normalized


def _consume_estimated_filament(database: sqlite3.Connection, job: dict[str, Any]) -> None:
    """Apply a slicer-derived estimate to the matching loaded spool.

    It only changes inventory when the operator has explicitly assigned a
    matching spool to the printer. This avoids guessing at material swaps.
    """
    file_id = job.get("file_id")
    printer_id = job.get("target_printer_id")
    if not file_id or not printer_id:
        return
    file = database.execute(
        "SELECT estimated_filament_grams, material FROM files WHERE id = ?",
        (file_id,),
    ).fetchone()
    if not file or not isinstance(file["estimated_filament_grams"], (int, float)):
        return
    grams = float(file["estimated_filament_grams"])
    if grams <= 0:
        return
    expected_type = _material_type(
        str(job.get("material") or file["material"] or "")
    )
    if not expected_type:
        return
    spools = database.execute(
        "SELECT * FROM materials WHERE assigned_printer_id = ? ORDER BY updated_at DESC, name COLLATE NOCASE",
        (printer_id,),
    ).fetchall()
    matching_spools = [
        row
        for row in spools
        if _material_type(str(row["material_type"])) == expected_type
    ]
    if not matching_spools:
        return
    if len(matching_spools) > 1:
        _activity(
            database,
            "warning",
            f"Filament usage for {job['name']} was not deducted because multiple assigned {expected_type} spools match.",
        )
        return
    spool = matching_spools[0]
    percent = max(1, round((grams / max(1, int(spool["spool_grams"]))) * 100))
    remaining = max(0, int(spool["remaining_percent"]) - percent)
    database.execute(
        "UPDATE materials SET remaining_percent = ?, updated_at = ? WHERE id = ?",
        (remaining, _now().isoformat(), spool["id"]),
    )
    _activity(
        database,
        "info",
        f"Estimated {grams:g}g of {expected_type} was recorded against {spool['name']} ({remaining}% remaining).",
    )


def _release_printer_after_completion(
    database: sqlite3.Connection,
    job: dict[str, Any],
    *,
    trusted_auto_clear: bool,
) -> None:
    printer_id = job.get("target_printer_id")
    if not printer_id:
        return
    automatically_cleared = (
        trusted_auto_clear
        and bool(job.get("auto_clear"))
        and _job_auto_eject_verified(database, job)
    )
    printer_state = "ready" if automatically_cleared else "attention"
    clearance_state = "auto_verified" if automatically_cleared else "awaiting_confirmation"
    clearance_message = (
        None
        if automatically_cleared
        else "Remove the completed print and confirm READY before another job."
    )
    database.execute(
        """UPDATE printers SET state = ?, active_job = NULL, active_job_id = NULL,
          percent_complete = NULL, connection_message = ?, clearance_state = ?,
          acknowledged_terminal_fingerprint = CASE
            WHEN ? THEN last_terminal_fingerprint
            ELSE acknowledged_terminal_fingerprint
          END
          WHERE id = ? AND (active_job_id IS NULL OR active_job_id = ?)""",
        (
            printer_state,
            clearance_message,
            clearance_state,
            int(automatically_cleared),
            printer_id,
            job["id"],
        ),
    )


def complete_job(job_id: str, *, trusted_auto_clear: bool = False) -> JobRecord | None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        if current["state"] == "completed":
            return _record(current, JobRecord)
        if current["state"] not in {"running", "paused"}:
            return _record(current, JobRecord)
        values = dict(current)
        printer_id = values.get("target_printer_id")
        if printer_id:
            printer = database.execute("SELECT active_job_id FROM printers WHERE id = ?", (printer_id,)).fetchone()
            if printer and printer["active_job_id"] not in (None, job_id):
                return _record(current, JobRecord)
        parent_job_id = values.get("parent_job_id")
        if parent_job_id:
            parent = database.execute("SELECT * FROM jobs WHERE id = ?", (parent_job_id,)).fetchone()
            if not parent:
                return _record(current, JobRecord)
            database.execute(
                """UPDATE jobs SET state = 'completed', completed_repetitions = 1,
                  queue_position = NULL WHERE id = ? AND state IN ('running', 'paused')""",
                (job_id,),
            )
            if database.execute("SELECT changes()").fetchone()[0] != 1:
                return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)
            _consume_estimated_filament(database, values)
            completed = int(parent["completed_repetitions"] or 0) + 1
            active_siblings = _active_child_count(database, parent_job_id)
            repetitions = parent["repetitions"]
            all_complete = (
                repetitions is not None
                and completed >= int(repetitions)
                and active_siblings == 0
            )
            if all_complete:
                database.execute(
                    """UPDATE jobs SET state = 'completed', queue_position = NULL,
                      completed_repetitions = ?, dispatch_paused = 0 WHERE id = ?""",
                    (completed, parent_job_id),
                )
                _activity(database, "success", f"{parent['name']} completed all scheduled runs.")
            else:
                database.execute(
                    "UPDATE jobs SET completed_repetitions = ? WHERE id = ?",
                    (completed, parent_job_id),
                )
                _activity(
                    database,
                    "success",
                    f"{parent['name']} completed run {values.get('run_number') or completed}.",
                )
            _release_printer_after_completion(
                database,
                values,
                trusted_auto_clear=trusted_auto_clear,
            )
            _normalize_queue(database)
            return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)

        # Compatibility path for active records created before child-run support.
        completed = int(values.get("completed_repetitions") or 0) + 1
        repetitions = values.get("repetitions")
        should_repeat = repetitions is None or completed < int(repetitions)
        _consume_estimated_filament(database, values)
        if should_repeat:
            queue_position = database.execute(
                """SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs
                  WHERE state = 'queued' AND parent_job_id IS NULL"""
            ).fetchone()[0]
            database.execute(
                "UPDATE jobs SET state = 'queued', queue_position = ?, completed_repetitions = ? WHERE id = ?",
                (queue_position, completed, job_id),
            )
            _activity(database, "success", f"{values['name']} completed run {completed} and returned to the queue.")
        else:
            database.execute(
                "UPDATE jobs SET state = 'completed', queue_position = NULL, completed_repetitions = ? WHERE id = ?",
                (completed, job_id),
            )
            _activity(database, "success", f"{values['name']} completed all scheduled runs.")
        _release_printer_after_completion(
            database,
            values,
            trusted_auto_clear=trusted_auto_clear,
        )
        _normalize_queue(database)
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def route_job(job_id: str, printer_id: str, reason: str) -> JobRecord | None:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        job = database.execute(
            "SELECT name, state, parent_job_id FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        printer = database.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not job or not printer:
            return None
        if job["parent_job_id"] or job["state"] != "queued" or _active_child_count(database, job_id):
            raise QueueConflictError("Only an idle queued parent task can be routed.")
        database.execute(
            """UPDATE jobs SET target_printer_id = ?,
              delivery_state = CASE WHEN file_id IS NULL THEN 'not_ready' ELSE 'ready' END,
              remote_file_path = NULL, delivered_at = NULL WHERE id = ?""",
            (printer_id, job_id),
        )
        _activity(database, "success", f"{job['name']} was routed to {printer['name']} ({reason}).")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def delete_file(file_id: str) -> str | None:
    with connection() as database:
        file = database.execute("SELECT name, stored_filename FROM files WHERE id = ?", (file_id,)).fetchone()
        if not file:
            return None
        database.execute("DELETE FROM files WHERE id = ?", (file_id,))
        _activity(database, "info", f"{file['name']} was removed from the file library.")
        if file["stored_filename"]:
            _safe_storage_path(str(file["stored_filename"])).unlink(missing_ok=True)
        return str(file["name"])


def dashboard() -> DashboardResponse:
    with connection() as database:
        printers = [_record(row, PrinterRecord) for row in database.execute("SELECT * FROM printers ORDER BY favorite DESC, name COLLATE NOCASE")]
        jobs = [_record(row, JobRecord) for row in database.execute("SELECT * FROM jobs ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, queue_position, created_at DESC")]
        materials = [_record(row, MaterialRecord) for row in database.execute("SELECT * FROM materials ORDER BY remaining_percent, name COLLATE NOCASE")]
        files = [_record(row, FileRecord) for row in database.execute("SELECT * FROM files ORDER BY created_at DESC")]
        activity = [_record(row, ActivityRecord) for row in database.execute("SELECT * FROM activity ORDER BY occurred_at DESC LIMIT 24")]
    return DashboardResponse(printers=printers, jobs=jobs, materials=materials, files=files, activity=activity)
