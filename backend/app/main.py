from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import database
from .adapters import AdapterError, get_adapter
from .models import (
    DashboardResponse,
    FileCreate,
    FileRecord,
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
    RouteResult,
)
from .secret_store import SecretStoreError, load as load_secret, remove as remove_secret, save as save_secret


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    yield


app = FastAPI(title="Mirdain Print Hub", version="0.2.0", lifespan=lifespan)
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
    for separator in ("·", "-", "/", ","):
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
    job = database.update_job(job_id, payload)
    if not job:
        raise missing("Job")
    return job


@app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_job(job_id: str) -> None:
    if not database.delete_job(job_id):
        raise missing("Job")


@app.get("/api/jobs/{job_id}/route-candidates", response_model=list[RouteCandidate])
def get_route_candidates(job_id: str) -> list[RouteCandidate]:
    job = database.get_job(job_id)
    if not job:
        raise missing("Job")
    return route_candidates(job)


@app.post("/api/jobs/{job_id}/route", response_model=RouteResult)
def route_job(job_id: str) -> RouteResult:
    job = database.get_job(job_id)
    if not job:
        raise missing("Job")
    candidates = route_candidates(job)
    if not candidates:
        material = material_type(job.material)
        suffix = f" for {material}" if material else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No ready, connected printer with file delivery is compatible{suffix}. Update its material profile or connect another printer.",
        )
    updated = database.route_job(job_id, candidates[0].printer_id, candidates[0].reason)
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
) -> FileRecord:
    original_filename = file.filename or ""
    suffix = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if suffix not in {"gcode", "g", "gc", "3mf", "bgcode"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a .gcode, .3mf, .bgcode, .g, or .gc print file.")
    payload = FileCreate(
        name=name.strip(), filename=original_filename, file_type=suffix,
        material=material.strip(), estimated_minutes=estimated_minutes, notes=notes.strip(),
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
    if not job.target_printer_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This job has no assigned printer.")
    printer = database.get_printer(job.target_printer_id)
    if not printer:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The assigned printer is no longer available.")
    if payload.action == "start" and (job.delivery_state != "uploaded" or not job.remote_file_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload this file to the printer before starting it.")
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
    printer = database.get_printer(printer_id)
    if not printer:
        raise missing("Printer")
    try:
        snapshot = get_adapter(printer.adapter_type).snapshot(printer.host, load_secret(database.credential_id_for_printer(printer_id)))
        updated = database.apply_snapshot(printer_id, snapshot)
    except (AdapterError, SecretStoreError) as error:
        raise connection_error(printer_id, error) from error
    if not updated:
        raise missing("Printer")
    return updated
