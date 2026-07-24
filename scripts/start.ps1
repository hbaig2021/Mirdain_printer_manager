$ErrorActionPreference = "Stop"

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if (-not (Test-Path .\.venv\Scripts\python.exe) -or -not (Test-Path .\node_modules)) {
        throw "Setup is incomplete. Run .\scripts\setup.ps1 first."
    }
    & npm.cmd run local
}
finally {
    Pop-Location
}
