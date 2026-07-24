from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AdapterType = Literal["bambu_lan", "prusalink", "octoprint", "moonraker", "manual", "simulator"]
PrinterState = Literal["setup", "ready", "starting", "printing", "attention", "offline"]
JobState = Literal["draft", "queued", "assigned", "starting", "running", "paused", "completed", "failed", "cancelled"]
LoopMode = Literal["sticky", "cycle"]
FilamentSystem = Literal["single", "ams", "multi", "external"]
TagMatchMode = Literal["all", "any", "exactly"]
ClearanceState = Literal["confirmed", "occupied", "awaiting_confirmation", "auto_verified", "unknown"]


class _PatchModel(BaseModel):
    """Distinguish an omitted PATCH field from an invalid explicit null."""

    non_nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def reject_null_for_non_nullable_fields(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            null_fields = sorted(
                field
                for field in cls.non_nullable_fields
                if field in value and value[field] is None
            )
            if null_fields:
                raise ValueError(f"These fields cannot be null: {', '.join(null_fields)}")
        return value


class PrinterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    manufacturer: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=80)
    adapter_type: AdapterType = "manual"
    host: str = Field(default="", max_length=255)
    camera_url: str = Field(default="", max_length=2_000)
    location: str = Field(default="", max_length=80)
    material: str = Field(default="", max_length=80)
    supported_materials: list[str] = Field(default_factory=list, max_length=24)
    nozzle_diameter: float = Field(default=0.4, gt=0, le=10)
    tags: list[str] = Field(default_factory=list, max_length=64)
    filament_system: FilamentSystem = "single"
    loaded_filaments: list[str] = Field(default_factory=list, max_length=32)
    auto_start_enabled: bool = False
    material_color: str = Field(default="#8aaec9", max_length=16)
    run_hours: int = Field(default=0, ge=0, le=1_000_000)
    maintenance_due_hours: int = Field(default=250, ge=1, le=1_000_000)
    note: str = Field(default="", max_length=2_000)


class PrinterUpdate(_PatchModel):
    non_nullable_fields = frozenset(
        {
            "adapter_type",
            "auto_start_enabled",
            "camera_url",
            "favorite",
            "filament_system",
            "host",
            "loaded_filaments",
            "location",
            "maintenance_due_hours",
            "manufacturer",
            "material",
            "material_color",
            "model",
            "name",
            "note",
            "nozzle_diameter",
            "run_hours",
            "state",
            "supported_materials",
            "tags",
        }
    )

    name: str | None = Field(default=None, min_length=1, max_length=80)
    manufacturer: str | None = Field(default=None, min_length=1, max_length=40)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    adapter_type: AdapterType | None = None
    host: str | None = Field(default=None, max_length=255)
    camera_url: str | None = Field(default=None, max_length=2_000)
    location: str | None = Field(default=None, max_length=80)
    state: PrinterState | None = None
    percent_complete: int | None = Field(default=None, ge=0, le=100)
    active_job: str | None = Field(default=None, max_length=120)
    material: str | None = Field(default=None, max_length=80)
    supported_materials: list[str] | None = Field(default=None, max_length=24)
    nozzle_diameter: float | None = Field(default=None, gt=0, le=10)
    tags: list[str] | None = Field(default=None, max_length=64)
    filament_system: FilamentSystem | None = None
    loaded_filaments: list[str] | None = Field(default=None, max_length=32)
    auto_start_enabled: bool | None = None
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
    active_job_id: str | None = None
    last_seen_at: datetime | None = None
    bed_temp: int | None = None
    nozzle_temp: int | None = None
    favorite: bool = False
    connection_state: Literal["unconfigured", "connected", "error", "manual"] = "unconfigured"
    connection_message: str | None = None
    last_refreshed_at: datetime | None = None
    capabilities: list[str] = Field(default_factory=list)
    clearance_state: ClearanceState = "unknown"


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=255)
    target_printer_id: str | None = None
    material: str = Field(default="", max_length=80)
    estimated_minutes: int = Field(default=60, ge=1, le=100_000)
    file_id: str | None = None
    repetitions: int | None = Field(default=1, ge=1, le=100_000)
    loop_mode: LoopMode = "sticky"
    auto_restart: bool = False
    auto_clear: bool = False
    dispatch_paused: bool = False
    required_printer_manufacturer: str = Field(default="", max_length=80)
    required_printer_model: str = Field(default="", max_length=80)
    required_nozzle_diameter: float | None = Field(default=None, gt=0, le=10)
    required_filaments: list[str] = Field(default_factory=list, max_length=32)
    required_tags: list[str] = Field(default_factory=list, max_length=64)
    tag_match_mode: TagMatchMode = "all"
    allowed_printer_ids: list[str] = Field(default_factory=list, max_length=256)
    auto_switch_filament: bool = False
    override_existing_file: bool = False


