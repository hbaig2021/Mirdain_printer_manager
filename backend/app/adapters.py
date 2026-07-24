from __future__ import annotations

import ipaddress
import json
import socket
import threading
from pathlib import Path
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    telemetry: bool
    file_transfer: bool
    job_control: bool
    camera: bool


@dataclass(frozen=True, slots=True)
class AdapterSnapshot:
    state: str
    percent_complete: int | None
    active_job: str | None
    bed_temp: int | None
    nozzle_temp: int | None
    note: str | None
    seen_at: datetime
    job_outcome: str | None = None


class AdapterError(RuntimeError):
    pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)


class PrinterAdapter:
    """A local printer protocol implementation.

    This boundary keeps vendor-specific requests out of the dashboard and makes
    it possible to add new printer families without changing queue logic.
    It provides telemetry everywhere, and file/control methods only where a
    local protocol implementation has been deliberately validated.
    """

    key: str
    label: str
    capabilities: AdapterCapabilities

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        raise NotImplementedError

    def upload(self, host: str, credential: dict[str, str], source: Path, remote_name: str) -> str:
        raise AdapterError(f"{self.label} file transfer is not available yet.")

    def command(self, host: str, credential: dict[str, str], action: str, remote_path: str | None = None) -> None:
        del host, credential, action, remote_path
        raise AdapterError(f"{self.label} job control is not available yet.")

    @staticmethod
    def _url(host: str, path: str) -> str:
        normalized = host.strip().rstrip("/")
        if not normalized:
            raise AdapterError("Enter the printer's local IP address or hostname first.")
        if not normalized.startswith(("http://", "https://")):
            normalized = f"http://{normalized}"
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AdapterError("Enter a valid local HTTP or HTTPS printer address.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AdapterError("Printer addresses cannot contain credentials, query strings, or fragments.")
        _require_local_hostname(parsed.hostname)
        base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        return f"{base}{path}"

    @staticmethod
    def _json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json", **(headers or {})}, method="GET")
        try:
            with _NO_REDIRECT_OPENER.open(request, timeout=4) as response:  # noqa: S310 - validated LAN endpoint
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise AdapterError("The printer rejected the API key. Check it and try again.") from exc
            raise AdapterError(f"The printer returned HTTP {exc.code} while checking its status.") from exc
        except (URLError, TimeoutError) as exc:
            raise AdapterError("The printer could not be reached on the local network.") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise AdapterError("The printer returned an unreadable status response.") from exc
        if not isinstance(payload, dict):
            raise AdapterError("The printer returned an unexpected status response.")
        return payload

    @staticmethod
    def _api_key(credential: dict[str, str]) -> dict[str, str]:
        key = credential.get("api_key", "").strip()
        return {"X-Api-Key": key} if key else {}


class OctoPrintAdapter(PrinterAdapter):
    key = "octoprint"
    label = "OctoPrint"
    capabilities = AdapterCapabilities(telemetry=True, file_transfer=True, job_control=True, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        headers = self._api_key(credential)
        printer = self._json(self._url(host, "/api/printer"), headers)
        job = self._json(self._url(host, "/api/job"), headers)
        state_text = str(printer.get("state", {}).get("text", "")).lower()
        flags = printer.get("state", {}).get("flags", {})
        if flags.get("printing"):
            state = "printing"
        elif flags.get("paused"):
            state = "attention"
        elif flags.get("operational"):
            state = "ready"
        else:
            state = "attention" if state_text else "offline"
        progress = job.get("progress", {}).get("completion")
        temperatures = printer.get("temperature", {})
        bed = temperatures.get("bed", {}).get("actual") if isinstance(temperatures.get("bed"), dict) else None
        tool = temperatures.get("tool0", {}).get("actual") if isinstance(temperatures.get("tool0"), dict) else None
        filename = job.get("job", {}).get("file", {}).get("name") if isinstance(job.get("job"), dict) else None
        return AdapterSnapshot(
            state=state,
            percent_complete=_percent(progress),
            active_job=str(filename) if filename else None,
            bed_temp=_temperature(bed),
            nozzle_temp=_temperature(tool),
            note=state_text or None,
            seen_at=datetime.now(UTC),
            # The polled OctoPrint job endpoint does not distinguish a
            # successful PrintDone event from every late cancel/error case.
            # Never release a bed from this ambiguous snapshot.
            job_outcome=None,
        )

    def upload(self, host: str, credential: dict[str, str], source: Path, remote_name: str) -> str:
        response = _upload_multipart(
            self._url(host, "/api/files/local"), self._api_key(credential), source, remote_name,
        )
        path = response.get("files", {}).get("local", {}).get("path") if isinstance(response.get("files"), dict) else None
        if not isinstance(path, str) or not path:
            raise AdapterError("OctoPrint accepted the upload but did not return the remote file path.")
        return path

    def command(self, host: str, credential: dict[str, str], action: str, remote_path: str | None = None) -> None:
        headers = self._api_key(credential)
        if action == "start":
            if not remote_path:
                raise AdapterError("Upload the job to OctoPrint before starting it.")
            _post_json(
                self._url(host, f"/api/files/local/{_path_component(remote_path)}"),
                headers,
                {"command": "select", "print": True},
            )
            return
        if action in {"pause", "resume"}:
            _post_json(self._url(host, "/api/job"), headers, {"command": "pause", "action": action})
            return
        if action == "cancel":
            _post_json(self._url(host, "/api/job"), headers, {"command": "cancel"})
            return
        raise AdapterError(f"{action} is not a supported OctoPrint job command.")


class MoonrakerAdapter(PrinterAdapter):
    key = "moonraker"
    label = "Moonraker / Klipper"
    capabilities = AdapterCapabilities(telemetry=True, file_transfer=True, job_control=True, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        headers = self._api_key(credential)
        parameters = urlencode({"webhooks": "", "virtual_sdcard": "", "print_stats": "", "extruder": "temperature,target", "heater_bed": "temperature,target"})
        payload = self._json(self._url(host, f"/printer/objects/query?{parameters}"), headers)
        status = payload.get("result", {}).get("status", {}) if isinstance(payload.get("result"), dict) else {}
        if not isinstance(status, dict) or not status:
            raise AdapterError("Moonraker is reachable, but Klipper did not return printer status.")
        webhooks = status.get("webhooks", {}) if isinstance(status.get("webhooks"), dict) else {}
        print_stats = status.get("print_stats", {}) if isinstance(status.get("print_stats"), dict) else {}
        raw_state = str(print_stats.get("state", "standby")).lower()
        klippy = str(webhooks.get("state", "ready")).lower()
        if raw_state == "printing":
            state = "printing"
        elif raw_state in {"paused", "error", "cancelled"} or klippy != "ready":
            state = "attention"
        else:
            state = "ready"
        job_outcome = {
            "complete": "completed",
            "cancelled": "cancelled",
            "error": "failed",
        }.get(raw_state)
        extruder = status.get("extruder", {}) if isinstance(status.get("extruder"), dict) else {}
        bed = status.get("heater_bed", {}) if isinstance(status.get("heater_bed"), dict) else {}
        progress = status.get("virtual_sdcard", {}).get("progress") if isinstance(status.get("virtual_sdcard"), dict) else None
        message = str(print_stats.get("message") or webhooks.get("state_message") or "").strip()
        return AdapterSnapshot(
            state=state,
            percent_complete=_percent(float(progress) * 100 if isinstance(progress, (int, float)) else None),
            active_job=(
                str(print_stats["filename"])
                if print_stats.get("filename")
                else None
            ),
            bed_temp=_temperature(bed.get("temperature")),
            nozzle_temp=_temperature(extruder.get("temperature")),
            note=message or None,
            seen_at=datetime.now(UTC),
            job_outcome=job_outcome,
        )

    def upload(self, host: str, credential: dict[str, str], source: Path, remote_name: str) -> str:
        response = _upload_multipart(
            self._url(host, "/server/files/upload"), self._api_key(credential), source, remote_name, {"root": "gcodes"},
        )
        item = response.get("item", {}) if isinstance(response.get("item"), dict) else {}
        path = item.get("path")
        if not isinstance(path, str) or not path:
            raise AdapterError("Moonraker accepted the upload but did not return the remote file path.")
        return path

    def command(self, host: str, credential: dict[str, str], action: str, remote_path: str | None = None) -> None:
        headers = self._api_key(credential)
        if action == "start":
            if not remote_path:
                raise AdapterError("Upload the job to Moonraker before starting it.")
            _post_json(self._url(host, "/printer/print/start"), headers, {"filename": remote_path})
            return
        if action in {"pause", "resume", "cancel"}:
            _post_json(self._url(host, f"/printer/print/{action}"), headers, {})
            return
        raise AdapterError(f"{action} is not a supported Moonraker job command.")


class PrusaLinkAdapter(PrinterAdapter):
    key = "prusalink"
    label = "PrusaLink"
    capabilities = AdapterCapabilities(telemetry=True, file_transfer=True, job_control=True, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        auth = _digest_auth(credential)
        payload = _get_json_request(
            self._url(host, "/api/v1/status"),
            auth=auth,
        )
        job_detail = _get_optional_json_request(
            self._url(host, "/api/v1/job"),
            auth=auth,
        )
        printer = payload.get("printer", {}) if isinstance(payload.get("printer"), dict) else {}
        job = payload.get("job", {}) if isinstance(payload.get("job"), dict) else {}
        raw_state = str(printer.get("state", "UNKNOWN")).upper()
        if raw_state in {"PRINTING", "PAUSED"}:
            state = "printing" if raw_state == "PRINTING" else "attention"
        elif raw_state in {"IDLE", "READY", "FINISHED", "STOPPED"}:
            state = "ready"
        else:
            state = "attention"
        job_outcome = {
            "FINISHED": "completed",
            "STOPPED": "cancelled",
            "ERROR": "failed",
        }.get(raw_state)
        progress = job.get("progress")
        temperatures = printer.get("temp_bed"), printer.get("temp_nozzle")
        detailed_job = job_detail if isinstance(job_detail, dict) else {}
        active = (
            detailed_job.get("file", {}).get("name")
            if isinstance(detailed_job.get("file"), dict)
            else detailed_job.get("filename")
        )
        return AdapterSnapshot(
            state=state,
            percent_complete=_percent(progress),
            active_job=str(active) if active else None,
            bed_temp=_temperature(temperatures[0]),
            nozzle_temp=_temperature(temperatures[1]),
            note=str(printer.get("state")) if printer.get("state") else None,
            seen_at=datetime.now(UTC),
            job_outcome=job_outcome,
        )

    def upload(self, host: str, credential: dict[str, str], source: Path, remote_name: str) -> str:
        safe_name = quote(Path(remote_name).name, safe="")
        _put_binary(
            self._url(host, f"/api/v1/files/local/{safe_name}"),
            {
                "Overwrite": "?0",
                "Print-After-Upload": "?0",
            },
            source,
            auth=_digest_auth(credential),
        )
        return f"local/{Path(remote_name).name}"

    def command(self, host: str, credential: dict[str, str], action: str, remote_path: str | None = None) -> None:
        auth = _digest_auth(credential)
        if action == "start":
            if not remote_path:
                raise AdapterError("Upload the job to PrusaLink before starting it.")
            storage_path = quote(remote_path.lstrip("/"), safe="/")
            _request_empty(
                "POST",
                self._url(host, f"/api/v1/files/{storage_path}"),
                {},
                auth=auth,
            )
            return
        if action not in {"pause", "resume", "cancel"}:
            raise AdapterError(f"{action} is not a supported PrusaLink job command.")
        job = _get_json_request(self._url(host, "/api/v1/job"), auth=auth)
        job_id = job.get("id")
        if job_id is None and isinstance(job.get("job"), dict):
            job_id = job["job"].get("id")
        if not isinstance(job_id, int):
            raise AdapterError("PrusaLink did not report an active job identifier.")
        if action == "cancel":
            _request_empty(
                "DELETE",
                self._url(host, f"/api/v1/job/{job_id}"),
                {},
                auth=auth,
            )
        else:
            _request_empty(
                "PUT",
                self._url(host, f"/api/v1/job/{job_id}/{action}"),
                {},
                auth=auth,
            )


class ManualAdapter(PrinterAdapter):
    key = "manual"
    label = "Manual tracking"
    capabilities = AdapterCapabilities(telemetry=False, file_transfer=False, job_control=False, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        del host, credential
        raise AdapterError("Manual tracking does not have a printer connection to test.")


class BambuLanAdapter(PrinterAdapter):
    key = "bambu_lan"
    label = "Bambu printer (record only; LAN control intentionally disabled)"
    capabilities = AdapterCapabilities(telemetry=False, file_transfer=False, job_control=False, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        del host, credential
        raise AdapterError(
            "Generic Bambu LAN control is intentionally unsupported. A future, separate "
            "official Fleet Hub integration would require Bambu provisioning, Fleet Hub "
            "hardware, terms review, and operator-supplied credentials."
        )


class SimulatorAdapter(PrinterAdapter):
    """A no-hardware adapter for evaluation, training, and integration tests."""

    key = "simulator"
    label = "Simulated printer"
    capabilities = AdapterCapabilities(telemetry=True, file_transfer=True, job_control=True, camera=False)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        del credential
        key = host.strip() or "simulator"
        with self._lock:
            state = dict(
                self._states.get(
                    key,
                    {
                        "state": "ready",
                        "percent_complete": None,
                        "active_job": None,
                        "job_outcome": None,
                    },
                )
            )
            if state.get("job_outcome"):
                self._states[key] = {
                    "state": "ready",
                    "percent_complete": None,
                    "active_job": None,
                    "job_outcome": None,
                }
        return AdapterSnapshot(
            state=str(state["state"]),
            percent_complete=state.get("percent_complete"),
            active_job=state.get("active_job"),
            bed_temp=25,
            nozzle_temp=30,
            note="Simulated local printer",
            seen_at=datetime.now(UTC),
            job_outcome=state.get("job_outcome"),
        )

    def upload(self, host: str, credential: dict[str, str], source: Path, remote_name: str) -> str:
        del host, credential
        if not source.is_file():
            raise AdapterError("The simulated upload source does not exist.")
        return f"simulator/{Path(remote_name).name}"

    def command(self, host: str, credential: dict[str, str], action: str, remote_path: str | None = None) -> None:
        del credential
        key = host.strip() or "simulator"
        with self._lock:
            current = self._states.get(key, {})
            if action == "start":
                if not remote_path:
                    raise AdapterError("Upload the simulated job before starting it.")
                self._states[key] = {
                    "state": "printing",
                    "percent_complete": 1,
                    "active_job": Path(remote_path).name,
                    "job_outcome": None,
                }
            elif action == "pause":
                self._states[key] = {**current, "state": "attention"}
            elif action == "resume":
                self._states[key] = {**current, "state": "printing"}
            elif action == "cancel":
                self._states[key] = {
                    **current,
                    "state": "ready",
                    "percent_complete": None,
                    "job_outcome": "cancelled",
                }
            else:
                raise AdapterError(f"{action} is not a supported simulator job command.")


def _percent(value: Any) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0, min(100, round(float(value))))


def _temperature(value: Any) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value))


def _path_component(value: str) -> str:
    # Preserve folder separators but prohibit URL manipulation from a stored path.
    return quote(value.lstrip("/"), safe="/")


def resolve_local_addresses(hostname: str) -> set[str]:
    """Resolve and validate all addresses for a local printer hostname.

    Printer integrations are deliberately LAN-only. Every hostname, including
    single-label and `.local` names, must resolve exclusively to loopback,
    RFC1918/ULA, or link-local addresses at request construction time.
    """

    normalized = hostname.rstrip(".").casefold()

    def allowed(value: str) -> bool:
        address = ipaddress.ip_address(value)
        if address.is_loopback or address.is_link_local:
            return True
        if isinstance(address, ipaddress.IPv4Address):
            return any(
                address in network
                for network in (
                    ipaddress.ip_network("10.0.0.0/8"),
                    ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"),
                )
            )
        return address in ipaddress.ip_network("fc00::/7")

    try:
        if allowed(normalized):
            return {ipaddress.ip_address(normalized).compressed}
        raise AdapterError("Printer connections are limited to private local-network addresses.")
    except ValueError:
        pass

    try:
        results = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise AdapterError("The printer hostname could not be resolved on the local network.") from exc
    addresses = {result[4][0] for result in results}
    if not addresses or not all(allowed(value) for value in addresses):
        raise AdapterError("Printer connections are limited to private local-network addresses.")
    return {ipaddress.ip_address(value).compressed for value in addresses}


def _require_local_hostname(hostname: str) -> None:
    """Reject public destinations before a local printer request is attempted."""
    resolve_local_addresses(hostname)


def _upload_multipart(
    url: str,
    headers: dict[str, str],
    source: Path,
    remote_name: str,
    fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise AdapterError("The file-transfer component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    try:
        with source.open("rb") as handle:
            response = requests.post(
                url,
                headers=headers,
                data=fields or {},
                files={"file": (remote_name, handle, "application/octet-stream")},
                timeout=(4, 120),
                allow_redirects=False,
            )
        _raise_for_response(response)
        payload = response.json()
    except OSError as exc:
        raise AdapterError("The local print file could not be read.") from exc
    except Exception as exc:
        if exc.__class__.__module__.startswith("requests"):
            raise AdapterError("The printer could not accept the file upload.") from exc
        raise
    if not isinstance(payload, dict):
        raise AdapterError("The printer returned an invalid upload response.")
    return payload


def _digest_auth(credential: dict[str, str]) -> Any:
    username = credential.get("username", "").strip()
    password = credential.get("password", "")
    if not username or not password:
        raise AdapterError("Enter the PrusaLink username and password.")
    try:
        from requests.auth import HTTPDigestAuth
    except ImportError as exc:
        raise AdapterError(
            "The PrusaLink authentication component is unavailable. Reinstall Mirdain Print Hub and try again."
        ) from exc
    return HTTPDigestAuth(username, password)


def _request_json(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    auth: Any = None,
    allow_empty: bool = False,
) -> dict[str, Any] | None:
    try:
        import requests
    except ImportError as exc:
        raise AdapterError(
            "The printer connection component is unavailable. Reinstall Mirdain Print Hub and try again."
        ) from exc
    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json", **(headers or {})},
            auth=auth,
            timeout=(4, 20),
            allow_redirects=False,
        )
        if allow_empty and response.status_code == 204:
            return None
        _raise_for_response(response)
        payload = response.json()
    except Exception as exc:
        if exc.__class__.__module__.startswith("requests"):
            raise AdapterError("The printer could not be reached on the local network.") from exc
        raise
    if not isinstance(payload, dict):
        raise AdapterError("The printer returned an unexpected status response.")
    return payload


def _get_json_request(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    auth: Any = None,
) -> dict[str, Any]:
    payload = _request_json(url, headers, auth=auth)
    if payload is None:
        raise AdapterError("The printer returned an unexpected empty response.")
    return payload


def _get_optional_json_request(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    auth: Any = None,
) -> dict[str, Any] | None:
    return _request_json(url, headers, auth=auth, allow_empty=True)


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> None:
    try:
        import requests
    except ImportError as exc:
        raise AdapterError("The job-control component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", **headers},
            json=payload,
            timeout=(4, 20),
            allow_redirects=False,
        )
        _raise_for_response(response)
    except Exception as exc:
        if exc.__class__.__module__.startswith("requests"):
            raise AdapterError("The printer rejected the requested job command.") from exc
        raise


def _put_binary(
    url: str,
    headers: dict[str, str],
    source: Path,
    *,
    auth: Any = None,
) -> None:
    try:
        import requests
    except ImportError as exc:
        raise AdapterError("The file-transfer component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    try:
        with source.open("rb") as handle:
            response = requests.put(
                url,
                headers={"Content-Type": "application/octet-stream", **headers},
                data=handle,
                auth=auth,
                timeout=(4, 120),
                allow_redirects=False,
            )
        _raise_for_response(response)
    except OSError as exc:
        raise AdapterError("The local print file could not be read.") from exc
    except Exception as exc:
        if exc.__class__.__module__.startswith("requests"):
            raise AdapterError("The printer could not accept the file upload.") from exc
        raise


def _request_empty(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    auth: Any = None,
) -> None:
    try:
        import requests
    except ImportError as exc:
        raise AdapterError("The job-control component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            auth=auth,
            timeout=(4, 20),
            allow_redirects=False,
        )
        _raise_for_response(response)
    except Exception as exc:
        if exc.__class__.__module__.startswith("requests"):
            raise AdapterError("The printer rejected the requested job command.") from exc
        raise


def _raise_for_response(response: Any) -> None:
    if response.status_code in {401, 403}:
        raise AdapterError("The printer rejected the API key. Check it and try again.")
    if 300 <= response.status_code < 400:
        raise AdapterError("Printer API redirects are blocked to keep credentials and uploads on the local endpoint.")
    if response.status_code >= 400:
        raise AdapterError(f"The printer returned HTTP {response.status_code}.")


ADAPTERS: dict[str, PrinterAdapter] = {
    "manual": ManualAdapter(),
    "simulator": SimulatorAdapter(),
    "octoprint": OctoPrintAdapter(),
    "moonraker": MoonrakerAdapter(),
    "prusalink": PrusaLinkAdapter(),
    "bambu_lan": BambuLanAdapter(),
}


def get_adapter(adapter_type: str) -> PrinterAdapter:
    try:
        return ADAPTERS[adapter_type]
    except KeyError as exc:
        raise AdapterError(f"{adapter_type} is not a supported printer adapter.") from exc
