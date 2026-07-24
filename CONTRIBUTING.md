# Contributing to Mirdain Print Hub

Thank you for helping make local print-farm tooling more useful and safer.

## Before you start

- Search existing issues before opening a new one.
- Use a discussion or feature request for substantial workflow or architecture
  changes.
- Report security problems privately as described in
  [SECURITY.md](SECURITY.md).
- Never include vendor assets, proprietary source code, copied product text,
  printer credentials, customer data, or real production files in a change.

## Development setup

Mirdain requires Python 3.11 or newer and a Node.js version supported by the
checked-in Vite release (Node 22 is used in CI).

Windows:

```powershell
.\scripts\setup.ps1
npm.cmd run local
```

Linux or macOS:

```bash
bash scripts/setup.sh
npm run local
```

Before submitting a pull request, run:

```bash
npm run lint
npm run build
python -m pytest
```

Use `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and
macOS when invoking Python directly.

## Pull requests

Keep changes focused and explain:

- the operator problem being solved;
- how the behavior was tested;
- any database, networking, credential, or physical-printer implications; and
- screenshots for visible interface changes.

New printer adapters need fixture-driven tests for response parsing and error
handling. File transfer or job-control support also needs protocol
documentation, timeouts, ownership checks, and a clear description of how the
behavior was validated on real hardware. A telemetry implementation does not
automatically qualify a protocol for remote starts.

By contributing, you agree that your contribution is licensed under the MIT
License in this repository.
