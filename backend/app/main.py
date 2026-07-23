from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import database
from .adapters import AdapterError, get_adapter
from .models import (
    DashboardResponse,
    FileCreate,
    FileRecord,
    JobCompletionRequest,
    JobControlRequest,
    JobCreate,
    JobRecord,
    JobUpdate,
    MaterialCreate,
    MaterialRecord,
    MaterialUpdate,
    PrinterCreate,
    PrinterConnectionRequest,
    PrinterConnectionResponse,
    PrinterRecord,
    PrinterUpdate,
    RouteCandidate,
    RouteJobRequest,
    RouteResult,
)
from .secret_store import SecretStoreError, load as load_secret, remove as remove_secret, save as save_secret


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    worker = asyncio.create_task(run_connected_printer_automation())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="Mirdain Print Hub", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


def missing(item: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{item} was not found.")


def adapter_capabilities(adapter_type: str) -> list[str]:
    capabilities = get_adapter(adapter_type).capabilities
    return [name for name, enabled in asdict(capabilities).items() if enabled]


def connection_error(printer_id: str, error: Exception) -> HTTPException:
    message = str(error)
    database.mark_connection_error(printer_id, message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def material_type(value: str) -> str:
    normalized = value.strip().upper()
    for separator in ("\u00b7", "-", "/", ","):
        normalized = normalized.split(separator, 1)[0].strip()
    return normalized


def route_candidates(job: JobRecord) -> list[RouteCandidate]:
    required = material_type(job.material)
    candidates: list[RouteCandidate] = []
    for printer in database.dashboard().printers:
        if printer.state != "ready" or printer.connection_state != "connected":
            continue
        if "file_transfer" not in printer.capabilities:
            continue
        loaded = material_type(printer.material)
        supported = {material_type(item) for item in printer.supported_materials if material_type(item)}
        if required and required not in supported and required != loaded:
            continue
        if required and printer.material.strip().casefold() == job.material.strip().casefold():
            score, reason = 100, "the matching material is already loaded"
        elif required and required == loaded:
            score, reason = 90, f"{required} is currently loaded"
        elif required:
            score, reason = 70, f"the printer profile supports {required}"
        else:
            score, reason = 30, "the printer is ready and accepts file delivery"
        candidates.append(RouteCandidate(printer_id=printer.id, printer_name=printer.name, score=score, reason=reason))
    return sorted(candidates, key=lambda item: (-item.score, item.printer_name.casefold()))


def dispatch_verified_auto_start(printer_id: str) -> JobRecord | None:
    """Start one queued job only after the operator has enabled all safety gates."""
    printer = database.get_printer(printer_id)
    if (
        not printer
        or not printer.auto_start_enabled
        or printer.state != "ready"
        or printer.connection_state != "connected"
    ):
        return None
    adapter = get_adapter(printer.adapter_type)
    if not (adapter.capabilities.file_transfer and adapter.capabilities.job_control):
        return None
    queued = sorted(
        (job for job in database.dashboard().jobs if job.state == "queued"),
        key=lambda job: (job.queue_position or 100_000, job.created_at),
    )
    for candidate_job in queued:
        if not candidate_job.auto_restart or not candidate_job.file_id:
            continue
        if candidate_job.target_printer_id not in (None, printer.id):
            continue
        if not any(item.printer_id == printer.id for item in route_candidates(candidate_job)):
            continue
        job = database.claim_job_for_auto_start(candidate_job.id, printer.id)
        if not job:
            continue
        try:
            if not job.file_id:
                raise AdapterError("The job no longer has an uploaded local file.")
            stored = database.stored_file(job.file_id)
            if not stored:
                raise AdapterError("The local print file is no longer available.")
            file_record, path = stored
            if not file_record.auto_eject_verified:
                raise AdapterError("The attached file is not marked as auto-eject verified.")
            if job.delivery_state != "uploaded" or not job.remote_file_path:
                remote_path = adapter.upload(
                    printer.host,
                    load_secret(database.credential_id_for_printer(printer.id)),
                    path,
                    file_record.filename,
                )
                job = database.mark_job_delivered(job.id, remote_path)
                if not job:
                    return None
            adapter.command(
                printer.host,
                load_secret(database.credential_id_for_printer(printer.id)),
                "start",
                job.remote_file_path,
            )
            started = database.update_job(job.id, JobUpdate(state="running"))
            database.log_job_control(job.id, "auto-start")
            return started
        except (AdapterError, SecretStoreError) as error:
            database.fail_auto_start_job(job.id, str(error))
            return None
    return None


def reconcile_reported_job_outcome(printer_id: str, snapshot: object) -> JobRecord | None:
    """Reconcile only explicit terminal states from a trusted adapter.

    A generic ready/idle report is intentionally not enough to mark a job
    complete. That avoids releasing a printer simply because it briefly lost
    its active-file telemetry.
    """
    outcome = getattr(snapshot, "job_outcome", None)
    if outcome not in {"completed", "cancelled", "failed"}:
        return None
    active = database.active_job_for_printer(printer_id)
    if not active or active.state not in {"running", "paused"}:
        return None
    reported_filename = getattr(snapshot, "active_job", None)
    if reported_filename:
        expected_names = {active.filename}
        if active.remote_file_path:
            expected_names.add(Path(active.remote_file_path).name)
        if Path(str(reported_filename)).name not in expected_names:
            return None
    if outcome == "completed":
        return database.complete_job(active.id)
    return database.update_job(active.id, JobUpdate(state=outcome))


def refresh_and_dispatch(printer_id: str) -> PrinterRecord:
    """Read one local printer, then run a single eligible queue transition."""
    printer = database.get_printer(printer_id)
    if not printer:
        raise missing("Printer")
    snapshot = get_adapter(printer.adapter_type).snapshot(
        printer.host,
        load_secret(database.credential_id_for_printer(printer_id)),
    )
    updated = database.apply_snapshot(printer_id, snapshot)
    if not updated:
        raise missing("Printer")
    reconciled = reconcile_reported_job_outcome(printer_id, snapshot)
    if reconciled and getattr(snapshot, "job_outcome", None) != "completed":
        # Cancellation and error reports require operator review; only an
        # explicit successful completion may release automatic dispatch.
        return database.get_printer(printer_id) or updated
    updated = database.get_printer(printer_id) or updated
    if updated.state == "ready":
        dispatch_verified_auto_start(updated.id)
    return database.get_printer(printer_id) or updated


def refresh_connected_printers() -> None:
    """Best-effort local polling used by the in-process automation worker."""
    for printer in database.connected_printers():
        try:
            refresh_and_dispatch(printer.id)
        except (AdapterError, SecretStoreError) as error:
            database.mark_connection_error(printer.id, str(error))


async def run_connected_printer_automation() -> None:
    """Poll connected local printers without blocking API requests.

    A printer must still have the per-printer and per-job verified auto-start
    gates enabled before this worker can transfer or start anything.
    """
    while True:
        await asyncio.to_thread(refresh_connected_printers)
        await asyncio.sleep(10)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local"}


@app.get("/api/dashboard", response_model=DashboardResponse)
def get_dashboard() -> DashboardResponse:
    return database.dashboard()


@app.post("/api/printers", response_model=PrinterRecord, status_code=status.HTTP_201_CREATED)
def add_printer(payload: PrinterCreate) -> PrinterRecord:
    return database.create_printer(payload)


@app.patch("/api/printers/{printer_id}", response_model=PrinterRecord)
def patch_printer(printer_id: str, payload: PrinterUpdate) -> PrinterRecord:
    printer = database.update_printer(printer_id, payload)
    if not printer:
        raise missing("Printer")
    return printer


@app.delete("/api/printers/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_printer(printer_id: str) -> None:
    credential_id = database.credential_id_for_printer(printer_id)
    if not database.delete_printer(printer_id):
        raise missing("Printer")
    remove_secret(credential_id)


@app.post("/api/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
def add_job(payload: JobCreate) -> JobRecord:
    return database.create_job(payload)


@app.patch("/api/jobs/{job_id}", response_model=JobRecord)
def patch_job(job_id: str, payload: JobUpdate) -> JobRecord:
    current = database.get_job(job_id)
    if not current:
        raise missing("Job")
    changes = payload.model_dump(exclude_unset=True)
    if payload.state is not None:
        if payload.state != "queued" or current.state not in {"draft", "failed", "cancelled"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Use the confirmed job controls to change an active job's state.",
            )
    if current.state in {"starting", "running", "paused"} and {"file_id", "target_printer_id"}.intersection(changes):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A file or printer cannot be changed while this job is active.",
        )
    job = database.update_job(job_id, payload)
    if not job:
        raise missing("Job")
    return job


@app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_job(job_id: str) -> None:
    if not database.delete_job(job_id):
        raise missing("Job")


@app.post("/api/jobs/{job_id}/complete", response_model=JobRecord)
def complete_job(job_id: str, payload: JobCompletionRequest) -> JobRecord:
    if payload.confirmation != "COMPLETE":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirm this action with COMPLETE.")
    current = database.get_job(job_id)
    if not current:
        raise missing("Job")
    if current.state not in {"running", "paused"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a running or paused job can be recorded as complete.",
        )
    if current.target_printer_id:
        printer = database.get_printer(current.target_printer_id)
        if printer and printer.active_job_id not in (None, current.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another job owns this printer, so completion was blocked for safety.",
            )
    job = database.complete_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This job could not be completed safely.")
    if job.target_printer_id:
        dispatch_verified_auto_start(job.target_printer_id)
    return job


@app.get("/api/jobs/{job_id}/route-candidates", response_model=list[RouteCandidate])
def get_route_candidates(job_id: str) -> list[RouteCandidate]:
    job = database.get_job(job_id)
    if not job:
        raise missing("Job")
    return route_candidates(job)


@app.post("/api/jobs/{job_id}/route", response_model=RouteResult)
def route_job(job_id: str, payload: RouteJobRequest | None = None) -> RouteResult:
    job = database.get_job(job_id)
    if not job:
        raise missing("Job")
    if job.state != "queued":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only queued jobs can be routed.")
    candidates = route_candidates(job)
    if not candidates:
        material = material_type(job.material)
        suffix = f" for {material}" if material else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No ready, connected printer with file delivery is compatible{suffix}. Update its material profile or connect another printer.",
        )
    selected = candidates[0]
    if payload and payload.printer_id:
        selected = next((candidate for candidate in candidates if candidate.printer_id == payload.printer_id), None)
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That printer is no longer a compatible ready route for this job.",
            )
    updated = database.route_job(job_id, selected.printer_id, selected.reason)
    if not updated:
        raise missing("Job")
    return RouteResult(job=updated, candidates=candidates)


@app.post("/api/materials", response_model=MaterialRecord, status_code=status.HTTP_201_CREATED)
def add_material(payload: MaterialCreate) -> MaterialRecord:
    return database.create_material(payload)


@app.patch("/api/materials/{material_id}", response_model=MaterialRecord)
def patch_material(material_id: str, payload: MaterialUpdate) -> MaterialRecord:
    material = database.update_material(material_id, payload)
    if not material:
        raise missing("Material")
    return material


@app.delete("/api/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_material(material_id: str) -> None:
    if not database.delete_material(material_id):
        raise missing("Material")


@app.post("/api/files", response_model=FileRecord, status_code=status.HTTP_201_CREATED)
def add_file(payload: FileCreate) -> FileRecord:
    return database.create_file(payload)


@app.delete("/api/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_file(file_id: str) -> None:
    if not database.delete_file(file_id):
        raise missing("File")


@app.post("/api/files/upload", response_model=FileRecord, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    name: str = Form(...),
    material: str = Form(""),
    estimated_minutes: int | None = Form(None),
    notes: str = Form(""),
    auto_eject_verified: bool = Form(False),
) -> FileRecord:
    original_filename = file.filename or ""
    suffix = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if suffix not in {"gcode", "g", "gc", "3mf", "bgcode"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a .gcode, .3mf, .bgcode, .g, or .gc print file.")
    payload = FileCreate(
        name=name.strip(), filename=original_filename, file_type=suffix,
        material=material.strip(), estimated_minutes=estimated_minutes, notes=notes.strip(),
        auto_eject_verified=auto_eject_verified,
    )
    try:
        return database.store_upload(payload, original_filename, file.file)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(error)) from error
    finally:
        await file.close()


@app.get("/api/files/{file_id}/download")
def download_file(file_id: str) -> FileResponse:
    stored = database.stored_file(file_id)
    if not stored:
        raise missing("Stored file")
    record, path = stored
    return FileResponse(path, filename=record.filename, media_type="application/octet-stream")


@app.get("/api/adapters")
def adapters() -> list[dict[str, object]]:
    return [
        {
            "id": adapter_type,
            "name": get_adapter(adapter_type).label,
            "capabilities": adapter_capabilities(adapter_type),
        }
        for adapter_type in ("octoprint", "moonraker", "prusalink", "bambu_lan", "manual")
    ]


@app.post("/api/printers/{printer_id}/connection", response_model=PrinterConnectionResponse)
def connect_printer(printer_id: str, payload: PrinterConnectionRequest) -> PrinterConnectionResponse:
    printer = database.get_printer(printer_id)
    if not printer:
        raise missing("Printer")
    try:
        adapter = get_adapter(payload.adapter_type)
        existing_credential_id = database.credential_id_for_printer(printer_id)
        credential = {"api_key": payload.api_key} if payload.api_key else load_secret(existing_credential_id)
        snapshot = adapter.snapshot(payload.host, credential)
        credential_id = existing_credential_id or f"printer:{printer_id}"
        if payload.api_key:
            save_secret(credential_id, credential)
        if not credential:
            credential_id = None
        updated = database.configure_connection(
            printer_id,
            adapter_type=payload.adapter_type,
            host=payload.host.strip(),
            credential_id=credential_id,
            capabilities=adapter_capabilities(payload.adapter_type),
            snapshot=snapshot,
        )
    except (AdapterError, SecretStoreError) as error:
        raise connection_error(printer_id, error) from error
    if not updated:
        raise missing("Printer")
    return PrinterConnectionResponse(
        printer=updated,
        capabilities=updated.capabilities,
        message="Local printer connection verified. Monitoring is now enabled.",
    )


@app.post("/api/jobs/{job_id}/deliver", response_model=JobRecord)
def deliver_job(job_id: str) -> JobRecord:
    job = database.get_job(job_id)
    if not job:
        raise missing("Job")
    if not job.target_printer_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Assign this job to a printer before uploading it.")
    if not job.file_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Choose an uploaded file for this job before sending it.")
    printer = database.get_printer(job.target_printer_id)
    stored = database.stored_file(job.file_id)
    if not printer:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The assigned printer is no longer available.")
    if not stored:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The job file is only a catalog record. Upload the actual file first.")
    record, path = stored
    try:
        adapter = get_adapter(printer.adapter_type)
        if not adapter.capabilities.file_transfer:
            raise AdapterError(f"{adapter.label} file transfer is not available in this release.")
        remote_path = adapter.upload(printer.host, load_secret(database.credential_id_for_printer(printer.id)), path, record.filename)
        updated = database.mark_job_delivered(job_id, remote_path)
    except (AdapterError, SecretStoreError) as error:
        database.mark_job_delivery_error(job_id, str(error))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    if not updated:
        raise missing("Job")
    return updated


@app.post("/api/jobs/{job_id}/control", response_model=JobRecord)
def control_job(job_id: str, payload: JobControlRequest) -> JobRecord:
    if payload.confirmation != payload.action.upper():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Confirm this action with {payload.action.upper()}.")
    job = database.get_job(job_id)
    if not job:
        raise missing("Job")
    allowed_states = {
        "start": {"queued", "assigned"},
        "pause": {"running"},
        "resume": {"paused"},
        "cancel": {"running", "paused"},
    }
    if job.state not in allowed_states[payload.action]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {payload.action} command is not available while this job is {job.state}.",
        )
    if not job.target_printer_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This job has no assigned printer.")
    printer = database.get_printer(job.target_printer_id)
    if not printer:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The assigned printer is no longer available.")
    if payload.action == "start" and (job.delivery_state != "uploaded" or not job.remote_file_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload this file to the printer before starting it.")
    if payload.action == "start" and printer.state != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The assigned printer is not ready to start a new job.")
    if payload.action == "start" and printer.active_job_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The printer already has an active job in this workspace.")
    if payload.action in {"pause", "resume", "cancel"} and printer.active_job:
        if printer.active_job_id and printer.active_job_id != job.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another job owns this printer, so this command was blocked for safety.",
            )
        expected_names = {job.filename}
        if job.remote_file_path:
            expected_names.add(Path(job.remote_file_path).name)
        if Path(printer.active_job).name not in expected_names:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The printer reports a different active file, so this command was blocked for safety.",
            )
    try:
        adapter = get_adapter(printer.adapter_type)
        if not adapter.capabilities.job_control:
            raise AdapterError(f"{adapter.label} job control is not available in this release.")
        adapter.command(
            printer.host, load_secret(database.credential_id_for_printer(printer.id)), payload.action, job.remote_file_path,
        )
    except (AdapterError, SecretStoreError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    if payload.action == "start":
        updated = database.update_job(job_id, JobUpdate(state="running"))
    elif payload.action == "pause":
        updated = database.update_job(job_id, JobUpdate(state="paused"))
    elif payload.action == "resume":
        updated = database.update_job(job_id, JobUpdate(state="running"))
    elif payload.action == "cancel":
        updated = database.update_job(job_id, JobUpdate(state="cancelled"))
    else:
        updated = database.get_job(job_id)
    database.log_job_control(job_id, payload.action)
    if not updated:
        raise missing("Job")
    return updated


@app.post("/api/printers/{printer_id}/refresh")
def refresh_printer(printer_id: str) -> PrinterRecord:
    try:
        return refresh_and_dispatch(printer_id)
    except (AdapterError, SecretStoreError) as error:
        raise connection_error(printer_id, error) from error
