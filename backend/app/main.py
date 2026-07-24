from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import database
from .adapters import AdapterError, get_adapter, resolve_local_addresses
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
    PrinterAutomationArmRequest,
    PrinterConnectionRequest,
    PrinterConnectionResponse,
    PrinterReadyRequest,
    PrinterRecord,
    PrinterUpdate,
    PublicPrinterReadyRequest,
    PublicQueueEnqueueRequest,
    RouteCandidate,
    RouteJobRequest,
    RouteResult,
)
from .secret_store import SecretStoreError, load as load_secret, remove as remove_secret, save as save_secret


_PRINTER_COMMAND_LOCKS: dict[str, threading.Lock] = {}
_PRINTER_COMMAND_LOCKS_GUARD = threading.Lock()
_CONNECTION_IDENTITY_LOCK = threading.RLock()
_AUTOMATION_STOP_EVENT = threading.Event()
_AUTOMATION_START_GATES: dict[str, threading.Lock] = {}
_AUTOMATION_START_GATES_GUARD = threading.Lock()
_AUTOMATION_REVOCATIONS: dict[str, threading.Event] = {}
_AUTOMATION_REVOCATIONS_PENDING: dict[str, int] = {}
_AUTOMATION_REVOCATION_EPOCHS: dict[str, int] = {}
_JOB_AUTOMATION_START_GATES: dict[str, threading.Lock] = {}
_JOB_AUTOMATION_START_GATES_GUARD = threading.Lock()
_JOB_AUTOMATION_REVOCATIONS: dict[str, threading.Event] = {}
_JOB_AUTOMATION_REVOCATIONS_PENDING: dict[str, int] = {}
_JOB_AUTOMATION_REVOCATION_EPOCHS: dict[str, int] = {}
_RUN_CANCEL_INTENTS: dict[str, threading.Event] = {}
_RUN_CANCEL_INTENTS_GUARD = threading.Lock()
_NETWORK_ADAPTER_TYPES = frozenset({"octoprint", "moonraker", "prusalink"})
_UNATTENDED_START_ADAPTER_TYPES = frozenset(
    {"moonraker", "prusalink", "simulator"}
)
_AUTOMATION_WORKER_STATUS: dict[str, object] = {
    "running": False,
    "last_cycle_at": None,
    "last_error": None,
}


def printer_command_lock(printer_id: str) -> threading.Lock:
    """Serialize physical commands for one printer within the supported process."""
    with _PRINTER_COMMAND_LOCKS_GUARD:
        return _PRINTER_COMMAND_LOCKS.setdefault(printer_id, threading.Lock())


def automation_start_gate(printer_id: str) -> threading.Lock:
    """Order final START against emergency revocation for one printer."""
    with _AUTOMATION_START_GATES_GUARD:
        return _AUTOMATION_START_GATES.setdefault(printer_id, threading.Lock())


def automation_revocation(printer_id: str) -> threading.Event:
    """Expose emergency disable intent before it waits for an in-flight START."""
    with _AUTOMATION_START_GATES_GUARD:
        return _AUTOMATION_REVOCATIONS.setdefault(
            printer_id,
            threading.Event(),
        )


def begin_automation_revocation(printer_id: str) -> threading.Event:
    with _AUTOMATION_START_GATES_GUARD:
        event = _AUTOMATION_REVOCATIONS.setdefault(
            printer_id,
            threading.Event(),
        )
        _AUTOMATION_REVOCATIONS_PENDING[printer_id] = (
            _AUTOMATION_REVOCATIONS_PENDING.get(printer_id, 0) + 1
        )
        _AUTOMATION_REVOCATION_EPOCHS[printer_id] = (
            _AUTOMATION_REVOCATION_EPOCHS.get(printer_id, 0) + 1
        )
        event.set()
        return event


def end_automation_revocation(printer_id: str) -> None:
    with _AUTOMATION_START_GATES_GUARD:
        pending = max(
            0,
            _AUTOMATION_REVOCATIONS_PENDING.get(printer_id, 1) - 1,
        )
        _AUTOMATION_REVOCATIONS_PENDING[printer_id] = pending


def invalidate_connection_identity_safely(
    printer_id: str,
    message: str,
    *,
    start_gate_held: bool = False,
) -> None:
    """Disarm and invalidate an endpoint without racing a final START."""
    revocation = begin_automation_revocation(printer_id)
    try:
        if start_gate_held:
            database.invalidate_connection_identity(printer_id, message)
        else:
            with automation_start_gate(printer_id):
                database.invalidate_connection_identity(printer_id, message)
        revocation.set()
    finally:
        end_automation_revocation(printer_id)


def mark_connection_error_safely(
    printer_id: str,
    message: str,
    *,
    start_gate_held: bool = False,
) -> PrinterRecord | None:
    """Publish monitoring failure before persisting its automation disarm."""
    revocation = begin_automation_revocation(printer_id)
    try:
        if start_gate_held:
            updated = database.mark_connection_error(printer_id, message)
        else:
            with automation_start_gate(printer_id):
                updated = database.mark_connection_error(printer_id, message)
        revocation.set()
        return updated
    finally:
        end_automation_revocation(printer_id)


def automation_revocation_is_pending(printer_id: str) -> bool:
    with _AUTOMATION_START_GATES_GUARD:
        return _AUTOMATION_REVOCATIONS_PENDING.get(printer_id, 0) > 0


def automation_revocation_epoch(printer_id: str) -> int:
    with _AUTOMATION_START_GATES_GUARD:
        return _AUTOMATION_REVOCATION_EPOCHS.get(printer_id, 0)


def job_automation_start_gate(job_id: str) -> threading.Lock:
    """Order final START against pause/disarm for one queued parent task."""
    with _JOB_AUTOMATION_START_GATES_GUARD:
        return _JOB_AUTOMATION_START_GATES.setdefault(job_id, threading.Lock())


def job_automation_revocation(job_id: str) -> threading.Event:
    """Expose queue disarm intent before it waits for an in-flight START."""
    with _JOB_AUTOMATION_START_GATES_GUARD:
        return _JOB_AUTOMATION_REVOCATIONS.setdefault(
            job_id,
            threading.Event(),
        )


def begin_job_automation_revocation(job_id: str) -> threading.Event:
    with _JOB_AUTOMATION_START_GATES_GUARD:
        event = _JOB_AUTOMATION_REVOCATIONS.setdefault(
            job_id,
            threading.Event(),
        )
        _JOB_AUTOMATION_REVOCATIONS_PENDING[job_id] = (
            _JOB_AUTOMATION_REVOCATIONS_PENDING.get(job_id, 0) + 1
        )
        _JOB_AUTOMATION_REVOCATION_EPOCHS[job_id] = (
            _JOB_AUTOMATION_REVOCATION_EPOCHS.get(job_id, 0) + 1
        )
        event.set()
        return event


def end_job_automation_revocation(job_id: str) -> None:
    with _JOB_AUTOMATION_START_GATES_GUARD:
        pending = max(
            0,
            _JOB_AUTOMATION_REVOCATIONS_PENDING.get(job_id, 1) - 1,
        )
        _JOB_AUTOMATION_REVOCATIONS_PENDING[job_id] = pending


def job_automation_revocation_is_pending(job_id: str) -> bool:
    with _JOB_AUTOMATION_START_GATES_GUARD:
        return _JOB_AUTOMATION_REVOCATIONS_PENDING.get(job_id, 0) > 0


def job_automation_revocation_epoch(job_id: str) -> int:
    with _JOB_AUTOMATION_START_GATES_GUARD:
        return _JOB_AUTOMATION_REVOCATION_EPOCHS.get(job_id, 0)


def run_cancel_intent(job_id: str) -> threading.Event:
    """Publish cancellation before a control request waits on a long upload."""
    with _RUN_CANCEL_INTENTS_GUARD:
        return _RUN_CANCEL_INTENTS.setdefault(job_id, threading.Event())


def drain_inflight_start_commands() -> None:
    """Wait for START calls that linearized before the shutdown signal."""
    with _AUTOMATION_START_GATES_GUARD:
        gates = list(_AUTOMATION_START_GATES.values())
    for gate in gates:
        with gate:
            pass


