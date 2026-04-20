Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$pipelineDir = Split-Path -Parent $scriptDir
$venvDir    = Join-Path $pipelineDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$envFile    = Join-Path $pipelineDir ".env"
$envExample = Join-Path $pipelineDir ".env.example"

Write-Host "[startup] Pipeline directory: $pipelineDir"

# ── Resolve a 64-bit Python 3 interpreter ────────────────────────────────────
# Try the Windows Python Launcher first (py.exe), then fall back to common paths.
$python64 = $null

if (Get-Command py -ErrorAction SilentlyContinue) {
    $arch = & py -3 -c "import struct; print(struct.calcsize('P')*8)" 2>$null
    if ($arch -eq "64") {
        $python64 = "py"
        Write-Host "[startup] Found 64-bit Python via py launcher."
    }
}

if (-not $python64) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",   # 64-bit 3.13
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $arch = & $c -c "import struct; print(struct.calcsize('P')*8)" 2>$null
            if ($arch -eq "64") {
                $python64 = $c
                Write-Host "[startup] Found 64-bit Python at $c"
                break
            }
        }
    }
}

if (-not $python64) {
    Write-Warning @"
[startup] Could not find a 64-bit Python 3 interpreter.
Your current Python appears to be 32-bit, which is incompatible with numpy/chromadb.

To fix:
  1. Download Python 3.12 (64-bit) from https://www.python.org/downloads/
  2. During install, check 'Add Python to PATH'
  3. Re-run this script

Skipping local venv install. Docker services will still start (they use their own Python).
"@
}

# ── Local venv (only if 64-bit Python found) ─────────────────────────────────
if ($python64) {
    $pipOk = $false
    if (Test-Path $venvPython) {
        $null = & $venvPython -m pip --version 2>&1
        if ($LASTEXITCODE -eq 0) { $pipOk = $true }
    }

    if (-not $pipOk) {
        if (Test-Path $venvDir) {
            Write-Host "[startup] Removing broken virtual environment."
            Remove-Item -Recurse -Force $venvDir
        }
        Write-Host "[startup] Creating virtual environment."
        & $python64 -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
    }
    else {
        Write-Host "[startup] Virtual environment OK."
    }

    Write-Host "[startup] Upgrading pip..."
    & $venvPython -m pip install --upgrade pip --quiet

    Write-Host "[startup] Installing dependencies (this takes a few minutes on first run)..."
    & $venvPython -m pip install -r (Join-Path $pipelineDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
    Write-Host "[startup] Dependencies installed."
}

# ── .env ──────────────────────────────────────────────────────────────────────
if (-not (Test-Path $envFile)) {
    Copy-Item -Path $envExample -Destination $envFile
    Write-Warning "[startup] Created pipeline/.env from example. Fill in API keys before running jobs."
}
else {
    Write-Host "[startup] Found existing .env"
}

# ── Docker ────────────────────────────────────────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "[startup] Docker CLI not found. Install Docker Desktop and retry."
}

Write-Host "[startup] Docker version: $(& docker --version)"

$daemonCheck = & docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "[startup] Docker daemon is not running. Open Docker Desktop and wait for it to start, then retry."
}

Write-Host "[startup] Starting services..."
Push-Location $pipelineDir
try {
    & docker compose up --build
}
finally {
    Pop-Location
}
