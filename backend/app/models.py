from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


AdapterType = Literal["bambu_lan", "prusalink", "octoprint", "moonraker", "manual"]
PrinterState = Literal["setup", "ready", "printing", "attention", "offline"]
JobState = Literal["draft", "queued", "assigned", "running", "completed", "failed", "cancelled"]


class PrinterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    manufacturer: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=80)
    adapter_type: AdapterType = "manual"
    host: str = Field(default="", max_length=255)
    location: str = Field(default="", max_length=80)
    material: str = Field(default="", max_length=80)
    supported_materials: list[str] = Field(default_factory=list, max_length=24)
    material_color: str = Field(default="#8aaec9", max_length=16)
    run_hours: int = Field(default=0, ge=0, le=1_000_000)
    maintenance_due_hours: int = Field(default=250, ge=1, le=1_000_000)
    note: str = Field(default="", max_length=2_000)


class PrinterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    manufacturer: str | None = Field(default=None, min_length=1, max_length=40)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    adapter_type: AdapterType | None = None
    host: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=80)
    state: PrinterState | None = None
    percent_complete: int | None = Field(default=None, ge=0, le=100)
    active_job: str | None = Field(default=None, max_length=120)
    material: str | None = Field(default=None, max_length=80)
    supported_materials: list[str] | None = Field(default=None, max_length=24)
    material_color: str | None = Field(default=None, max_length=16)
    bed_temp: int | None = Field(default=None, ge=0, le=500)
    nozzle_temp: int | None = Field(default=None, ge=0, le=500)
    run_hours: int | None = Field(default=None, ge=0, le=1_000_000)
    maintenance_due_hours: int | None = Field(default=None, ge=1, le=1_000_000)
    favorite: bool | None = None
    note: str | None = Field(default=None, max_length=2_000)


class PrinterRecord(PrinterCreate):
    id: str
    state: PrinterState
    percent_complete: int | None = None
    active_job: str | None = None
    last_seen_at: datetime | None = None
    bed_temp: int | None = None
    nozzle_temp: int | None = None
    favorite: bool = False
    connection_state: Literal["unconfigured", "connected", "error", "manual"] = "unconfigured"
    connection_message: str | None = None
    last_refreshed_at: datetime | None = None
    capabilities: list[str] = Field(default_factory=list)


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=255)
    target_printer_id: str | None = None
    material: str = Field(default="", max_length=80)
    estimated_minutes: int = Field(default=60, ge=1, le=100_000)
    file_id: str | None = None


class JobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    target_printer_id: str | None = None
    material: str | None = Field(default=None, max_length=80)
    estimated_minutes: int | None = Field(default=None, ge=1, le=100_000)
    state: JobState | None = None
    queue_position: int | None = Field(default=None, ge=1, le=100_000)
    file_id: str | None = None


class JobRecord(JobCreate):
    id: str
    state: JobState
    created_at: datetime
    queue_position: int | None = None
    delivery_state: Literal["not_ready", "ready", "uploaded", "error"] = "not_ready"
    remote_file_path: str | None = None
    delivered_at: datetime | None = None


class MaterialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    material_type: str = Field(min_length=1, max_length=40)
    brand: str = Field(default="", max_length=80)
    color: str = Field(default="#8aaec9", max_length=16)
    remaining_percent: int = Field(default=100, ge=0, le=100)
    spool_grams: int = Field(default=1_000, ge=1, le=100_000)
    assigned_printer_id: str | None = None


class MaterialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    material_type: str | None = Field(default=None, min_length=1, max_length=40)
    brand: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=16)
    remaining_percent: int | None = Field(default=None, ge=0, le=100)
    spool_grams: int | None = Field(default=None, ge=1, le=100_000)
    assigned_printer_id: str | None = None


class MaterialRecord(MaterialCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class FileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=255)
    file_type: str = Field(default="gcode", max_length=32)
    material: str = Field(default="", max_length=80)
    estimated_minutes: int | None = Field(default=None, ge=1, le=100_000)
    notes: str = Field(default="", max_length=2_000)


class FileRecord(FileCreate):
    id: str
    created_at: datetime
    stored_filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class JobControlRequest(BaseModel):
    action: Literal["start", "pause", "resume", "cancel"]
    confirmation: Literal["START", "PAUSE", "RESUME", "CANCEL"]


class RouteCandidate(BaseModel):
    printer_id: str
    printer_name: str
    score: int
    reason: str


class RouteResult(BaseModel):
    job: JobRecord
    candidates: list[RouteCandidate]


class PrinterConnectionRequest(BaseModel):
    adapter_type: AdapterType
    host: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=2_000)


class PrinterConnectionResponse(BaseModel):
    printer: PrinterRecord
    capabilities: list[str]
    message: str


class ActivityRecord(BaseModel):
    id: str
    occurred_at: datetime
    level: Literal["info", "success", "warning"]
    message: str


class DashboardResponse(BaseModel):
    printers: list[PrinterRecord]
    jobs: list[JobRecord]
    materials: list[MaterialRecord]
    files: list[FileRecord]
    activity: list[ActivityRecord]