class JobUpdate(_PatchModel):
    non_nullable_fields = frozenset(
        {
            "allowed_printer_ids",
            "auto_clear",
            "auto_restart",
            "auto_switch_filament",
            "dispatch_paused",
            "estimated_minutes",
            "filename",
            "loop_mode",
            "material",
            "name",
            "override_existing_file",
            "required_filaments",
            "required_printer_manufacturer",
            "required_printer_model",
            "required_tags",
            "state",
            "tag_match_mode",
        }
    )

    name: str | None = Field(default=None, min_length=1, max_length=120)
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    target_printer_id: str | None = None
    material: str | None = Field(default=None, max_length=80)
    estimated_minutes: int | None = Field(default=None, ge=1, le=100_000)
    state: JobState | None = None
    queue_position: int | None = Field(default=None, ge=1, le=100_000)
    file_id: str | None = None
    repetitions: int | None = Field(default=None, ge=1, le=100_000)
    loop_mode: LoopMode | None = None
    auto_restart: bool | None = None
    auto_clear: bool | None = None
    dispatch_paused: bool | None = None
    required_printer_manufacturer: str | None = Field(default=None, max_length=80)
    required_printer_model: str | None = Field(default=None, max_length=80)
    required_nozzle_diameter: float | None = Field(default=None, gt=0, le=10)
    required_filaments: list[str] | None = Field(default=None, max_length=32)
    required_tags: list[str] | None = Field(default=None, max_length=64)
    tag_match_mode: TagMatchMode | None = None
    allowed_printer_ids: list[str] | None = Field(default=None, max_length=256)
    auto_switch_filament: bool | None = None
    override_existing_file: bool | None = None


class JobRecord(JobCreate):
    id: str
    state: JobState
    created_at: datetime
    queue_position: int | None = None
    delivery_state: Literal["not_ready", "ready", "uploaded", "error"] = "not_ready"
    remote_file_path: str | None = None
    delivered_at: datetime | None = None
    completed_repetitions: int = Field(default=0, ge=0)
    parent_job_id: str | None = None
    run_number: int | None = Field(default=None, ge=1)
    start_observed: bool = False


class MaterialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    material_type: str = Field(min_length=1, max_length=40)
    brand: str = Field(default="", max_length=80)
    color: str = Field(default="#8aaec9", max_length=16)
    remaining_percent: int = Field(default=100, ge=0, le=100)
    spool_grams: int = Field(default=1_000, ge=1, le=100_000)
    assigned_printer_id: str | None = None


class MaterialUpdate(_PatchModel):
    non_nullable_fields = frozenset(
        {
            "brand",
            "color",
            "material_type",
            "name",
            "remaining_percent",
            "spool_grams",
        }
    )

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
    auto_eject_verified: bool = False
    estimated_filament_grams: float | None = Field(default=None, ge=0, le=100_000)
    printer_manufacturer: str = Field(default="", max_length=80)
    printer_model: str = Field(default="", max_length=80)
    nozzle_diameter: float | None = Field(default=None, gt=0, le=10)
    filament_profiles: list[str] = Field(default_factory=list, max_length=32)


class FileRecord(FileCreate):
    id: str
    created_at: datetime
    stored_filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class JobControlRequest(BaseModel):
    action: Literal["start", "pause", "resume", "cancel"]
    confirmation: Literal["START", "PAUSE", "RESUME", "CANCEL"]


class JobCompletionRequest(BaseModel):
    confirmation: Literal["COMPLETE"]


class PrinterReadyRequest(BaseModel):
    confirmation: Literal["READY"]


class PrinterAutomationArmRequest(BaseModel):
    confirmation: Literal["ARM STATIC ENDPOINT"]


class PublicPrinterReadyRequest(PrinterReadyRequest):
    model_config = ConfigDict(extra="forbid")

    printer_id: str = Field(min_length=1, max_length=100)


class PublicQueueEnqueueRequest(JobCreate):
    model_config = ConfigDict(extra="forbid")


class RouteCandidate(BaseModel):
    printer_id: str
    printer_name: str
    score: int
    reason: str


class RouteJobRequest(BaseModel):
    printer_id: str | None = None


class RouteResult(BaseModel):
    job: JobRecord
    candidates: list[RouteCandidate]


class PrinterConnectionRequest(BaseModel):
    adapter_type: AdapterType
    host: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=2_000)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=2_000)


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
