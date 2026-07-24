$ErrorActionPreference = "Stop"

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11 or newer is required.')"
        & py -3 -m venv .venv
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11 or newer is required.')"
        & python -m venv .venv
    }
    else {
        throw "Python 3.11 or newer was not found. Install Python from https://www.python.org/downloads/."
    }

    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "Node.js and npm were not found. Install Node.js 22 LTS from https://nodejs.org/."
    }

    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -e . pytest httpx2
    & npm.cmd ci

    Write-Host ""
    Write-Host "Mirdain Print Hub is ready. Start it with: npm.cmd run local"
}
finally {
    Pop-Location
}
