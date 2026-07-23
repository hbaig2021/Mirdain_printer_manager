# Mirdain Print Hub

Mirdain Print Hub is an original, local-first print-farm workspace. It is
designed to cover the everyday operational workflows of hosted print-farm
software without an account, subscription, or cloud data store.

Everything is saved to a SQLite database on this computer under
`backend/data/`. No data is sent to a third-party service.

## Included now

- fleet records, printer status, favorite machines, service hours, and notes;
- a persistent production queue with explicit printer assignments, finite or
  continuous run counts, priority/rotation loops, and job history;
- compatibility profiles and an **Auto-route** action that selects the best
  ready, connected printer with file delivery and the required material, then
  asks the operator to review the route;
- a local print-file library that stores actual `.gcode`, `.3mf`, `.bgcode`,
  `.g`, and `.gc` files (up to 512 MB), extracts common slicer filament
  estimates from G-code, and can seed queue jobs;
- spool inventory, printer assignments, low-stock visibility, and automatic
  slicer-estimate deductions only when a matching spool is assigned to the
  printer;
- optional direct local camera stream/snapshot URLs (Mirdain displays a real
  source or clearly says no source is configured);
- a local activity log; and
- local telemetry connections for OctoPrint, Moonraker/Klipper, and PrusaLink,
  with credentials stored in the operating system credential vault; and
- explicit file upload plus confirmed start, pause, resume, and cancel controls
  for connected OctoPrint and Moonraker/Klipper printers; and
- a background local monitor that polls connected printers every 10 seconds and
  can launch one verified queued job only after all safety gates are satisfied.

By default, every print remains manual: the operator selects **Upload** and
confirms **Start**. Verified automatic starts are an opt-in safety workflow. It
requires all of the following: a capable connected printer, the printer's
"verified auto-start" switch enabled, a queued job armed for restart, and an
uploaded file explicitly marked as containing tested auto-ejection G-code.
Mirdain atomically reserves both the job and printer before sending a command;
an uncertain response stops the workflow and marks the printer for review.

Moonraker's explicit `complete` status is reconciled automatically. Mirdain
does not infer completion solely from a generic ready/idle response, so an
OctoPrint job without a definitive completion report remains active until an
operator records completion. This is deliberate protection against starting a
second print on an uncleared bed.

Not yet included: Bambu LAN developer-mode controls/cameras, PrusaLink file
delivery or controls, an authenticated remote-access gateway, outbound
Zapier/Make webhooks, and hardware/scale-based filament measurement. The
filament number currently shown is a slicer-derived estimate, not a real-time
meter. Those capability layers need model-specific validation and, for remote
access, an explicit choice of secure tunnel or gateway.

## First-time setup (Windows)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm.cmd install
```

## Start the hub

```powershell
npm.cmd run local
```

The command starts the API and dashboard together, opens an available local
address (normally `http://127.0.0.1:5174`), and stops both services when you
press `Ctrl+C`.

## Validation

```powershell
npm.cmd run build
npm.cmd run lint
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```