def canonical_connection_endpoints(host: str) -> list[str]:
    """Resolve canonical physical endpoint keys for duplicate-record protection."""
    normalized = host.strip()
    if not normalized.startswith(("http://", "https://")):
        normalized = f"http://{normalized}"
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AdapterError("Enter a valid local HTTP or HTTPS printer address.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise AdapterError("Enter a valid printer port.") from error
    raw_path = parsed.path
    path_segments = raw_path.strip("/").split("/") if raw_path.strip("/") else []
    if (
        "%" in raw_path
        or "\\" in raw_path
        or "//" in raw_path
        or any(segment in {"", ".", ".."} for segment in path_segments)
    ):
        raise AdapterError(
            "Printer base paths cannot contain encoded characters, empty "
            "segments, or dot segments."
        )
    path = f"/{'/'.join(path_segments)}" if path_segments else ""
    keys: list[str] = []
    for address in sorted(resolve_local_addresses(parsed.hostname)):
        rendered_address = f"[{address}]" if ":" in address else address
        keys.append(
            f"{parsed.scheme.casefold()}://{rendered_address}:{port}{path}"
        )
    return keys


def normalized_request_hostname(raw_host: str) -> str | None:
    """Return a port-free Host value, rejecting malformed authority syntax."""
    value = raw_host.strip()
    if (
        not value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "/" in value
        or "\\" in value
        or "@" in value
        or value.endswith(":")
    ):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "*" in hostname
    ):
        return None
    normalized = hostname.rstrip(".").casefold()
    try:
        ipaddress.ip_address(normalized)
        return normalized
    except ValueError:
        labels = normalized.split(".")
        if (
            len(normalized) > 253
            or any(
                not label
                or len(label) > 63
                or label[0] == "-"
                or label[-1] == "-"
                or any(
                    character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                    for character in label
                )
                for label in labels
            )
        ):
            return None
        return normalized


def allowed_request_hosts() -> set[str]:
    """Build the exact local/VPN Host allowlist used against DNS rebinding."""
    allowed = {"localhost", "127.0.0.1", "::1"}
    configured = os.getenv("MIRDAIN_ALLOWED_HOSTS", "")
    for value in configured.replace(",", " ").split():
        hostname = normalized_request_hostname(value)
        if hostname:
            allowed.add(hostname)
    return allowed


def normalized_request_authority(
    raw_host: str,
    scheme: str,
) -> tuple[str, int] | None:
    hostname = normalized_request_hostname(raw_host)
    if not hostname or scheme not in {"http", "https"}:
        return None
    try:
        parsed = urlsplit(f"//{raw_host.strip()}")
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return hostname, port


def verify_printer_connection_identity(
    printer: PrinterRecord,
    *,
    start_gate_held: bool = False,
) -> None:
    """Fail closed when a configured hostname no longer identifies this printer."""
    if printer.adapter_type not in _NETWORK_ADAPTER_TYPES:
        return
    with _CONNECTION_IDENTITY_LOCK:
        endpoint_keys = canonical_connection_endpoints(printer.host)
        requested = set(endpoint_keys)
        for identity in database.network_connection_identities():
            if identity["id"] == printer.id:
                continue
            other_id = str(identity["id"])
            try:
                other_live_keys = set(
                    canonical_connection_endpoints(str(identity["host"]))
                )
            except AdapterError:
                invalidate_connection_identity_safely(
                    other_id,
                    "The saved printer hostname could not be verified. Reconnect and "
                    "confirm the physical endpoint.",
                )
                continue
            if other_live_keys != set(identity["keys"]):
                invalidate_connection_identity_safely(
                    other_id,
                    "The printer hostname's resolved identity changed. Reconnect and "
                    "confirm the physical endpoint.",
                )
            if requested.intersection(other_live_keys):
                message = (
                    "Two printer records now resolve to the same physical endpoint. "
                    "Reconnect the intended record before sending another command."
                )
                invalidate_connection_identity_safely(
                    printer.id,
                    message,
                    start_gate_held=start_gate_held,
                )
                invalidate_connection_identity_safely(other_id, message)
                raise AdapterError(message)
        if not database.connection_identity_matches(printer.id, endpoint_keys):
            message = (
                "The printer hostname's resolved identity changed or conflicts with "
                "another record. Reconnect and confirm the physical endpoint."
            )
            invalidate_connection_identity_safely(
                printer.id,
                message,
                start_gate_held=start_gate_held,
            )
            raise AdapterError(message)


def acquire_verified_physical_command_gate(
    printer_id: str,
) -> tuple[threading.Lock, PrinterRecord]:
    """Verify under identity ordering, then retain only this printer's gate."""
    gate = automation_start_gate(printer_id)
    with _CONNECTION_IDENTITY_LOCK:
        gate.acquire()
        try:
            printer = database.get_printer(printer_id)
            if not printer:
                raise database.QueueConflictError(
                    "The assigned printer was removed before the physical command."
                )
            verify_printer_connection_identity(
                printer,
                start_gate_held=True,
            )
        except BaseException:
            gate.release()
            raise
    return gate, printer


@contextmanager
def verified_physical_command_gate(
    printer_id: str,
):
    gate, printer = acquire_verified_physical_command_gate(printer_id)
    try:
        yield printer
    finally:
        gate.release()


def ensure_endpoint_available_for_connection(
    printer_id: str,
    endpoint_keys: list[str],
) -> None:
    """Re-resolve existing records before accepting a physical endpoint."""
    requested = set(endpoint_keys)
    if not requested:
        return
    for identity in database.network_connection_identities():
        if identity["id"] == printer_id:
            continue
        try:
            live_keys = set(canonical_connection_endpoints(str(identity["host"])))
        except AdapterError:
            invalidate_connection_identity_safely(
                str(identity["id"]),
                "The saved printer hostname could not be verified. Reconnect and "
                "confirm the physical endpoint.",
            )
            continue
        stored_keys = set(identity["keys"])
        if live_keys != stored_keys:
            invalidate_connection_identity_safely(
                str(identity["id"]),
                "The printer hostname's resolved identity changed. Reconnect and "
                "confirm the physical endpoint.",
            )
        if requested.intersection(live_keys):
            raise database.QueueConflictError(
                f"{identity['name']} is already connected to this physical endpoint."
            )


def automation_shutdown_requested() -> bool:
    return _AUTOMATION_STOP_EVENT.is_set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    database.invalidate_unattended_clearance_on_startup()
    _AUTOMATION_STOP_EVENT.clear()
    worker = asyncio.create_task(run_connected_printer_automation())
    try:
        yield
    finally:
        # Signal first so a dispatch waiting on another printer or task cannot
        # overtake shutdown. Uploads/snapshots may finish, but their final gate
        # will observe this event and refuse START.
        _AUTOMATION_STOP_EVENT.set()
        await asyncio.to_thread(drain_inflight_start_commands)
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="Mirdain Print Hub", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


@app.middleware("http")
async def protect_public_api(request: Request, call_next):
    raw_request_host = request.headers.get("host", "")
    request_host = normalized_request_hostname(raw_request_host)
    if not request_host or request_host not in allowed_request_hosts():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": (
                    "This Host is not allowed. Use localhost or configure "
                    "MIRDAIN_ALLOWED_HOSTS for the trusted VPN/LAN name."
                )
            },
        )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            origin_scheme = ""
            origin_authority = None
            try:
                parsed_origin = urlsplit(origin)
                origin_scheme = parsed_origin.scheme
                if (
                    not parsed_origin.path
                    and not parsed_origin.query
                    and not parsed_origin.fragment
                    and not parsed_origin.username
                    and not parsed_origin.password
                ):
                    origin_authority = normalized_request_authority(
                        parsed_origin.netloc,
                        origin_scheme,
                    )
            except ValueError:
                origin_authority = None
            request_authority = normalized_request_authority(
                raw_request_host,
                request.url.scheme.casefold(),
            )
            if (
                origin_scheme not in {"http", "https"}
                or not origin_authority
                or not request_authority
                or origin_authority != request_authority
            ):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Cross-origin control requests are blocked."},
                )
    configured_token = os.getenv("MIRDAIN_API_TOKEN", "").strip()
    if configured_token and request.method != "OPTIONS" and request.url.path.startswith("/api/public/v1/"):
        authorization = request.headers.get("authorization", "")
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        api_key = request.headers.get("x-api-key", "").strip()
        if not (
            (bearer and secrets.compare_digest(bearer, configured_token))
            or (api_key and secrets.compare_digest(api_key, configured_token))
        ):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "A valid public API token is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


