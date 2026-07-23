from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from . import database
from .adapters import AdapterError, get_adapter
from .models import (
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
    PrinterConnectionRequest,
    PrinterConnectionResponse,
    PrinterRecord,
    PrinterUpdate,
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
