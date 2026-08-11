$ErrorActionPreference = "Stop"

# ============================================================
# VYZER ONE-CLICK LAUNCHER
# ============================================================

# Resolve the directory containing this script.
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $ProjectRoot) {
    Write-Host "[ERROR] Could not determine Vyzer project directory." -ForegroundColor Red
    exit 1
}

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

# Existing virtual environment is one directory above vyzer_final.
$VenvRoot = Join-Path (Split-Path -Parent $ProjectRoot) ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

$BackendDir = Join-Path $ProjectRoot "backend"

$BackendHost = "127.0.0.1"
$BackendPort = 8000
$FrontendPort = 8501

$BackendUrl = "http://$BackendHost`:$BackendPort"
$FrontendUrl = "http://127.0.0.1`:$FrontendPort"
$OllamaUrl = "http://127.0.0.1:11434"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "           VYZER LAUNCHER" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------
# Verify paths
# ------------------------------------------------------------

Write-Host "[INFO] Project: $ProjectRoot"
Write-Host "[INFO] Python:  $VenvPython"
Write-Host ""

if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] Vyzer virtual environment not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Expected:"
    Write-Host $VenvPython
    Write-Host ""
    exit 1
}

if (-not (Test-Path $BackendDir)) {
    Write-Host "[ERROR] Backend directory not found:" -ForegroundColor Red
    Write-Host $BackendDir
    exit 1
}

Write-Host "[OK] Virtual environment found." -ForegroundColor Green

# ------------------------------------------------------------
# Environment variables
# ------------------------------------------------------------

$env:BACKEND_URL = $BackendUrl
$env:OLLAMA_BASE_URL = $OllamaUrl
$env:AUTO_VERIFY_CODE = "true"

$env:ENVIRONMENT = "development"

if (-not $env:JWT_SECRET_KEY) {
    $env:JWT_SECRET_KEY = "development-only-change-me"
}

$env:CORS_ORIGINS = $FrontendUrl
$env:DATABASE_URL = "sqlite:///./vyzer.db"

# ------------------------------------------------------------
# Check Ollama
# ------------------------------------------------------------

Write-Host "[INFO] Checking Ollama..." -ForegroundColor Yellow

try {
    $ollama = Invoke-RestMethod `
        -Uri "$OllamaUrl/api/tags" `
        -Method Get `
        -TimeoutSec 3

    Write-Host "[OK] Ollama is running." -ForegroundColor Green

    $modelNames = @(
        $ollama.models | ForEach-Object {
            $_.name
        }
    )

    if ($modelNames -contains "qwen2.5-coder:7b") {
        Write-Host "[OK] qwen2.5-coder:7b found." -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] qwen2.5-coder:7b not found." -ForegroundColor Yellow
    }

    if ($modelNames -contains "gemma3:4b") {
        Write-Host "[OK] gemma3:4b found." -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] gemma3:4b not found." -ForegroundColor Yellow
    }
}
catch {
    Write-Host "[WARN] Ollama is not running." -ForegroundColor Yellow
    Write-Host "Vyzer can start, but AI requests will not work." -ForegroundColor Yellow
}

# ------------------------------------------------------------
# Check backend
# ------------------------------------------------------------

$backendRunning = $false

try {
    $health = Invoke-RestMethod `
        -Uri "$BackendUrl/health" `
        -Method Get `
        -TimeoutSec 2

    if ($health.status -eq "healthy") {
        $backendRunning = $true
        Write-Host "[OK] Backend already running." -ForegroundColor Green
    }
}
catch {
    $backendRunning = $false
}

# ------------------------------------------------------------
# Start backend
# ------------------------------------------------------------

$backendProcess = $null