def missing(item: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{item} was not found.")


def adapter_capabilities(adapter_type: str) -> list[str]:
    capabilities = get_adapter(adapter_type).capabilities
    return [name for name, enabled in asdict(capabilities).items() if enabled]


def connection_error(
    printer_id: str,
    error: Exception,
    *,
    start_gate_held: bool = False,
) -> HTTPException:
    message = str(error)
    mark_connection_error_safely(
        printer_id,
        message,
        start_gate_held=start_gate_held,
    )
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


def validate_job_automation(
    file_id: str | None,
    *,
    auto_restart: bool,
    auto_clear: bool,
) -> None:
    """Require an actual, explicitly verified file before arming automation."""
    if not auto_restart and not auto_clear:
        return
    source_file = database.get_file(file_id) if file_id else None
    if not source_file or not source_file.auto_eject_verified:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Automatic restart or clearance requires an attached local file "
                "marked as auto-eject verified."
            ),
        )
    if not database.stored_file(source_file.id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Automatic workflows require the actual uploaded local print file.",
        )


def validate_unattended_start_scope(
    *,
    auto_restart: bool,
    target_printer_id: str | None,
    allowed_printer_ids: list[str],
) -> None:
    """Reject an armed queue scope that cannot issue unattended START."""
    if not auto_restart:
        return
    scoped_ids = (
        [target_printer_id]
        if target_printer_id
        else list(allowed_printer_ids)
    )
    if not scoped_ids:
        return
    printers = {
        printer.id: printer
        for printer in database.dashboard().printers
    }
    if any(
        printers.get(printer_id)
        and printers[printer_id].adapter_type in _UNATTENDED_START_ADAPTER_TYPES
        for printer_id in scoped_ids
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            "Automatic restart is available only for Moonraker, PrusaLink, and "
            "the simulator. OctoPrint requires confirmed manual START, while "
            "manual and Bambu records cannot execute queue runs."
        ),
    )


def material_type(value: str) -> str:
    normalized = value.strip().upper()
    for separator in ("\u00b7", "-", "/", ","):
        normalized = normalized.split(separator, 1)[0].strip()
    return normalized


