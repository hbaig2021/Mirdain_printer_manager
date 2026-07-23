# Mirdain Print Hub

Mirdain Print Hub is an original, local-first print-farm workspace. It is
designed to cover the everyday operational workflows of hosted print-farm
software without an account, subscription, or cloud data store.

Everything is saved to a SQLite database on this computer under
`backend/data/`. No data is sent to a third-party service.

## Included now

- fleet records, printer status, favorite machines, service hours, and notes;
- a persistent production queue with explicit printer assignments and local
  start/complete state changes;
- a reusable file catalog that can seed queue jobs;
- spool inventory, printer assignments, and low-stock visibility; and
- a local activity log.

The app does **not** yet control printers, transmit G-code, or expose cameras.
Those features require model-specific, owner-approved LAN adapters and are kept
off by default so a mistaken setup cannot start or interrupt a print.

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

The command starts the API and dashboard together, opens
`http://127.0.0.1:5174`, and stops both services when you press `Ctrl+C`.

## Validation

```powershell
npm.cmd run build
```
