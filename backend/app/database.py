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


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE_PATH)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        yield database
        database.commit()
    finally:
        database.close()


def _columns(database: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(database: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
    if name not in _columns(database, table):
        database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def initialize() -> None:
    with connection() as database:
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
            ("completed_repetitions", "INTEGER NOT NULL DEFAULT 0"),
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
        ):
            _add_column_if_missing(database, "files", name, declaration)
        database.execute("CREATE INDEX IF NOT EXISTS jobs_position_idx ON jobs(state, queue_position)")
        database.execute("CREATE INDEX IF NOT EXISTS activity_time_idx ON activity(occurred_at DESC)")


def _now() -> datetime:
    return datetime.now(UTC)


def _activity(database: sqlite3.Connection, level: str, message: str) -> None:
    database.execute(
        "INSERT INTO activity (id, occurred_at, level, message) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), _now().isoformat(), level, message),
    )


def _record(row: sqlite3.Row | None, model: type[Any]) -> Any:
    if not row:
        return None
    values = dict(row)
    for name in ("favorite", "auto_start_enabled", "auto_restart", "auto_eject_verified"):
        if name in values:
            values[name] = bool(values[name])
    if "capabilities" in values:
        try:
            values["capabilities"] = json.loads(values["capabilities"] or "[]")
        except (TypeError, ValueError):
            values["capabilities"] = []
    if "supported_materials" in values:
        try:
            values["supported_materials"] = json.loads(values["supported_materials"] or "[]")
        except (TypeError, ValueError):
            values["supported_materials"] = []
    return model(**values)


def create_printer(payload: PrinterCreate) -> PrinterRecord:
    record = PrinterRecord(id=str(uuid.uuid4()), state="setup", **payload.model_dump())
    with connection() as database:
        database.execute(
            """INSERT INTO printers (
              id, name, manufacturer, model, adapter_type, host, camera_url, location, state,
              material, material_color, supported_materials, auto_start_enabled, run_hours, maintenance_due_hours, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.manufacturer, record.model, record.adapter_type,
                record.host, record.camera_url, record.location, record.state, record.material, record.material_color,
                json.dumps(record.supported_materials), int(record.auto_start_enabled), record.run_hours, record.maintenance_due_hours, record.note,
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
    with connection() as database:
        current = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        current_values = dict(current)
        next_values = {**current_values, **changes}
        if "favorite" in next_values:
            next_values["favorite"] = int(bool(next_values["favorite"]))
        if "auto_start_enabled" in changes:
            next_values["auto_start_enabled"] = int(bool(next_values["auto_start_enabled"]))
        if "supported_materials" in changes:
            next_values["supported_materials"] = json.dumps(next_values["supported_materials"])
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [next_values[field] for field in changes]
        database.execute(f"UPDATE printers SET {assignments} WHERE id = ?", (*values, printer_id))
        if "state" in changes and changes["state"] != current_values["state"]:
            _activity(database, "warning" if changes["state"] == "attention" else "info", f"{next_values['name']} is now {changes['state']}.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def delete_printer(printer_id: str) -> str | None:
    with connection() as database:
        printer = database.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not printer:
            return None
        database.execute("UPDATE jobs SET target_printer_id = NULL WHERE target_printer_id = ?", (printer_id,))
        database.execute("UPDATE materials SET assigned_printer_id = NULL WHERE assigned_printer_id = ?", (printer_id,))
        database.execute("DELETE FROM printers WHERE id = ?", (printer_id,))
        _activity(database, "info", f"{printer['name']} was removed from the fleet.")
        return str(printer["name"])


def credential_id_for_printer(printer_id: str) -> str | None:
    with connection() as database:
        row = database.execute("SELECT credential_id FROM printers WHERE id = ?", (printer_id,)).fetchone()
        return str(row["credential_id"]) if row and row["credential_id"] else None


def configure_connection(
    printer_id: str,
    *,
    adapter_type: str,
    host: str,
    credential_id: str | None,
    capabilities: list[str],
    snapshot: AdapterSnapshot,
) -> PrinterRecord | None:
    with connection() as database:
        current = database.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        database.execute(
            """UPDATE printers SET
              adapter_type = ?, host = ?, credential_id = ?, connection_state = 'connected',
              connection_message = ?, last_refreshed_at = ?, capabilities = ?, state = ?,
              percent_complete = ?, active_job = ?, bed_temp = ?, nozzle_temp = ?, last_seen_at = ?, note = ?
              WHERE id = ?""",
            (
                adapter_type, host, credential_id, snapshot.note, snapshot.seen_at.isoformat(),
                json.dumps(capabilities), snapshot.state, snapshot.percent_complete, snapshot.active_job,
                snapshot.bed_temp, snapshot.nozzle_temp, snapshot.seen_at.isoformat(), snapshot.note or "", printer_id,
            ),
        )
        _activity(database, "success", f"{current['name']} connected through {adapter_type}.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def apply_snapshot(printer_id: str, snapshot: AdapterSnapshot) -> PrinterRecord | None:
    with connection() as database:
        current = database.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        database.execute(
            """UPDATE printers SET connection_state = 'connected', connection_message = ?,
              last_refreshed_at = ?, state = ?, percent_complete = ?, active_job = ?,
              bed_temp = ?, nozzle_temp = ?, last_seen_at = ?, note = ? WHERE id = ?""",
            (
                snapshot.note, snapshot.seen_at.isoformat(), snapshot.state, snapshot.percent_complete,
                snapshot.active_job, snapshot.bed_temp, snapshot.nozzle_temp, snapshot.seen_at.isoformat(),
                snapshot.note or "", printer_id,
            ),
        )
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def mark_connection_error(printer_id: str, message: str) -> PrinterRecord | None:
    with connection() as database:
        current = database.execute(
            "SELECT name, connection_state, connection_message FROM printers WHERE id = ?", (printer_id,)
        ).fetchone()
        if not current:
            return None
        database.execute(
            "UPDATE printers SET connection_state = 'error', connection_message = ? WHERE id = ?",
            (message, printer_id),
        )
        if current["connection_state"] != "error" or current["connection_message"] != message:
            _activity(database, "warning", f"{current['name']} connection needs attention.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def connected_printers() -> list[PrinterRecord]:
    with connection() as database:
        rows = database.execute(
            "SELECT * FROM printers WHERE connection_state = 'connected' ORDER BY name COLLATE NOCASE"
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


def _normalize_queue(database: sqlite3.Connection) -> None:
    queued = database.execute("SELECT id FROM jobs WHERE state = 'queued' ORDER BY queue_position, created_at, id").fetchall()
    for position, row in enumerate(queued, start=1):
        database.execute("UPDATE jobs SET queue_position = ? WHERE id = ?", (position, row["id"]))


def create_job(payload: JobCreate) -> JobRecord:
    created_at = _now()
    with connection() as database:
        position = database.execute("SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs WHERE state = 'queued'").fetchone()[0]
        record = JobRecord(id=str(uuid.uuid4()), state="queued", created_at=created_at, queue_position=position, **payload.model_dump())
        database.execute(
            """INSERT INTO jobs (
              id, name, filename, target_printer_id, material, estimated_minutes, file_id,
              repetitions, loop_mode, auto_restart, completed_repetitions,
              state, created_at, queue_position, delivery_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.filename, record.target_printer_id, record.material,
                record.estimated_minutes, record.file_id, record.repetitions, record.loop_mode,
                int(record.auto_restart), record.completed_repetitions, record.state,
                record.created_at.isoformat(), record.queue_position, "ready" if record.file_id else "not_ready",
            ),
        )
        _activity(database, "info", f"{record.name} was added to the production queue.")
    return record


def get_job(job_id: str) -> JobRecord | None:
    with connection() as database:
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def update_job(job_id: str, payload: JobUpdate) -> JobRecord | None:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        with connection() as database:
            return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)
    with connection() as database:
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        current_values = dict(current)
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
        if "auto_restart" in changes:
            next_values["auto_restart"] = int(bool(next_values["auto_restart"]))
        old_state = current_values["state"]
        new_state = next_values["state"]
        if new_state == "queued" and old_state != "queued":
            next_values["queue_position"] = database.execute("SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs WHERE state = 'queued'").fetchone()[0]
            changes["queue_position"] = next_values["queue_position"]
        if new_state != "queued":
            next_values["queue_position"] = None
            changes["queue_position"] = None
        if new_state == "queued" and "queue_position" in changes:
            desired = max(1, int(next_values["queue_position"] or 1))
            database.execute("UPDATE jobs SET queue_position = queue_position + 1 WHERE state = 'queued' AND id != ? AND queue_position >= ?", (job_id, desired))
            next_values["queue_position"] = desired
            changes["queue_position"] = desired
        assignments = ", ".join(f"{field} = ?" for field in changes)
        database.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*[next_values[field] for field in changes], job_id))
        _normalize_queue(database)
        printer_id = next_values.get("target_printer_id")
        if printer_id and new_state == "running":
            database.execute(
                "UPDATE printers SET state = 'printing', active_job = ?, active_job_id = ?, percent_complete = 0 WHERE id = ?",
                (next_values["name"], job_id, printer_id),
            )
            _activity(database, "success", f"{next_values['name']} was marked as printing.")
        elif printer_id and new_state == "paused":
            database.execute(
                "UPDATE printers SET state = 'attention', active_job_id = COALESCE(active_job_id, ?) WHERE id = ?",
                (job_id, printer_id),
            )
            _activity(database, "warning", f"{next_values['name']} was paused.")
        elif printer_id and old_state in {"running", "paused"} and new_state in {"completed", "failed", "cancelled"}:
            database.execute(
                """UPDATE printers SET state = 'ready', active_job = NULL, active_job_id = NULL, percent_complete = NULL
                  WHERE id = ? AND (active_job_id IS NULL OR active_job_id = ?)""",
                (printer_id, job_id),
            )
            _activity(database, "success" if new_state == "completed" else "warning", f"{next_values['name']} was marked {new_state}.")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def delete_job(job_id: str) -> str | None:
    with connection() as database:
        job = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
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
              auto_eject_verified, estimated_filament_grams, created_at, stored_filename, size_bytes, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.filename, record.file_type, record.material,
                record.estimated_minutes, record.notes, int(record.auto_eject_verified), record.estimated_filament_grams,
                now.isoformat(), record.stored_filename, record.size_bytes, record.sha256,
            ),
        )
        _activity(database, "info", f"{record.name} was added to the file library.")
    return record


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