def normalized_profile(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def filament_profiles_match(job: JobRecord, printer: PrinterRecord, required_profiles: list[str]) -> bool:
    required = [normalized_profile(item) for item in required_profiles if normalized_profile(item)]
    loaded_source = printer.loaded_filaments or ([printer.material] if printer.material.strip() else [])
    loaded = [normalized_profile(item) for item in loaded_source if normalized_profile(item)]
    # No enabled adapter currently sends a validated filament-slot remapping
    # command, so the physical slot order must always match the sliced file.
    return loaded == required


def tags_match(printer_tags: list[str], required_tags: list[str], mode: str) -> bool:
    if not required_tags:
        return True
    available = {normalized_profile(item) for item in printer_tags if normalized_profile(item)}
    required = {normalized_profile(item) for item in required_tags if normalized_profile(item)}
    if not required:
        return True
    if mode == "any":
        return bool(available.intersection(required))
    if mode == "exactly":
        return available == required
    return required.issubset(available)


def route_candidates(job: JobRecord) -> list[RouteCandidate]:
    source_file = database.get_file(job.file_id) if job.file_id else None
    required_manufacturer = (
        job.required_printer_manufacturer.strip()
        or (source_file.printer_manufacturer.strip() if source_file else "")
    )
    required_model = job.required_printer_model.strip() or (source_file.printer_model.strip() if source_file else "")
    required_nozzle = (
        job.required_nozzle_diameter
        if job.required_nozzle_diameter is not None
        else source_file.nozzle_diameter if source_file else None
    )
    required_profiles = job.required_filaments or (source_file.filament_profiles if source_file else [])
    material_name = job.material.strip() or (source_file.material.strip() if source_file else "")
    required_material = material_type(material_name)
    allowed_printers = set(job.allowed_printer_ids)
    candidates: list[RouteCandidate] = []
    for printer in database.dashboard().printers:
        if (
            printer.state != "ready"
            or printer.connection_state != "connected"
            or printer.clearance_state not in {"confirmed", "auto_verified"}
        ):
            continue
        if "file_transfer" not in printer.capabilities:
            continue
        if allowed_printers and printer.id not in allowed_printers:
            continue
        if required_manufacturer and printer.manufacturer.strip().casefold() != required_manufacturer.casefold():
            continue
        if required_model and printer.model.strip().casefold() != required_model.casefold():
            continue
        if required_nozzle is not None and abs(printer.nozzle_diameter - required_nozzle) > 0.0001:
            continue
        if not tags_match(printer.tags, job.required_tags, job.tag_match_mode):
            continue
        if required_profiles and not filament_profiles_match(job, printer, required_profiles):
            continue
        loaded = material_type(printer.material)
        supported = {material_type(item) for item in printer.supported_materials if material_type(item)}
        if not required_profiles and required_material and required_material not in supported and required_material != loaded:
            continue
        if required_profiles:
            score = 100
            reason = "the required filament profiles are loaded in the requested slot order"
        elif required_material and printer.material.strip().casefold() == material_name.casefold():
            score, reason = 100, "the matching material is already loaded"
        elif required_material and required_material == loaded:
            score, reason = 90, f"{required_material} is currently loaded"
        elif required_material:
            score, reason = 70, f"the printer profile supports {required_material}"
        else:
            score, reason = 30, "the printer is ready and accepts file delivery"
        constraints: list[str] = []
        if required_manufacturer:
            constraints.append(f"manufacturer {required_manufacturer}")
        if required_model:
            constraints.append(f"model {required_model}")
        if required_nozzle is not None:
            constraints.append(f"{required_nozzle:g} mm nozzle")
        if job.required_tags:
            constraints.append(f"{job.tag_match_mode} tag match")
        if allowed_printers:
            constraints.append("allowed-printer list")
        if constraints:
            reason = f"{reason}; compatible with {', '.join(constraints)}"
        candidates.append(RouteCandidate(printer_id=printer.id, printer_name=printer.name, score=score, reason=reason))
    return sorted(candidates, key=lambda item: (-item.score, item.printer_name.casefold()))


def loaded_material_matches(job: JobRecord, printer: PrinterRecord) -> bool:
    """Require physically loaded material before issuing a START command.

    ``route_candidates`` also includes supported-but-not-loaded printers for
    planning and file delivery. Physical starts use this stricter gate.
    """
    source_file = database.get_file(job.file_id) if job.file_id else None
    required_profiles = job.required_filaments or (
        source_file.filament_profiles if source_file else []
    )
    if required_profiles:
        return filament_profiles_match(job, printer, required_profiles)
    material_name = job.material.strip() or (
        source_file.material.strip() if source_file else ""
    )
    required_material = material_type(material_name)
    if not required_material:
        return True
    if material_type(printer.material) != required_material:
        return False
    # A value such as "PETG - Orange" is a concrete loaded profile, while a
    # bare "PETG" value is a family requirement.
    if normalized_profile(material_name) != normalized_profile(required_material):
        return normalized_profile(printer.material) == normalized_profile(material_name)
    return True


def dispatch_verified_auto_start(printer_id: str) -> JobRecord | None:
    """Start one queued job only after the operator has enabled all safety gates."""
    if automation_shutdown_requested():
        return None
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
    # OctoPrint's polling API cannot distinguish a prior successful completion
    # from every late cancel/error case. Keep confirmed manual START available,
    # but do not issue unattended START commands from ambiguous readiness.
    if printer.adapter_type == "octoprint":
        return None
    queued = sorted(
        (
            job
            for job in database.dashboard().jobs
            if job.state == "queued"
            and job.parent_job_id is None
            and not job.dispatch_paused
        ),
        key=lambda job: (job.queue_position or 100_000, job.created_at),
    )
    for candidate_job in queued:
        if not candidate_job.auto_restart or not candidate_job.file_id:
            continue
        source_file = database.get_file(candidate_job.file_id)
        if not source_file or not source_file.auto_eject_verified:
            continue
        command_lock = printer_command_lock(printer_id)
        with command_lock:
            if automation_shutdown_requested():
                return None
            live_printer = database.get_printer(printer_id)
            if (
                not live_printer
                or not live_printer.auto_start_enabled
                or live_printer.state != "ready"
                or live_printer.connection_state != "connected"
            ):
                return None
            live_adapter = get_adapter(live_printer.adapter_type)
            if not (
                live_adapter.capabilities.file_transfer
                and live_adapter.capabilities.job_control
            ):
                return None
            if candidate_job.target_printer_id not in (None, live_printer.id):
                continue
            if not any(
                item.printer_id == live_printer.id
                for item in route_candidates(candidate_job)
            ):
                continue
            if not loaded_material_matches(candidate_job, live_printer):
                continue
            verify_printer_connection_identity(live_printer)
            baseline_active_file = live_printer.active_job
            job = database.claim_job_for_auto_start(
                candidate_job.id,
                live_printer.id,
                expected_job=candidate_job,
            )
            if not job:
                continue
            printer = live_printer
            adapter = live_adapter
            try:
                if automation_shutdown_requested():
                    database.abandon_unstarted_run(
                        job.id,
                        "The hub began shutting down before file delivery.",
                    )
                    return None
                if not job.file_id:
                    raise AdapterError("The job no longer has an uploaded local file.")
                stored = database.stored_file(job.file_id)
                if not stored:
                    raise AdapterError("The local print file is no longer available.")
                file_record, path = stored
                if not file_record.auto_eject_verified:
                    raise AdapterError(
                        "The attached file is not marked as auto-eject verified."
                    )
                credential_id = database.credential_id_for_printer(printer.id)
                credential = load_secret(credential_id)
                uploaded_path: str | None = None
                if (
                    job.override_existing_file
                    or job.delivery_state != "uploaded"
                    or not job.remote_file_path
                ):
                    uploaded_path = adapter.upload(
                        printer.host,
                        credential,
                        path,
                        file_record.filename,
                    )
                current_job = database.get_job(job.id)
                current_printer = database.get_printer(printer.id)
                current_parent = (
                    database.get_job(current_job.parent_job_id)
                    if current_job and current_job.parent_job_id
                    else None
                )
                current_file = (
                    database.get_file(current_job.file_id)
                    if current_job and current_job.file_id
                    else None
                )
                if (
                    not current_job
                    or current_job.state != "starting"
                    or not current_printer
                    or current_printer.active_job_id != current_job.id
                ):
                    return None
                if (
                    not current_printer.auto_start_enabled
                    or current_printer.connection_state != "connected"
                    or current_printer.adapter_type != printer.adapter_type
                    or current_printer.host != printer.host
                    or database.credential_id_for_printer(printer.id) != credential_id
                    or not current_parent
                    or current_parent.state != "queued"
                    or current_parent.dispatch_paused
                    or not current_parent.auto_restart
                    or current_parent.file_id != current_job.file_id
                    or not current_file
                    or not current_file.auto_eject_verified
                    or not database.stored_file(current_file.id)
                    or not loaded_material_matches(current_parent, current_printer)
                    or automation_shutdown_requested()
                ):
                    database.abandon_unstarted_run(
                        current_job.id,
                        "Automation settings changed while the file was uploading.",
                        require_ready=True,
                    )
                    return None
                if uploaded_path is not None:
                    current_job = database.mark_claimed_run_delivered(
                        current_job.id,
                        printer.id,
                        uploaded_path,
                    )
                    if not current_job:
                        database.abandon_unstarted_run(
                            job.id,
                            "Delivery ownership changed after upload. Inspect the bed and confirm READY.",
                            require_ready=True,
                        )
                        return None
                fresh_snapshot = adapter.snapshot(printer.host, credential)
                if not database.prestart_snapshot_is_safe(
                    printer.id,
                    current_job.id,
                    fresh_snapshot,
                    baseline_active_file=baseline_active_file,
                ):
                    database.abandon_unstarted_run(
                        current_job.id,
                        "Fresh printer telemetry changed during upload. Inspect the bed and confirm READY.",
                        require_ready=True,
                    )
                    return None
                verify_printer_connection_identity(
                    database.get_printer(printer.id) or current_printer
                )
                parent_id = current_job.parent_job_id
                if not parent_id:
                    raise database.QueueConflictError(
                        "The reserved run lost its parent queue task."
                    )
                with job_automation_start_gate(parent_id):
                    with verified_physical_command_gate(printer.id):
                        final_job = database.get_job(current_job.id)
                        final_printer = database.get_printer(printer.id)
                        final_parent = (
                            database.get_job(final_job.parent_job_id)
                            if final_job and final_job.parent_job_id
                            else None
                        )
                        final_file = (
                            database.get_file(final_job.file_id)
                            if final_job and final_job.file_id
                            else None
                        )
                        if (
                            not final_job
                            or final_job.state != "starting"
                            or not final_printer
                            or final_printer.active_job_id != final_job.id
                            or final_printer.state != "starting"
                            or final_printer.connection_state != "connected"
                            or not final_printer.auto_start_enabled
                            or final_printer.adapter_type != printer.adapter_type
                            or final_printer.adapter_type
                            not in _UNATTENDED_START_ADAPTER_TYPES
                            or final_printer.host != printer.host
                            or database.credential_id_for_printer(printer.id)
                            != credential_id
                            or not final_parent
                            or final_parent.id != parent_id
                            or final_parent.state != "queued"
                            or final_parent.dispatch_paused
                            or not final_parent.auto_restart
                            or final_parent.file_id != final_job.file_id
                            or not final_file
                            or not final_file.auto_eject_verified
                            or not database.stored_file(final_file.id)
                            or not loaded_material_matches(
                                final_parent,
                                final_printer,
                            )
                            or automation_revocation(printer.id).is_set()
                            or job_automation_revocation(parent_id).is_set()
                            or run_cancel_intent(final_job.id).is_set()
                            or automation_shutdown_requested()
                        ):
                            database.abandon_unstarted_run(
                                current_job.id,
                                "Automation was revoked before the final start command. "
                                "Inspect the bed and confirm READY.",
                                require_ready=True,
                            )
                            return None
                        started = database.update_job(
                            final_job.id,
                            JobUpdate(state="running"),
                        )
                        if not started:
                            raise database.QueueConflictError(
                                "The reserved run changed before the final start command."
                            )
                        adapter.command(
                            final_printer.host,
                            credential,
                            "start",
                            final_job.remote_file_path,
                        )
                database.log_job_control(current_job.id, "auto-start")
                return started
            except (AdapterError, SecretStoreError, database.QueueConflictError) as error:
                database.fail_auto_start_job(job.id, str(error))
                return None
            except Exception as error:
                message = (
                    "An unexpected internal error interrupted auto-start "
                    f"({type(error).__name__})."
                )
                database.fail_auto_start_job(job.id, message)
                _AUTOMATION_WORKER_STATUS["last_error"] = message
                return None
    return None


def reconcile_reported_job_outcome(printer_id: str, snapshot: object) -> JobRecord | None:
    """Reconcile only explicit terminal states from a trusted adapter.

    A generic ready/idle report is intentionally not enough to mark a job
    complete. That avoids releasing a printer simply because it briefly lost
    its active-file telemetry.
    """
    with printer_command_lock(printer_id):
        return _reconcile_reported_job_outcome_locked(printer_id, snapshot)


def _reconcile_reported_job_outcome_locked(
    printer_id: str,
    snapshot: object,
) -> JobRecord | None:
    active = database.active_job_for_printer(printer_id)
    if not active or active.state not in {"starting", "running", "paused"}:
        return None
    reported_filename = getattr(snapshot, "active_job", None)
    filename_matches = True
    if reported_filename:
        expected_names = {active.filename}
        if active.remote_file_path:
            expected_names.add(Path(active.remote_file_path).name)
        filename_matches = Path(str(reported_filename)).name in expected_names
    if not filename_matches:
        return None
    outcome = getattr(snapshot, "job_outcome", None)
    if (
        getattr(snapshot, "state", None) == "printing"
        and reported_filename
    ):
        if active.state == "starting":
            active = database.update_job(active.id, JobUpdate(state="running")) or active
        active = database.mark_run_start_observed(active.id) or active
    if outcome not in {"completed", "cancelled", "failed"}:
        return active if active.state == "running" else None
    if not reported_filename:
        return None
    if not active.start_observed:
        return None
    if outcome == "completed":
        return database.complete_job(active.id, trusted_auto_clear=True)
    return database.terminate_job(
        active.id,
        outcome,
        f"The printer reported this run as {outcome}; inspect it and confirm READY.",
        release_ownership=True,
    )


def refresh_and_dispatch(printer_id: str) -> PrinterRecord:
    """Read one local printer, then run a single eligible queue transition."""
    should_dispatch = False
    with printer_command_lock(printer_id):
        try:
            printer = database.get_printer(printer_id)
            if not printer:
                raise missing("Printer")
            verify_printer_connection_identity(printer)
            snapshot = get_adapter(printer.adapter_type).snapshot(
                printer.host,
                load_secret(database.credential_id_for_printer(printer_id)),
            )
            updated = database.apply_snapshot(printer_id, snapshot)
            if not updated:
                raise missing("Printer")
            reconciled = _reconcile_reported_job_outcome_locked(
                printer_id,
                snapshot,
            )
            if reconciled and getattr(snapshot, "job_outcome", None) != "completed":
                # Cancellation and error reports require operator review; only an
                # explicit successful completion may release automatic dispatch.
                return database.get_printer(printer_id) or updated
            updated = database.get_printer(printer_id) or updated
            should_dispatch = updated.state == "ready"
        except HTTPException:
            raise
        except (AdapterError, SecretStoreError) as error:
            mark_connection_error_safely(printer_id, str(error))
            raise
        except Exception as error:
            message = (
                "Monitoring failed unexpectedly "
                f"({type(error).__name__}). Reconnect and confirm READY."
            )
            mark_connection_error_safely(printer_id, message)
            raise AdapterError(message) from error
    if should_dispatch:
        dispatch_verified_auto_start(printer_id)
    return database.get_printer(printer_id) or updated


def refresh_connected_printers() -> None:
    """Best-effort local polling used by the in-process automation worker."""
    printers = database.connected_printers()
    if not printers:
        return

    def refresh_one(printer: PrinterRecord) -> None:
        if automation_shutdown_requested():
            return
        try:
            refresh_and_dispatch(printer.id)
        except (AdapterError, SecretStoreError):
            # refresh_and_dispatch already disarmed the printer while its
            # command lock was held, so a newer reconnect cannot be overwritten.
            return
        except Exception as error:
            _AUTOMATION_WORKER_STATUS["last_error"] = (
                f"{printer.name}: {type(error).__name__}"
            )
            with suppress(Exception):
                database.log_automation_event(
                    "warning",
                    f"{printer.name} polling hit an unexpected {type(error).__name__}; the worker continued.",
                )

    # One slow/offline endpoint must not delay the rest of the farm. Keep the
    # pool bounded so large fleets do not create unbounded worker threads.
    with ThreadPoolExecutor(max_workers=min(8, len(printers))) as executor:
        list(executor.map(refresh_one, printers))


async def run_connected_printer_automation() -> None:
    """Poll connected local printers without blocking API requests.

    A printer must still have the per-printer and per-job verified auto-start
    gates enabled before this worker can transfer or start anything.
    """
    _AUTOMATION_WORKER_STATUS["running"] = True
    try:
        while not _AUTOMATION_STOP_EVENT.is_set():
            try:
                await asyncio.to_thread(refresh_connected_printers)
            except Exception as error:
                _AUTOMATION_WORKER_STATUS["last_error"] = type(error).__name__
                with suppress(Exception):
                    database.log_automation_event(
                        "warning",
                        f"The automation cycle hit an unexpected {type(error).__name__}; polling will continue.",
                    )
            _AUTOMATION_WORKER_STATUS["last_cycle_at"] = datetime.now(UTC).isoformat()
            if not _AUTOMATION_STOP_EVENT.is_set():
                await asyncio.to_thread(_AUTOMATION_STOP_EVENT.wait, 10)
    finally:
        _AUTOMATION_WORKER_STATUS["running"] = False


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "local",
        "automation_worker": dict(_AUTOMATION_WORKER_STATUS),
    }


