$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot\..\backend"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not $env:JWT_SECRET_KEY) { $env:JWT_SECRET_KEY = 'development-only-change-me' }
if (-not $env:ENVIRONMENT) { $env:ENVIRONMENT = 'development' }
if (-not $env:CORS_ORIGINS) { $env:CORS_ORIGINS = 'http://localhost:8501' }
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = 'sqlite:///./vyzer.db' }
.\.venv\Scripts\python.exe scripts\upgrade_db.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
