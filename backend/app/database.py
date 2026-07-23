from __future__ import annotations

import hashlib
import json
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
            ("material", "TEXT NOT NULL DEFAULT ''"),
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
        ):
            _add_column_if_missing(database, "printers", name, declaration)
        for name, declaration in (
            ("file_id", "TEXT"),
            ("delivery_state", "TEXT NOT NULL DEFAULT 'not_ready'"),
            ("remote_file_path", "TEXT"),
            ("delivered_at", "TEXT"),
        ):
            _add_column_if_missing(database, "jobs", name, declaration)
        for name, declaration in (
            ("stored_filename", "TEXT"),
            ("size_bytes", "INTEGER"),
            ("sha256", "TEXT"),
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
    if "favorite" in values:
        values["favorite"] = bool(values["favorite"])
    if "capabilities" in values:
        try:
            values["capabilities"] = json.loads(values["capabilities"] or "[]")
        except (TypeError, ValueError):
            values["capabilities"] = []
    return model(**values)


def create_printer(payload: PrinterCreate) -> PrinterRecord:
    record = PrinterRecord(id=str(uuid.uuid4()), state="setup", **payload.model_dump())
    with connection() as database:
        database.execute(
            """INSERT INTO printers (
              id, name, manufacturer, model, adapter_type, host, location, state,
              material, material_color, run_hours, maintenance_due_hours, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.manufacturer, record.model, record.adapter_type,
                record.host, record.location, record.state, record.material, record.material_color,
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
    with connection() as database:
        current = database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        current_values = dict(current)
        next_values = {**current_values, **changes}
        if "favorite" in next_values:
            next_values["favorite"] = int(bool(next_values["favorite"]))
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
                snapshot.bed_temp, snapshot.nozzle_temp, snapshot.seen_at.isoformat(), snapshot.note, printer_id,
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
                snapshot.note, printer_id,
            ),
        )
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


def mark_connection_error(printer_id: str, message: str) -> PrinterRecord | None:
    with connection() as database:
        current = database.execute("SELECT name FROM printers WHERE id = ?", (printer_id,)).fetchone()
        if not current:
            return None
        database.execute(
            "UPDATE printers SET connection_state = 'error', connection_message = ? WHERE id = ?",
            (message, printer_id),
        )
        _activity(database, "warning", f"{current['name']} connection needs attention.")
        return _record(database.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone(), PrinterRecord)


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
            """INSERT INTO jobs (id, name, filename, target_printer_id, material, estimated_minutes, file_id, state, created_at, queue_position, delivery_state)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.filename, record.target_printer_id, record.material,
                record.estimated_minutes, record.file_id, record.state, record.created_at.isoformat(),
                record.queue_position, "ready" if record.file_id else "not_ready",
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
        if "file_id" in changes and changes["file_id"] != current_values.get("file_id"):
            next_values["delivery_state"] = "ready" if changes["file_id"] else "not_ready"
            next_values["remote_file_path"] = None
            next_values["delivered_at"] = None
            changes.update({
                "delivery_state": next_values["delivery_state"],
                "remote_file_path": None,
                "delivered_at": None,
            })
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
            database.execute("UPDATE printers SET state = 'printing', active_job = ?, percent_complete = 0 WHERE id = ?", (next_values["name"], printer_id))
            _activity(database, "success", f"{next_values['name']} was marked as printing.")
        elif printer_id and old_state == "running" and new_state in {"completed", "failed", "cancelled"}:
            database.execute("UPDATE printers SET state = 'ready', active_job = NULL, percent_complete = NULL WHERE id = ?", (printer_id,))
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
            """INSERT INTO files (id, name, filename, file_type, material, estimated_minutes, notes, created_at, stored_filename, size_bytes, sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.name, record.filename, record.file_type, record.material,
                record.estimated_minutes, record.notes, now.isoformat(), record.stored_filename,
                record.size_bytes, record.sha256,
            ),
        )
        _activity(database, "info", f"{record.name} was added to the file library.")
    return record


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
        return create_file(payload, stored_filename=stored_filename, size_bytes=size_bytes, sha256=digest.hexdigest())
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


def log_job_control(job_id: str, action: str) -> None:
    with connection() as database:
        job = database.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job:
            _activity(database, "info", f"{job['name']} received a confirmed {action} command.")


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