@app.get("/api/public/v1/ping")
def public_ping() -> dict[str, str]:
    return {"status": "ok", "mode": "local", "api_version": "v1"}


@app.get("/api/public/v1/inventory/filament", response_model=list[MaterialRecord])
def public_filament_inventory() -> list[MaterialRecord]:
    return database.dashboard().materials


@app.get("/api/public/v1/printer/active", response_model=list[PrinterRecord])
def public_active_printers() -> list[PrinterRecord]:
    return [
        printer
        for printer in database.dashboard().printers
        if printer.active_job_id is not None or printer.state in {"starting", "printing"}
    ]


@app.get("/api/public/v1/printer/idle", response_model=list[PrinterRecord])
def public_idle_printers() -> list[PrinterRecord]:
    return [
        printer
        for printer in database.dashboard().printers
        if (
            printer.state == "ready"
            and printer.active_job_id is None
            and printer.clearance_state in {"confirmed", "auto_verified"}
        )
    ]


@app.get("/api/public/v1/queue/files", response_model=list[FileRecord])
def public_queue_files() -> list[FileRecord]:
    return database.dashboard().files


@app.get("/api/public/v1/queue/tasks", response_model=list[JobRecord])
def public_queue_tasks() -> list[JobRecord]:
    return [
        job
        for job in database.dashboard().jobs
        if job.state == "queued" and job.parent_job_id is None
    ]


def confirmed_ready(printer_id: str) -> PrinterRecord:
    with printer_command_lock(printer_id):
        return _confirmed_ready_locked(printer_id)


def _confirmed_ready_locked(printer_id: str) -> PrinterRecord:
    current = database.get_printer(printer_id)
    if not current:
        raise missing("Printer")
    if (
        current.adapter_type not in {"manual", "bambu_lan"}
        and current.connection_state != "connected"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reconnect or successfully refresh this printer before confirming READY.",
        )
    if current.state in {"starting", "printing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A printer that is starting or printing cannot be marked ready.",
        )
    active = database.active_job_for_printer(printer_id)
    if active and active.state == "starting" and current.state == "attention":
        database.terminate_job(
            active.id,
            "failed",
            "The operator cleared a stale start reservation and confirmed the printer bed.",
            release_ownership=True,
        )
        active = None
    if active and active.state in {"starting", "running", "paused"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete or cancel the active job before marking this printer ready.",
        )
    updated = database.mark_printer_ready(printer_id)
    if not updated:
        raise missing("Printer")
    if updated.state != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The printer still owns active work and could not be marked ready.",
        )
    return updated


@app.post("/api/public/v1/printer/ready", response_model=PrinterRecord)
def public_mark_printer_ready(payload: PublicPrinterReadyRequest) -> PrinterRecord:
    return confirmed_ready(payload.printer_id)


