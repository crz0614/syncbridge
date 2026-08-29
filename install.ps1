$ErrorActionPreference = "Stop"
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install .
if (Test-Path .env) {
    Write-Host "Installed. Existing .env kept. Run: .\.venv\Scripts\syncbridge.exe serve"
} else {
    Write-Host "Installed. Run: .\.venv\Scripts\syncbridge.exe init"
    Write-Host "Then start SyncBridge with: .\.venv\Scripts\syncbridge.exe serve"
}
