param([int]$Port=8000)
$ErrorActionPreference='Stop'
$icpProjectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $icpProjectRoot
$icpPython=Join-Path $icpProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $icpPython)) { throw 'Create .venv and install requirements-lock.txt first; see docs/部署运行手册.md.' }
if (-not (Test-Path -LiteralPath 'data\processed\knowledge.json')) { throw 'Run ingestion first; see deployment manual.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build frontend first; see deployment manual.' }
$env:PYTHONPATH=Join-Path $icpProjectRoot 'backend'
$env:PYTHONIOENCODING='utf-8'
Write-Output ('Workbench: http://127.0.0.1:'+$Port)
& $icpPython -m uvicorn icp.app:app --host 127.0.0.1 --port $Port