@app.post("/api/public/v1/queue/enqueue", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
def public_enqueue(payload: PublicQueueEnqueueRequest) -> JobRecord:
    if payload.file_id and not database.get_file(payload.file_id):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The requested local file does not exist.")
    printers = {printer.id for printer in database.dashboard().printers}
    if payload.target_printer_id and payload.target_printer_id not in printers:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The target printer does not exist.")
    validate_job_automation(
        payload.file_id,
        auto_restart=payload.auto_restart,
        auto_clear=payload.auto_clear,
    )
    validate_unattended_start_scope(
        auto_restart=payload.auto_restart,
        target_printer_id=payload.target_printer_id,
        allowed_printer_ids=payload.allowed_printer_ids,
    )
    return database.create_job(JobCreate.model_validate(payload.model_dump()))


@app.get("/api/dashboard", response_model=DashboardResponse)
def get_dashboard() -> DashboardResponse:
    return database.dashboard()


@app.post("/api/printers", response_model=PrinterRecord, status_code=status.HTTP_201_CREATED)
def add_printer(payload: PrinterCreate) -> PrinterRecord:
    if payload.auto_start_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Add and connect the printer with automation off, confirm READY, "
                "then use the static-endpoint arming action."
            ),
        )
    return database.create_printer(payload)


@app.patch("/api/printers/{printer_id}", response_model=PrinterRecord)
def patch_printer(printer_id: str, payload: PrinterUpdate) -> PrinterRecord:
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("auto_start_enabled") is True:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Use the confirmed static-endpoint arming action to enable "
                "automatic starts."
            ),
        )
    if changes.get("auto_start_enabled") is False:
        # Emergency automation revocation must remain available while a long
        # upload holds the physical command lock. Dispatch re-reads this value
        # immediately before START.
        revocation = begin_automation_revocation(printer_id)
        try:
            with automation_start_gate(printer_id):
                current = database.get_printer(printer_id)
                if not current:
                    raise missing("Printer")
                if {"host", "adapter_type"}.intersection(changes):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Use Test and save connection to change a printer host or adapter.",
                    )
                telemetry_fields = {
                    "active_job",
                    "bed_temp",
                    "nozzle_temp",
                    "percent_complete",
                    "state",
                }
                if (
                    current.adapter_type != "manual"
                    and telemetry_fields.intersection(changes)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Connected printer status is telemetry-owned; use Refresh or the confirmed READY action.",
                    )
                if (
                    payload.state == "ready"
                    and current.clearance_state
                    not in {"confirmed", "auto_verified"}
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Use the confirmed READY action after inspecting and clearing the printer.",
                    )
                disabled = database.update_printer(printer_id, payload)
                revocation.set()
        except database.QueueConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        finally:
            end_automation_revocation(printer_id)
        if not disabled:
            raise missing("Printer")
        return disabled
    with printer_command_lock(printer_id):
        current = database.get_printer(printer_id)
        if not current:
            raise missing("Printer")
        if {"host", "adapter_type"}.intersection(changes):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Use Test and save connection to change a printer host or adapter.",
            )
        telemetry_fields = {
            "active_job",
            "bed_temp",
            "nozzle_temp",
            "percent_complete",
            "state",
        }
        if (
            current.adapter_type != "manual"
            and telemetry_fields.intersection(changes)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Connected printer status is telemetry-owned; use Refresh or the confirmed READY action.",
            )
        if payload.state == "ready" and current.clearance_state not in {"confirmed", "auto_verified"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Use the confirmed READY action after inspecting and clearing the printer.",
            )
        try:
            printer = database.update_printer(printer_id, payload)
        except database.QueueConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        if not printer:
            raise missing("Printer")
        return printer


@app.post(
    "/api/printers/{printer_id}/automation/arm",
    response_model=PrinterRecord,
)
def arm_printer_automation(
    printer_id: str,
    payload: PrinterAutomationArmRequest,
) -> PrinterRecord:
    del payload  # Literal validation records the explicit operator attestation.
    requested_epoch = automation_revocation_epoch(printer_id)
    with printer_command_lock(printer_id):
        with _CONNECTION_IDENTITY_LOCK:
            with automation_start_gate(printer_id):
                if automation_revocation_epoch(printer_id) != requested_epoch:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Automation was disabled while this arm request was "
                            "waiting. Review the printer before trying again."
                        ),
                    )
                printer = database.get_printer(printer_id)
                if not printer:
                    raise missing("Printer")
                if printer.adapter_type not in _UNATTENDED_START_ADAPTER_TYPES:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=(
                            "Automatic START is available only for Moonraker, "
                            "PrusaLink, and the simulator."
                        ),
                    )
                if (
                    printer.connection_state != "connected"
                    or printer.state != "ready"
                    or printer.clearance_state
                    not in {"confirmed", "auto_verified"}
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Connect this printer, inspect the physical machine, "
                            "and confirm READY before arming automatic starts."
                        ),
                    )
                try:
                    verify_printer_connection_identity(
                        printer,
                        start_gate_held=True,
                    )
                    armed = database.update_printer(
                        printer_id,
                        PrinterUpdate(auto_start_enabled=True),
                    )
                except database.QueueConflictError as error:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=str(error),
                    ) from error
                except AdapterError as error:
                    raise connection_error(
                        printer_id,
                        error,
                        start_gate_held=True,
                    ) from error
                if not armed:
                    raise missing("Printer")
                if not automation_revocation_is_pending(printer_id):
                    automation_revocation(printer_id).clear()
                return armed


@app.post("/api/printers/{printer_id}/ready", response_model=PrinterRecord)
def mark_printer_ready(printer_id: str, payload: PrinterReadyRequest) -> PrinterRecord:
    del payload  # Literal validation is the confirmation gate.
    return confirmed_ready(printer_id)


@app.delete("/api/printers/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_printer(printer_id: str) -> None:
    with printer_command_lock(printer_id):
        credential_id = database.credential_id_for_printer(printer_id)
        try:
            removed = database.delete_printer(printer_id)
        except database.QueueConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        if not removed:
            raise missing("Printer")
        remove_secret(credential_id)


@app.post("/api/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
def add_job(payload: JobCreate) -> JobRecord:
    validate_job_automation(
        payload.file_id,
        auto_restart=payload.auto_restart,
        auto_clear=payload.auto_clear,
    )
    validate_unattended_start_scope(
        auto_restart=payload.auto_restart,
        target_printer_id=payload.target_printer_id,
        allowed_printer_ids=payload.allowed_printer_ids,
    )
    return database.create_job(payload)


@app.patch("/api/jobs/{job_id}", response_model=JobRecord)
def patch_job(job_id: str, payload: JobUpdate) -> JobRecord:
    current = database.get_job(job_id)
    if not current:
        raise missing("Job")
    changes = payload.model_dump(exclude_unset=True)
    if current.parent_job_id and changes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Individual run records are immutable; update the queued parent task instead.",
        )
    if payload.state is not None:
        if payload.state != "queued" or current.state not in {"draft", "failed", "cancelled"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Use the confirmed job controls to change an active job's state.",
            )
    routing_fields = {
        "allowed_printer_ids",
        "auto_switch_filament",
        "file_id",
        "material",
        "required_filaments",
        "required_nozzle_diameter",
        "required_printer_manufacturer",
        "required_printer_model",
        "required_tags",
        "tag_match_mode",
        "target_printer_id",
    }
    active_mutations = (
        routing_fields | database.RUN_DEFINITION_FIELDS | {"queue_position"}
    ).intersection(changes)
    safe_revocations = {
        field
        for field in {"auto_restart", "auto_clear"}
        if field in changes and changes[field] is False
    }
    if (
        current.state in {"starting", "running", "paused"}
        or database.job_has_active_runs(current.id)
    ) and (active_mutations - safe_revocations):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A file, printer, or routing requirement cannot be changed while this job is active.",
        )
    if {"auto_restart", "auto_clear", "file_id"}.intersection(changes):
        validate_job_automation(
            changes.get("file_id", current.file_id),
            auto_restart=bool(changes.get("auto_restart", current.auto_restart)),
            auto_clear=bool(changes.get("auto_clear", current.auto_clear)),
        )
    if {
        "allowed_printer_ids",
        "auto_restart",
        "target_printer_id",
    }.intersection(changes):
        validate_unattended_start_scope(
            auto_restart=bool(changes.get("auto_restart", current.auto_restart)),
            target_printer_id=changes.get(
                "target_printer_id",
                current.target_printer_id,
            ),
            allowed_printer_ids=changes.get(
                "allowed_printer_ids",
                current.allowed_printer_ids,
            ),
        )
    gate_revocation = bool(safe_revocations) or (
        changes.get("dispatch_paused") is True
    )
    start_gate_change = bool(
        {"auto_restart", "dispatch_paused"}.intersection(changes)
    )
    start_revocation = (
        changes.get("auto_restart") is False
        or changes.get("dispatch_paused") is True
    )
    requested_rearm_epoch = (
        job_automation_revocation_epoch(job_id)
        if start_gate_change
        and not start_revocation
        and (
            changes.get("auto_restart") is True
            or changes.get("dispatch_paused") is False
        )
        else None
    )
    revocation = (
        begin_job_automation_revocation(job_id)
        if start_revocation
        else job_automation_revocation(job_id)
    )
    try:
        if gate_revocation or start_gate_change:
            with job_automation_start_gate(job_id):
                if (
                    requested_rearm_epoch is not None
                    and job_automation_revocation_epoch(job_id)
                    != requested_rearm_epoch
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "This task was disarmed while the re-arm request "
                            "was waiting. Review it before trying again."
                        ),
                    )
                job = database.update_job(job_id, payload)
                if job and start_gate_change:
                    if job.auto_restart and not job.dispatch_paused:
                        if not job_automation_revocation_is_pending(job_id):
                            revocation.clear()
                    else:
                        revocation.set()
        else:
            job = database.update_job(job_id, payload)
    except database.QueueConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    finally:
        if start_revocation:
            end_job_automation_revocation(job_id)
    if not job:
        raise missing("Job")
    return job


