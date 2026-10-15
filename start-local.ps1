param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:TEMP = Join-Path $PSScriptRoot '.tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create venv. Install Python 3.13 and retry.' }
}
& $projectPython -c 'import fastapi,uvicorn,numpy,scipy,pandas,httpx,dotenv,multipart'
if ($LASTEXITCODE -ne 0) {
    & $projectPython -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Write-Host "Buck Lab: http://127.0.0.1:$Port"
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
& $projectPython -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