if (-not $backendRunning) {

    Write-Host "[INFO] Running database migration..." -ForegroundColor Yellow

    Push-Location $BackendDir

    try {
        & $VenvPython "scripts\upgrade_db.py"

        if ($LASTEXITCODE -ne 0) {
            throw "Database migration failed."
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "[OK] Database ready." -ForegroundColor Green

    Write-Host "[INFO] Starting FastAPI..." -ForegroundColor Yellow

    $backendProcess = Start-Process `
        -FilePath $VenvPython `
        -ArgumentList "-m uvicorn app.main:app --host $BackendHost --port $BackendPort" `
        -WorkingDirectory $BackendDir `
        -PassThru

    Write-Host "[INFO] Waiting for backend..." -ForegroundColor Yellow

    $backendReady = $false

    for ($i = 1; $i -le 30; $i++) {

        Start-Sleep -Seconds 1

        if ($backendProcess.HasExited) {
            break
        }

        try {
            $health = Invoke-RestMethod `
                -Uri "$BackendUrl/health" `
                -Method Get `
                -TimeoutSec 2

            if ($health.status -eq "healthy") {
                $backendReady = $true
                break
            }
        }
        catch {
        }
    }

    if (-not $backendReady) {

        Write-Host "[ERROR] Backend did not become healthy." -ForegroundColor Red

        if ($backendProcess -and -not $backendProcess.HasExited) {
            Stop-Process -Id $backendProcess.Id -Force
        }

        exit 1
    }

    Write-Host "[OK] Backend is healthy." -ForegroundColor Green
}

# ------------------------------------------------------------
# Check frontend
# ------------------------------------------------------------

$frontendRunning = $false

try {
    $tcp = Test-NetConnection `
        -ComputerName "127.0.0.1" `
        -Port $FrontendPort `
        -WarningAction SilentlyContinue

    if ($tcp.TcpTestSucceeded) {
        $frontendRunning = $true
        Write-Host "[OK] Streamlit already running." -ForegroundColor Green
    }
}
catch {
    $frontendRunning = $false
}

# ------------------------------------------------------------
# Start Streamlit
# ------------------------------------------------------------

$frontendProcess = $null

if (-not $frontendRunning) {

    Write-Host "[INFO] Starting Streamlit..." -ForegroundColor Yellow

    $frontendProcess = Start-Process `
        -FilePath $VenvPython `
        -ArgumentList "-m streamlit run app.py --server.address 0.0.0.0 --server.port $FrontendPort" `
        -WorkingDirectory $ProjectRoot `
        -PassThru

    Write-Host "[INFO] Waiting for Streamlit..." -ForegroundColor Yellow

    $frontendReady = $false

    for ($i = 1; $i -le 30; $i++) {

        Start-Sleep -Seconds 1

        if ($frontendProcess.HasExited) {
            break
        }

        try {
            $tcp = Test-NetConnection `
                -ComputerName "127.0.0.1" `
                -Port $FrontendPort `
                -WarningAction SilentlyContinue

            if ($tcp.TcpTestSucceeded) {
                $frontendReady = $true
                break
            }
        }
        catch {
        }
    }

    if (-not $frontendReady) {

        Write-Host "[ERROR] Streamlit did not become ready." -ForegroundColor Red

        if ($frontendProcess -and -not $frontendProcess.HasExited) {
            Stop-Process -Id $frontendProcess.Id -Force
        }

        exit 1
    }

    Write-Host "[OK] Streamlit is running." -ForegroundColor Green
}

# ------------------------------------------------------------
# Open browser
# ------------------------------------------------------------

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "           VYZER IS READY" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Frontend: $FrontendUrl" -ForegroundColor Cyan
Write-Host "Backend:  $BackendUrl" -ForegroundColor Cyan
Write-Host "Ollama:   $OllamaUrl" -ForegroundColor Cyan
Write-Host ""

Start-Process $FrontendUrl

Write-Host "[OK] Browser opened." -ForegroundColor Green
Write-Host ""
Write-Host "Press Ctrl+C to stop the launcher." -ForegroundColor Yellow
Write-Host ""

# Keep the launcher alive.
while ($true) {

    if ($backendProcess -and $backendProcess.HasExited) {
        Write-Host "[WARN] Backend process stopped." -ForegroundColor Red
    }

    if ($frontendProcess -and $frontendProcess.HasExited) {
        Write-Host "[WARN] Streamlit process stopped." -ForegroundColor Red
    }

    Start-Sleep -Seconds 5
}