def mark_job_delivery_error(job_id: str, message: str) -> JobRecord | None:
    with connection() as database:
        current = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        database.execute("UPDATE jobs SET delivery_state = 'error' WHERE id = ?", (job_id,))
        _activity(database, "warning", f"{current['name']} could not be uploaded: {message}")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def claim_job_for_auto_start(job_id: str, printer_id: str) -> JobRecord | None:
    """Reserve a queued job before any physical start request is sent.

    The compare-and-set state transition prevents two pollers from starting the
    same job. A manually assigned job is deliberately left with that printer;
    only unassigned jobs may be claimed by another compatible printer.
    """
    with connection() as database:
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current or current["state"] != "queued":
            return None
        printer = database.execute("SELECT name, state, connection_state, active_job_id FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if (
            not printer
            or printer["state"] != "ready"
            or printer["connection_state"] != "connected"
            or printer["active_job_id"] is not None
        ):
            return None
        assigned_printer_id = current["target_printer_id"]
        if assigned_printer_id not in (None, printer_id):
            return None
        target_changed = assigned_printer_id != printer_id
        if target_changed:
            delivery_state = "ready" if current["file_id"] else "not_ready"
            database.execute(
                """UPDATE jobs SET target_printer_id = ?, state = 'starting', queue_position = NULL,
                  delivery_state = ?, remote_file_path = NULL, delivered_at = NULL
                  WHERE id = ? AND state = 'queued'""",
                (printer_id, delivery_state, job_id),
            )
        else:
            database.execute(
                "UPDATE jobs SET state = 'starting', queue_position = NULL WHERE id = ? AND state = 'queued'",
                (job_id,),
            )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return None
        database.execute(
            """UPDATE printers SET state = 'starting', active_job = ?, active_job_id = ?, percent_complete = 0
              WHERE id = ? AND state = 'ready' AND connection_state = 'connected' AND active_job_id IS NULL""",
            (current["name"], job_id, printer_id),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            database.execute(
                "UPDATE jobs SET state = 'queued', queue_position = 1 WHERE id = ? AND state = 'starting'",
                (job_id,),
            )
            _normalize_queue(database)
            return None
        _normalize_queue(database)
        _activity(database, "info", f"{current['name']} was reserved for verified auto-start on {printer['name']}.")
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def fail_auto_start_job(job_id: str, message: str) -> JobRecord | None:
    """Stop an unattended retry after a delivery or start error.

    Operators can explicitly requeue the failed job after inspecting the
    printer. This is safer than repeatedly issuing physical start commands.
    """
    with connection() as database:
        current = database.execute("SELECT name, target_printer_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        database.execute(
            """UPDATE jobs SET state = 'failed', queue_position = NULL,
              delivery_state = 'error' WHERE id = ? AND state = 'starting'""",
            (job_id,),
        )
        if database.execute("SELECT changes()").fetchone()[0] != 1:
            return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)
        if current["target_printer_id"]:
            database.execute(
                """UPDATE printers SET state = 'attention', connection_message = ?, percent_complete = NULL
                  WHERE id = ? AND active_job_id = ?""",
                ("Auto-start outcome needs review before another job can run.", current["target_printer_id"], job_id),
            )
        _activity(database, "warning", f"Verified auto-start stopped for {current['name']}: {message}")
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
    file = database.execute("SELECT estimated_filament_grams FROM files WHERE id = ?", (file_id,)).fetchone()
    if not file or not isinstance(file["estimated_filament_grams"], (int, float)):
        return
    grams = float(file["estimated_filament_grams"])
    if grams <= 0:
        return
    expected_type = _material_type(str(job.get("material") or ""))
    if not expected_type:
        return
    spools = database.execute(
        "SELECT * FROM materials WHERE assigned_printer_id = ? ORDER BY updated_at DESC, name COLLATE NOCASE",
        (printer_id,),
    ).fetchall()
    spool = next((row for row in spools if _material_type(str(row["material_type"])) == expected_type), None)
    if not spool:
        return
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


