# Mirdain Print Hub

Mirdain Print Hub is a free, open-source, local-first command center for a 3D
print farm. It keeps fleet records, production queues, print files, materials,
telemetry, and carefully gated printer controls in one workspace—without an
account, subscription, or required cloud service.

The project is currently at **v0.4.0**. It is useful today, but it should still
be treated as pre-1.0 software around real equipment. Read
[the safety model](#safety-model) before connecting a printer.

## Why Mirdain

- **Local by default:** operational data lives in SQLite on the host.
- **No fake telemetry:** a value is live only when a configured printer reports
  it; otherwise Mirdain says it is unavailable.
- **Protocol-aware:** OctoPrint, Moonraker/Klipper, and PrusaLink behavior is
  isolated behind adapter boundaries.
- **Operator-controlled:** upload, start, pause, resume, and cancel actions are
  explicit and checked against job and printer ownership.
- **Portable:** run from source on Windows, Linux, or macOS, or use Docker
  Compose.
- **MIT licensed:** fork it, audit it, and adapt it to your farm.

## What works

- persistent printer records, status, favorites, service hours, maintenance
  intervals, supported materials, and notes;
- a production queue with printer assignments, finite or continuous run counts,
  sticky or rotating loops, priorities, per-run history, and atomic concurrent
  dispatch to multiple compatible printers;
- material-aware **Auto-route** suggestions for ready, connected printers that
  can accept a file;
- a local library for `.gcode`, `.g`, `.gc`, `.3mf`, and `.bgcode` files up to
  512 MB, including SHA-256 records and common slicer filament estimates;
- spool inventory, printer assignments, low-stock visibility, and
  slicer-estimate deductions when exactly one assigned spool matches the
  material family;
- direct local MJPEG or snapshot camera URLs;
- a local activity log;
- bounded concurrent background polling for connected or recovering printers,
  with a ten-second interval after each poll cycle; and
- opt-in, verified automatic dispatch for deliberately engineered
  auto-ejection workflows on Moonraker, PrusaLink, and the simulator.

### Printer integration matrix

| Adapter | Fleet tracking | Live telemetry | File delivery | Start/pause/resume/cancel | Completion handling |
| --- | --- | --- | --- | --- | --- |
| Manual | Yes, record only | No | No | No | Not tracked; use a connected adapter or simulator for queue runs |
| Simulator (no hardware) | Yes | Simulated | Simulated | Yes, simulated with confirmation | Simulated cancel/status; operator may record completion |
| OctoPrint | Yes | Yes | Yes | Yes, with confirmation | Polling cannot prove a successful terminal event; the operator records completion and confirms READY |
| Moonraker / Klipper | Yes | Yes | Yes | Yes, with confirmation | Explicit terminal status is reconciled |
| PrusaLink | Yes | Yes | Yes | Yes, with confirmation | Explicit terminal status is reconciled |
| Bambu printers | Yes, record only | No; generic Bambu LAN is intentionally disabled | No | No | Not tracked |

Camera display is configured with a direct URL on the printer record; it is not
currently negotiated by any adapter. Capability checks in the application, not
this table, are authoritative for a running release.

OctoPrint supports confirmed upload and job controls, but unattended START is
intentionally disabled. Its polled job state cannot reliably prove a successful
terminal event versus every late cancellation/error, so it remains a
confirmed-manual workflow in this release.

Mirdain intentionally does not implement generic Bambu LAN control. The
official Fleet Hub documentation describes a separate, provisioned hardware and
API path with Bambu-issued credentials. Any future Fleet Hub support would be a
separate, optional adapter developed only after Bambu provisioning, hardware
testing, and redistribution and terms review. Mirdain does not bundle Fleet Hub
certificates, private keys, activation material, or other credentials. Until
then, a Bambu printer can be kept as a manual, record-only fleet entry.

## Quick start with Docker

[Docker Desktop](https://docs.docker.com/desktop/) or Docker Engine with the
Compose plugin is required.

```bash
git clone https://github.com/hbaig2021/Mirdain_printer_manager.git
cd Mirdain_printer_manager
docker compose up --build -d
```

Open <http://127.0.0.1:8080>. Follow logs with:

```bash
docker compose logs -f
```

Stop the services without deleting data:

```bash
docker compose down
```

Compose binds only to `127.0.0.1` and stores application data in a persistent
named volume. Copy `.env.example` to `.env` to change the web port, provide a
managed `MIRDAIN_SECRET_KEY`, or protect the public automation API with
`MIRDAIN_API_TOKEN`.

The API container must be able to reach each printer's local address. If a
`.local` hostname does not resolve from Docker, use the printer's reserved LAN
IP address.

## Install from source

Source installs need:

- Python 3.11 or newer;
- Node.js 22 and npm; and
- Git.

### Windows

In PowerShell:

```powershell
git clone https://github.com/hbaig2021/Mirdain_printer_manager.git
Set-Location Mirdain_printer_manager
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\start.ps1
```

The process-scoped execution-policy change lasts only for that PowerShell
window. The launcher normally opens <http://127.0.0.1:5174>.

### Linux

Install Python's venv package first if your distribution separates it (for
example, `python3-venv` on Debian/Ubuntu), then run:

```bash
git clone https://github.com/hbaig2021/Mirdain_printer_manager.git
cd Mirdain_printer_manager
bash scripts/setup.sh
bash scripts/start.sh
```

### macOS

Install current Python and Node.js with your preferred package manager, then use
the same `setup.sh` and `start.sh` commands shown for Linux. The launcher uses
the macOS `open` command when browser opening is enabled.

### Manual setup

If you prefer not to use the helper scripts:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e . pytest httpx2
npm ci
npm run local
```

On Windows, replace `./.venv/bin/python` with
`.\.venv\Scripts\python.exe` and `npm` with `npm.cmd`.

The local launcher finds free ports near 8765 and 5174, starts the API and Vite
dashboard together, and stops both when you press `Ctrl+C`. Optional environment
variables are listed in `.env.example`; set them in the shell before starting
the launcher.

## No-hardware first run

Use the simulator to verify the complete queue workflow before connecting real
equipment:

1. Choose **Add printer**. Set the name to `demo`, manufacturer to `Mirdain`,
   model to `Simulator`, connection to **Simulator (no hardware)**, local host
   to `demo`, and loaded/compatible material to `PLA`.
2. Open that printer's connection settings, select **Simulator (no hardware)**,
   keep the host as `demo`, then choose **Test and save connection**.
3. Inspect the simulated bed and choose **Mark ready**. A new connection starts
   with unknown bed-clearance state and cannot receive work until this one-time
   confirmation.
4. In the file library, upload
   [`examples/simulator-cube.gcode`](examples/simulator-cube.gcode) as a PLA
   file. It is a comments-only workflow fixture with no motion, heat, or other
   printer commands.
5. Create a queued job from that file, choose **Auto-route**, and approve the
   `demo` simulator.
6. Choose **Upload**, then use the confirmed **Start**, **Pause**, **Resume**,
   and **Cancel** controls.

The simulator keeps its state only in the running API process. The example file
is deliberately non-printable and must never be sent to a physical printer.

## First printer

1. Add a printer from the fleet view.
2. Choose OctoPrint, Moonraker/Klipper, PrusaLink, or the no-hardware
   simulator. **Manual tracking** and Bambu entries are record-only in this
   release and cannot execute queue runs.
3. Enter the connected printer's local IP/hostname and credential, then test the
   connection. OctoPrint and Moonraker accept API keys; PrusaLink uses its HTTP
   Digest username and password. A test reads telemetry only; it does not
   upload or start a file.
4. Inspect the build plate and choose **Mark ready**. New connections remain
   gated until an operator confirms the plate is clear.
5. Configure supported and currently loaded materials. If you intend to use
   unattended starts, first give the printer a trusted static address or DHCP
   reservation, verify that the physical printer is not registered through any
   other URL or record, then use **Enable verified auto-start** on its printer page.
   That separate action records an explicit `ARM STATIC ENDPOINT`
   confirmation; adding or reconnecting a printer never arms it.
6. Upload a tested print file to the local library.
7. Create and route a queue job.
8. Review the printer, material, build plate, and file before using **Upload**
   and the confirmed **Start** action.

Use credentials scoped to the minimum permissions supported by the printer
software. Mirdain first tries the operating-system credential vault. If no
vault is available, it encrypts credentials with Fernet using
`MIRDAIN_SECRET_KEY` or an automatically generated local key.

## Safety model

Mirdain deliberately separates planning, file delivery, and physical execution.

- A queued job does not send anything to a printer.
- **Auto-route** proposes or records an assignment; it does not start a print.
- Manual starts require a capable connected printer, an uploaded file, an idle
  printer owned by that job, the required material physically loaded, and an
  explicit typed confirmation. A detailed value such as `PETG - Orange` must
  match the loaded profile; required multi-slot profiles must match recorded
  slot order exactly.
- Pause, resume, and cancel commands are blocked if live telemetry reports a
  different active file or another job owns the printer.
- Automatic starts are off by default. They require the printer's verified
  auto-start switch, an explicit static/reserved-endpoint attestation, a task
  armed for restart, and a local file explicitly marked as tested for
  auto-ejection. Unattended START is enabled only for Moonraker, PrusaLink, and
  the no-hardware simulator; OctoPrint requires a confirmed manual START.
- Unattended bed clearance is a separate opt-in. Mirdain releases a printer
  automatically only when the task also enables **Auto-clear**, the attached
  file is marked as auto-eject verified, and a connected adapter reports an
  explicit successful completion.
- Before dispatch, Mirdain atomically creates a child run and reserves its
  printer. Several printers can safely consume different repetitions of one
  parent task without oversubscribing a finite run count. Sticky tasks keep
  their position; rotating tasks move to the tail after each assignment.
- An uncertain upload/start response, cancellation, or reported failure pauses
  further dispatch for that parent task and flags the printer for review.
- Auto-clear and auto-restart can be revoked while a child is active; this does
  not stop the physical print, but it prevents trusted clearance and future
  dispatch.
- A trusted terminal event is accepted only after telemetry has observed that
  specific run printing. This prevents a latched completion for an earlier
  same-name run from completing a new child.
- Recording completion for an active connected/simulated run always requires a
  subsequent **Mark ready** confirmation; it never proves that an automated
  ejection occurred.
- A generic ready/idle response is not treated as proof that an active job
  completed. This prevents a brief telemetry gap from releasing an uncleared
  build plate.
- A restart, a monitoring outage, or a changed resolved endpoint invalidates
  stored bed-clearance permission and disarms the printer-level automation
  switch. Inspect the printer, confirm **READY**, and explicitly re-arm only
  after monitoring recovers.

OctoPrint, Moonraker, and PrusaLink do not expose one universal stable hardware
fingerprint that Mirdain can verify across every supported setup. The hub
re-resolves endpoint addresses before network operations and fails closed on
address drift or duplicate records, but it cannot detect hardware replacement
or a DHCP lease reassignment that preserves the same address. A trusted static
address or DHCP reservation is therefore a safety precondition for unattended
physical starts, not merely a performance recommendation. The operator must
also ensure the same physical controller is not registered through another
scheme, address, port, base path, or printer record. Reconnect and inspect the
machine after network or hardware changes.

These are software interlocks, not safety certification. Mirdain cannot inspect
the build plate, validate G-code, detect a fire, prove ventilation, measure
actual remaining filament, or certify an ejection mechanism. Validate every
printer and file, follow manufacturer guidance, use appropriate fire and
electrical precautions, and supervise new automation. See
[SECURITY.md](SECURITY.md) for the complete deployment boundary.

## Local and remote access

There are no Mirdain dashboard accounts, authorization, or built-in TLS in this
release. The default loopback binding is intentional. The optional public API
token described below does not protect dashboard routes.

For remote access, keep the hub behind an authenticated private VPN such as
WireGuard or Tailscale and use VPN ACLs plus the host firewall. Never
port-forward it directly from the public internet.

To listen on private network interfaces with Docker, set this in `.env`:

```dotenv
MIRDAIN_BIND_ADDRESS=0.0.0.0
MIRDAIN_ALLOWED_HOSTS="farm-hub.local 192.168.1.20"
```

For a source install:

```bash
MIRDAIN_BIND_ADDRESS=0.0.0.0 \
MIRDAIN_ALLOWED_HOSTS="farm-hub.local 192.168.1.20" \
bash scripts/start.sh
```

PowerShell:

```powershell
$env:MIRDAIN_BIND_ADDRESS = "0.0.0.0"
$env:MIRDAIN_ALLOWED_HOSTS = "farm-hub.local 192.168.1.20"
.\scripts\start.ps1
```

Anyone who can reach that address can operate the hub. A public reverse proxy
is unsafe unless it adds strong authentication, HTTPS, request limits, and
network policy. Mirdain rejects foreign `Host` headers and cross-origin browser
control requests to reduce DNS-rebinding and CSRF risk. `MIRDAIN_ALLOWED_HOSTS`
accepts only exact space-separated hostnames or IP addresses—never add a
wildcard. This allowlist is a browser boundary, not user authentication. Run a
single API process only; multi-worker Uvicorn and replicated API containers are
not supported because physical-command safety gates are process-local.

## Data, credentials, and backups

Source installs store application data in `backend/data/`:

| Path | Contents |
| --- | --- |
| `fleet.db` | SQLite fleet, queue, material, file metadata, and activity |
| `files/` | Uploaded print files |
| `secrets/` | Fernet-encrypted printer credentials when the fallback store is used |
| `.secret-key` | Auto-generated fallback encryption key, if `MIRDAIN_SECRET_KEY` was not supplied |

Docker stores the same directory at `/app/backend/data` in the
`mirdain-data` named volume.

Stop Mirdain before taking or restoring a filesystem copy so SQLite and uploaded
files represent the same point in time. For a source install, copy the entire
`backend/data/` directory—not only `fleet.db`.

For Docker:

```bash
docker compose stop api
docker compose cp --archive api:/app/backend/data/. ./mirdain-data-backup/
docker compose start api
```

Restore the complete backup into a clean data volume while the API is stopped,
then restart it. Preserve `.secret-key` and `secrets/` together. If you set
`MIRDAIN_SECRET_KEY`, preserve that external secret instead; a backup without
the matching key cannot decrypt printer credentials.

When a desktop operating-system vault accepted a credential, that secret is not
inside `backend/data/` and must be re-entered after migrating to another
computer. Treat every backup as sensitive because it contains print files,
operational history, and potentially encrypted credentials.

## API

The FastAPI service publishes an OpenAPI schema and interactive documentation:

- source install: <http://127.0.0.1:8765/docs>
- Docker: <http://127.0.0.1:8080/docs>
- raw schema: `/openapi.json`
- alternative documentation: `/redoc`

FastAPI's interactive documentation pages load their presentation assets from
the upstream jsDelivr, FastAPI, and Google Fonts hosts. The application,
printer traffic, and raw OpenAPI schema remain local; use `/openapi.json` when
offline or when external documentation assets are blocked by network policy.

The API covers:

- `/api/dashboard` and `/api/health`;
- a versioned `/api/public/v1` integration surface for inventory, active/idle
  printers, queue reads, enqueueing, and confirmed ready-state changes;
- printer, job, material, and file CRUD operations;
- file upload/download and adapter discovery;
- printer connection tests and telemetry refresh;
- route candidates and reviewed job assignment; and
- file delivery plus confirmed job control/completion.

Set `MIRDAIN_API_TOKEN` to a strong random value to require this header on
`/api/public/v1` requests:

```http
Authorization: Bearer your-random-token
```

If the variable is empty, those endpoints remain unauthenticated. The token
protects only the versioned public automation surface; it does not protect the
dashboard, generated docs, health endpoint, or internal `/api` control routes.
Keep the whole service on loopback or an authenticated VPN even when the token
is set.

Request and response models, validation rules, and status codes are documented
in the generated OpenAPI pages. `/api/public/v1` is the versioned integration
surface. Other dashboard endpoints are internal and may change before v1.0.

## Development and validation

```bash
npm run lint
npm run build
./.venv/bin/python -m pytest
```

On Windows:

```powershell
npm.cmd run lint
npm.cmd run build
.\.venv\Scripts\python.exe -m pytest
```

GitHub Actions runs Python tests and dependency auditing, frontend lint/build
and dependency auditing, and a complete Docker Compose build for pull requests.
Contribution and adapter-validation expectations are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

Priorities for future releases include:

- evaluating a separate, optional official Bambu Fleet Hub adapter only after
  hardware availability, Bambu provisioning, redistribution and terms review,
  and real-device safety testing; generic Bambu LAN control remains
  intentionally unsupported;
- authentication, roles, HTTPS deployment guidance, and an audited remote
  gateway;
- negotiated camera adapters, health checks, and event recording;
- notifications and deliberately scoped outbound webhooks;
- order/product, labeling, quality-control, and richer production reporting;
- hardware/scale-backed filament measurements;
- export/import and guided backup/restore; and
- broader adapter fixtures plus real-hardware compatibility certification.

Features that can move a machine or start heat require model-specific testing
and safety review before they are enabled.

## Independent implementation and research

Mirdain Print Hub is an independent implementation written for this repository
from public descriptions of print-farm workflows and documented printer APIs.
No FlowQ source code, private API, assets, screenshots, branding, or copied
interface text is included or required. Mirdain is not affiliated with,
endorsed by, or a replacement warranted by Infinity Flow 3D; FlowQ and related
marks belong to their respective owners.

Public research used to understand the problem space and interoperability
boundaries includes:

- the public [FlowQ product description](https://infinityflow3d.com/pages/flowq-3d-printer-automation-print-farm-management-software)
  for high-level operator workflow research only;
- the official [OctoPrint REST API](https://docs.octoprint.org/en/main/api/);
- the official [Moonraker external API](https://moonraker.readthedocs.io/en/latest/external_api/introduction/),
  including file and job operations;
- the official [PrusaLink OpenAPI specification](https://github.com/prusa3d/Prusa-Link-Web/blob/master/spec/openapi.yaml); and
- Bambu Lab's official [Fleet Hub HTTP API](https://portal.bblmw.com/fleet-hub/wb76oog4i8k/files/Bambu%20Fleet%20Hub%20HTTP%20API%20EN%20v1.0.0.pdf),
  [technical white paper](https://portal.bblmw.com/fleet-hub/wb76oog4i8k/files/Fleet%20Hub%20Technical%20White%20Paper%20EN%20v1.0.pdf),
  and [security white paper](https://portal.bblmw.com/fleet-hub/wb76oog4i8k/files/Bambulab%20Fleet%20Hub%20Security%20White%20Paper%20EN%20v1.0.pdf)
  for the boundaries of a possible future, separately provisioned integration.

Those references are research inputs, not bundled code or assets. The
application uses its own Mirdain name, visual design, data model, copy, and
safety workflow.

## License

Copyright © 2026 Hamza Baig. Released under the [MIT License](LICENSE).
