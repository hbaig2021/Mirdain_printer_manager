from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from . import database
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
    PrinterRecord,
    PrinterUpdate,
)


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
    if not database.delete_printer(printer_id):
        raise missing("Printer")


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


@app.post("/api/printers/{printer_id}/refresh")
def refresh_printer(printer_id: str) -> dict[str, str]:
    if not database.get_printer(printer_id):
        raise missing("Printer")
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "reason": "setup_required",
            "message": "Choose a verified local adapter and add its device-specific credentials before enabling monitoring.",
            "printer_id": printer_id,
        },
    )