def complete_job(job_id: str) -> JobRecord | None:
    with connection() as database:
        current = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return None
        if current["state"] not in {"running", "paused"}:
            return _record(current, JobRecord)
        values = dict(current)
        printer_id = values.get("target_printer_id")
        if printer_id:
            printer = database.execute("SELECT active_job_id FROM printers WHERE id = ?", (printer_id,)).fetchone()
            if printer and printer["active_job_id"] not in (None, job_id):
                return _record(current, JobRecord)
        completed = int(values.get("completed_repetitions") or 0) + 1
        repetitions = values.get("repetitions")
        should_repeat = repetitions is None or completed < int(repetitions)
        _consume_estimated_filament(database, values)
        if should_repeat:
            if values.get("loop_mode") == "sticky":
                database.execute("UPDATE jobs SET queue_position = queue_position + 1 WHERE state = 'queued'")
                queue_position = 1
            else:
                queue_position = database.execute(
                    "SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs WHERE state = 'queued'"
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
        if printer_id:
            database.execute(
                """UPDATE printers SET state = 'ready', active_job = NULL, active_job_id = NULL, percent_complete = NULL
                  WHERE id = ? AND (active_job_id IS NULL OR active_job_id = ?)""",
                (printer_id, job_id),
            )
        _normalize_queue(database)
        return _record(database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), JobRecord)


def route_job(job_id: str, printer_id: str, reason: str) -> JobRecord | None:
    with connection() as database:
        job = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        printer = database.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not job or not printer:
            return None
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
