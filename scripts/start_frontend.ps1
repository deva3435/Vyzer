$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot\.."
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not $env:BACKEND_URL) { $env:BACKEND_URL = 'http://127.0.0.1:8000' }
if (-not $env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL = 'http://localhost:11434' }
if (-not $env:AUTO_VERIFY_CODE) { $env:AUTO_VERIFY_CODE = 'true' }
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501
