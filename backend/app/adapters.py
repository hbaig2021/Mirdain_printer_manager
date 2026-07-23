from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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


class AdapterError(RuntimeError):
    pass


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
        return f"{normalized}{path}"

    @staticmethod
    def _json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json", **(headers or {})}, method="GET")
        try:
            with urlopen(request, timeout=4) as response:  # noqa: S310 - endpoint is a user-owned local printer
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
            _post_json(self._url(host, f"/api/files/local/{_path_component(remote_path)}"), headers, {"command": "select", "print": False})
            _post_json(self._url(host, "/api/job"), headers, {"command": "start"})
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
        extruder = status.get("extruder", {}) if isinstance(status.get("extruder"), dict) else {}
        bed = status.get("heater_bed", {}) if isinstance(status.get("heater_bed"), dict) else {}
        progress = status.get("virtual_sdcard", {}).get("progress") if isinstance(status.get("virtual_sdcard"), dict) else None
        message = str(print_stats.get("message") or webhooks.get("state_message") or "").strip()
        return AdapterSnapshot(
            state=state,
            percent_complete=_percent(float(progress) * 100 if isinstance(progress, (int, float)) else None),
            active_job=str(print_stats.get("filename")) or None,
            bed_temp=_temperature(bed.get("temperature")),
            nozzle_temp=_temperature(extruder.get("temperature")),
            note=message or None,
            seen_at=datetime.now(UTC),
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
    capabilities = AdapterCapabilities(telemetry=True, file_transfer=False, job_control=False, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        payload = self._json(self._url(host, "/api/v1/status"), self._api_key(credential))
        printer = payload.get("printer", {}) if isinstance(payload.get("printer"), dict) else {}
        job = payload.get("job", {}) if isinstance(payload.get("job"), dict) else {}
        raw_state = str(printer.get("state", "UNKNOWN")).upper()
        if raw_state in {"PRINTING", "PAUSED"}:
            state = "printing" if raw_state == "PRINTING" else "attention"
        elif raw_state in {"IDLE", "READY", "FINISHED", "STOPPED"}:
            state = "ready"
        else:
            state = "attention"
        progress = job.get("progress") or job.get("time_printing")
        temperatures = printer.get("temp_bed"), printer.get("temp_nozzle")
        active = job.get("file", {}).get("name") if isinstance(job.get("file"), dict) else job.get("filename")
        return AdapterSnapshot(
            state=state,
            percent_complete=_percent(progress),
            active_job=str(active) if active else None,
            bed_temp=_temperature(temperatures[0]),
            nozzle_temp=_temperature(temperatures[1]),
            note=str(printer.get("state")) if printer.get("state") else None,
            seen_at=datetime.now(UTC),
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
    label = "Bambu LAN (coming next)"
    capabilities = AdapterCapabilities(telemetry=False, file_transfer=False, job_control=False, camera=False)

    def snapshot(self, host: str, credential: dict[str, str]) -> AdapterSnapshot:
        del host, credential
        raise AdapterError("Bambu LAN onboarding is the next dedicated adapter. This app will not guess at model-specific access codes or controls.")


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
    from urllib.parse import quote

    return quote(value.lstrip("/"), safe="/")


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


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> None:
    try:
        import requests
    except ImportError as exc:
        raise AdapterError("The job-control component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    try:
        response = requests.post(url, headers={"Content-Type": "application/json", **headers}, json=payload, timeout=(4, 20))
        _raise_for_response(response)
    except Exception as exc:
        if exc.__class__.__module__.startswith("requests"):
            raise AdapterError("The printer rejected the requested job command.") from exc
        raise


def _raise_for_response(response: Any) -> None:
    if response.status_code in {401, 403}:
        raise AdapterError("The printer rejected the API key. Check it and try again.")
    if response.status_code >= 400:
        raise AdapterError(f"The printer returned HTTP {response.status_code}.")


ADAPTERS: dict[str, PrinterAdapter] = {
    "manual": ManualAdapter(),
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
