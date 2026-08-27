$ErrorActionPreference = "Stop"
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "Installed. Edit .env, then run: .\.venv\Scripts\syncbridge.exe serve"
