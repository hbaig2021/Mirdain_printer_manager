# Mirdain Print Hub

Mirdain Print Hub is an original, local-first print-farm workspace. It is
designed to cover the everyday operational workflows of hosted print-farm
software without an account, subscription, or cloud data store.

Everything is saved to a SQLite database on this computer under
`backend/data/`. No data is sent to a third-party service.

## Included now

- fleet records, printer status, favorite machines, service hours, and notes;
- a persistent production queue with explicit printer assignments;
- compatibility profiles and an **Auto-route** action that selects the best
  ready, connected printer with file delivery and the required material;
- a local print-file library that stores actual `.gcode`, `.3mf`, `.bgcode`,
  `.g`, and `.gc` files (up to 512 MB) and can seed queue jobs;
- spool inventory, printer assignments, and low-stock visibility; and
- a local activity log; and
- local telemetry connections for OctoPrint, Moonraker/Klipper, and PrusaLink,
  with credentials stored in the operating system credential vault; and
- explicit file upload plus confirmed start, pause, resume, and cancel controls
  for connected OctoPrint and Moonraker/Klipper printers.

Before a print is started, the operator must select **Upload** and then confirm
**Start**. Mirdain does not issue a physical printer command during connection
setup or merely because a job was added to the queue.

Not yet included: Bambu LAN controls, PrusaLink controls, cameras, remote
access, compatibility-based routing, automatic filament measurement, and
automation workflows. Those are separate capability layers that need
printer-family-specific validation before they can safely be enabled.

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