@app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_job(job_id: str) -> None:
    current = database.get_job(job_id)
    if not current:
        raise missing("Job")
    if not database.delete_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A queue task with run history, or an individual run record, cannot be removed.",
        )


@app.post("/api/jobs/{job_id}/complete", response_model=JobRecord)
def complete_job(job_id: str, payload: JobCompletionRequest) -> JobRecord:
    if payload.confirmation != "COMPLETE":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirm this action with COMPLETE.")
    current = database.get_job(job_id)
    if not current:
        raise missing("Job")
    if current.target_printer_id:
        with printer_command_lock(current.target_printer_id):
            return _complete_job_locked(job_id)
    return _complete_job_locked(job_id)


def _complete_job_locked(job_id: str) -> JobRecord:
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
    job = database.complete_job(job_id, trusted_auto_clear=False)
    if not job:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This job could not be completed safely.")
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No ready, connected printer with file delivery satisfies this job's "
                "printer, nozzle, tag, and filament requirements."
            ),
        )
    selected = candidates[0]
    if payload and payload.printer_id:
        selected = next((candidate for candidate in candidates if candidate.printer_id == payload.printer_id), None)
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That printer is no longer a compatible ready route for this job.",
            )
    try:
        updated = database.route_job(job_id, selected.printer_id, selected.reason)
    except database.QueueConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
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
    printer_model: str = Form(""),
    printer_manufacturer: str = Form(""),
    nozzle_diameter: float | None = Form(None),
    filament_profiles: str = Form("[]", max_length=8_000),
) -> FileRecord:
    original_filename = file.filename or ""
    suffix = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if suffix not in {"gcode", "g", "gc", "3mf", "bgcode"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a .gcode, .3mf, .bgcode, .g, or .gc print file.")
    try:
        decoded_profiles = json.loads(filament_profiles)
    except (TypeError, ValueError):
        decoded_profiles = [item.strip() for item in filament_profiles.split(",") if item.strip()]
    if not isinstance(decoded_profiles, list) or not all(isinstance(item, str) for item in decoded_profiles):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="filament_profiles must be a JSON array of strings.",
        )
    payload = FileCreate(
        name=name.strip(), filename=original_filename, file_type=suffix,
        material=material.strip(), estimated_minutes=estimated_minutes, notes=notes.strip(),
        auto_eject_verified=auto_eject_verified,
        printer_manufacturer=printer_manufacturer.strip(), printer_model=printer_model.strip(),
        nozzle_diameter=nozzle_diameter, filament_profiles=decoded_profiles,
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
        for adapter_type in ("octoprint", "moonraker", "prusalink", "bambu_lan", "simulator", "manual")
    ]


@app.post("/api/printers/{printer_id}/connection", response_model=PrinterConnectionResponse)
def connect_printer(printer_id: str, payload: PrinterConnectionRequest) -> PrinterConnectionResponse:
    with printer_command_lock(printer_id):
        with _CONNECTION_IDENTITY_LOCK:
            return _connect_printer_locked(printer_id, payload)


def _connect_printer_locked(
    printer_id: str,
    payload: PrinterConnectionRequest,
) -> PrinterConnectionResponse:
    printer = database.get_printer(printer_id)
    if not printer:
        raise missing("Printer")
    if printer.active_job_id or printer.state in {"starting", "printing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finish or cancel active work before changing this printer's connection.",
        )
    pending_credential_id: str | None = None
    existing_credential_id = database.credential_id_for_printer(printer_id)
    try:
        adapter = get_adapter(payload.adapter_type)
        if payload.adapter_type == "prusalink":
            credentials_supplied = payload.username is not None or payload.password is not None
            credential = (
                {"username": payload.username or "", "password": payload.password or ""}
                if credentials_supplied
                else load_secret(existing_credential_id)
            )
        else:
            credentials_supplied = payload.api_key is not None
            credential = (
                {"api_key": payload.api_key or ""}
                if credentials_supplied
                else load_secret(existing_credential_id)
            )
        connection_endpoint_keys = (
            canonical_connection_endpoints(payload.host)
            if payload.adapter_type in {"octoprint", "moonraker", "prusalink"}
            else []
        )
        connection_endpoint = (
            connection_endpoint_keys[0] if connection_endpoint_keys else None
        )
        ensure_endpoint_available_for_connection(
            printer_id,
            connection_endpoint_keys,
        )
        snapshot = adapter.snapshot(payload.host, credential)
        if credentials_supplied:
            has_credential = any(
                value if key == "password" else value.strip()
                for key, value in credential.items()
            )
            if has_credential:
                pending_credential_id = f"printer:{printer_id}:{uuid.uuid4()}"
                save_secret(pending_credential_id, credential)
                credential_id = pending_credential_id
            else:
                credential_id = None
        else:
            credential_id = existing_credential_id
        updated = database.configure_connection(
            printer_id,
            adapter_type=payload.adapter_type,
            host=payload.host.strip(),
            credential_id=credential_id,
            capabilities=adapter_capabilities(payload.adapter_type),
            snapshot=snapshot,
            connection_endpoint=connection_endpoint,
            connection_endpoint_keys=connection_endpoint_keys,
        )
        if (
            updated
            and payload.adapter_type == "octoprint"
            and updated.auto_start_enabled
        ):
            updated = database.update_printer(
                printer_id,
                PrinterUpdate(auto_start_enabled=False),
            )
    except database.QueueConflictError as error:
        remove_secret(pending_credential_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (AdapterError, SecretStoreError) as error:
        remove_secret(pending_credential_id)
        raise connection_error(printer_id, error) from error
    if not updated:
        remove_secret(pending_credential_id)
        raise missing("Printer")
    if credentials_supplied and existing_credential_id != credential_id:
        remove_secret(existing_credential_id)
    return PrinterConnectionResponse(
        printer=updated,
        capabilities=updated.capabilities,
        message="Local printer connection verified. Monitoring is now enabled.",
    )


@app.post("/api/jobs/{job_id}/deliver", response_model=JobRecord)
def deliver_job(job_id: str) -> JobRecord:
    initial_job = database.get_job(job_id)
    if not initial_job:
        raise missing("Job")
    if not initial_job.target_printer_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Assign this job to a printer before uploading it.")
    printer_id = initial_job.target_printer_id
    with printer_command_lock(printer_id):
        job = database.get_job(job_id)
        if not job:
            raise missing("Job")
        if (
            job.parent_job_id
            or job.state != "queued"
            or job.target_printer_id != printer_id
            or database.job_has_active_runs(job.id)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an idle queued parent task can be uploaded.",
            )
        if not job.file_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Choose an uploaded file for this job before sending it.")
        printer = database.get_printer(printer_id)
        stored = database.stored_file(job.file_id)
        if not printer:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The assigned printer is no longer available.")
        if printer.connection_state != "connected":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reconnect this printer before uploading a file.")
        if printer.active_job_id or printer.state in {"starting", "printing"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This printer already owns active work; wait before uploading another task.",
            )
        if not stored:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The job file is only a catalog record. Upload the actual file first.")
        record, path = stored
        adapter = get_adapter(printer.adapter_type)
        if not adapter.capabilities.file_transfer:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{adapter.label} file transfer is not available in this release.",
            )
        credential_id = database.credential_id_for_printer(printer.id)
        try:
            verify_printer_connection_identity(printer)
            credential = load_secret(credential_id)
            remote_path = adapter.upload(
                printer.host,
                credential,
                path,
                record.filename,
            )
        except (AdapterError, SecretStoreError) as error:
            database.log_automation_event("warning", f"{job.name} could not be uploaded: {error}")
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
        updated = database.mark_job_delivered_if_current(
            job_id,
            remote_path,
            printer_id=printer.id,
            file_id=record.id,
            adapter_type=printer.adapter_type,
            host=printer.host,
            credential_id=credential_id,
            expected_delivery_state=job.delivery_state,
            expected_remote_file_path=job.remote_file_path,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The task, file, or printer connection changed during upload. "
                    "Review it and upload again before starting."
                ),
            )
        return updated


