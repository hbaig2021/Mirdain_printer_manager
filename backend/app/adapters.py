from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class AdapterSnapshot:
    state: str
    percent_complete: int | None
    active_job: str | None
    note: str | None
    seen_at: datetime


class AdapterError(RuntimeError):
    pass


class PrinterAdapter:
    """Read-only adapter boundary for a locally managed printer."""

    def snapshot(self, host: str) -> AdapterSnapshot:
        raise NotImplementedError


class PrusaLinkAdapter(PrinterAdapter):
    """Best-effort local PrusaLink status reader.

    A printer's API key will be added to the OS-backed secrets store during
    onboarding. This first release intentionally does not issue print commands.
    """

    def snapshot(self, host: str) -> AdapterSnapshot:
        normalized_host = host.rstrip("/")
        request = Request(
            f"http://{normalized_host}/api/v1/status",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=3) as response:  # noqa: S310 - local printer URL from the owner
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise AdapterError("PrusaLink is unavailable or needs local API setup.") from exc

        printer = payload.get("printer", {}) if isinstance(payload, dict) else {}
        telemetry = payload.get("telemetry", {}) if isinstance(payload, dict) else {}
        raw_state = str(printer.get("state", "UNKNOWN")).upper()
        state = "printing" if raw_state in {"PRINTING", "PAUSED"} else "ready"
        progress = telemetry.get("print_progress")
        percent = round(float(progress)) if isinstance(progress, (int, float)) else None
        return AdapterSnapshot(
            state=state,
            percent_complete=percent,
            active_job=None,
            note=None,
            seen_at=datetime.now(UTC),
        )


class BambuLanAdapter(PrinterAdapter):
    """Bambu LAN boundary.

    Bambu telemetry is model and firmware dependent. The dashboard accepts a
    Bambu LAN printer now, but keeps it in setup until its serial number and
    LAN access code are added through the local secrets onboarding flow.
    """

    def snapshot(self, host: str) -> AdapterSnapshot:
        del host
        return AdapterSnapshot(
            state="setup",
            percent_complete=None,
            active_job=None,
            note="Add the printer serial number and LAN access code to enable Bambu telemetry.",
            seen_at=datetime.now(UTC),
        )


ADAPTERS: dict[str, PrinterAdapter] = {
    "prusalink": PrusaLinkAdapter(),
    "bambu_lan": BambuLanAdapter(),
}