@app.post("/api/jobs/{job_id}/control", response_model=JobRecord)
def control_job(job_id: str, payload: JobControlRequest) -> JobRecord:
    if payload.confirmation != payload.action.upper():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Confirm this action with {payload.action.upper()}.")
    initial_job = database.get_job(job_id)
    if not initial_job:
        raise missing("Job")
    if not initial_job.target_printer_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This job has no assigned printer.")
    cancel_intent = (
        run_cancel_intent(job_id)
        if payload.action == "cancel"
        and initial_job.state in {"assigned", "starting"}
        else None
    )
    if cancel_intent:
        cancel_intent.set()
    expected_printer_id = initial_job.target_printer_id
    with printer_command_lock(expected_printer_id):
        job = database.get_job(job_id)
        if not job:
            raise missing("Job")
        if (
            payload.action == "cancel"
            and cancel_intent
            and job.state == "cancelled"
        ):
            return job
        if job.target_printer_id != expected_printer_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The task's assigned printer changed; review it and try again.",
            )
        allowed_states = {
            "start": {"queued", "assigned"},
            "pause": {"running"},
            "resume": {"paused"},
            "cancel": {"assigned", "starting", "running", "paused"},
        }
        if job.state not in allowed_states[payload.action]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A {payload.action} command is not available while this job is {job.state}.",
            )
        printer = database.get_printer(expected_printer_id)
        if not printer:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The assigned printer is no longer available.")
        if payload.action == "start":
            if job.parent_job_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An individual run cannot be started twice.")
            if job.delivery_state != "uploaded" or not job.remote_file_path:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload this file to the printer before starting it.")
            if not any(candidate.printer_id == printer.id for candidate in route_candidates(job)):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The assigned printer is not currently a safe compatible route for this task.",
                )
            if not loaded_material_matches(job, printer):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Load the task's required material or filament profiles before starting it.",
                )
        starting_recovery = payload.action == "cancel" and job.state in {"assigned", "starting"}
        if payload.action in {"pause", "resume", "cancel"}:
            if printer.active_job_id != job.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Another run owns this printer, so this command was blocked for safety.",
                )
        run: JobRecord | None = None
        command_gate: threading.Lock | None = None
        try:
            command_gate, printer = acquire_verified_physical_command_gate(
                printer.id
            )
            adapter = get_adapter(printer.adapter_type)
            if not adapter.capabilities.job_control:
                raise AdapterError(f"{adapter.label} job control is not available in this release.")
            credential = load_secret(database.credential_id_for_printer(printer.id))
            if payload.action == "start":
                snapshot = adapter.snapshot(printer.host, credential)
                fresh_printer = database.apply_snapshot(printer.id, snapshot)
                if not fresh_printer:
                    raise database.QueueConflictError(
                        "The assigned printer was removed before fresh pre-start telemetry was applied."
                    )
                if snapshot.state != "ready" or snapshot.active_job:
                    raise database.QueueConflictError(
                        "Fresh printer telemetry is not idle: manual START requires "
                        "a ready printer with no active file."
                    )
                if not any(
                    candidate.printer_id == fresh_printer.id
                    for candidate in route_candidates(job)
                ):
                    raise database.QueueConflictError(
                        "Fresh printer telemetry no longer satisfies the task's safe "
                        "route and clearance requirements."
                    )
                if not loaded_material_matches(job, fresh_printer):
                    raise database.QueueConflictError(
                        "Load the task's required material or filament profiles "
                        "before starting it."
                    )
                printer = fresh_printer
                run = database.claim_job_for_auto_start(
                    job.id,
                    printer.id,
                    expected_job=job,
                )
                if not run:
                    raise database.QueueConflictError(
                        "This task repetition or printer was claimed by another request."
                    )
                job = run
            skip_command = False
            if payload.action in {"pause", "resume", "cancel"}:
                snapshot = adapter.snapshot(printer.host, credential)
                reported_filename = snapshot.active_job
                expected_names = {job.filename}
                if job.remote_file_path:
                    expected_names.add(Path(job.remote_file_path).name)
                if reported_filename:
                    if Path(reported_filename).name not in expected_names:
                        raise database.QueueConflictError(
                            "The printer reports a different active file, so this command was blocked."
                        )
                elif not (starting_recovery and snapshot.state == "ready"):
                    raise database.QueueConflictError(
                        "Fresh printer telemetry did not identify this run's active file, so the command was blocked."
                    )
                if not starting_recovery:
                    expected_states = {
                        "pause": {"printing"},
                        "resume": {"attention"},
                        "cancel": {"printing", "attention"},
                    }[payload.action]
                    if snapshot.state not in expected_states:
                        raise database.QueueConflictError(
                            f"Fresh printer telemetry is {snapshot.state}, so {payload.action} was blocked."
                        )
                skip_command = (
                    starting_recovery
                    and snapshot.state == "ready"
                    and not reported_filename
                )
            if not skip_command:
                adapter.command(
                    printer.host,
                    credential,
                    payload.action,
                    job.remote_file_path,
                )
            if payload.action == "start":
                updated = database.update_job(job.id, JobUpdate(state="running"))
            elif payload.action == "pause":
                updated = database.update_job(job.id, JobUpdate(state="paused"))
            elif payload.action == "resume":
                updated = database.update_job(job.id, JobUpdate(state="running"))
            elif payload.action == "cancel":
                updated = database.terminate_job(
                    job.id,
                    "cancelled",
                    "The run was cancelled; remove material and confirm READY before continuing.",
                    release_ownership=True,
                )
            else:
                updated = database.get_job(job.id)
        except database.QueueConflictError as error:
            if run:
                database.fail_auto_start_job(run.id, str(error))
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except (AdapterError, SecretStoreError) as error:
            if run:
                database.fail_auto_start_job(run.id, str(error))
            raise connection_error(
                printer.id,
                error,
                start_gate_held=command_gate is not None,
            ) from error
        finally:
            if command_gate:
                command_gate.release()
        database.log_job_control(job.id, payload.action)
        if not updated:
            raise missing("Job")
        return updated


@app.post("/api/printers/{printer_id}/refresh")
def refresh_printer(printer_id: str) -> PrinterRecord:
    try:
        return refresh_and_dispatch(printer_id)
    except (AdapterError, SecretStoreError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
