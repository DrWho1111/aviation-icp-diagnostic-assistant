$ErrorActionPreference='Stop'
$icpComputer=Get-CimInstance Win32_ComputerSystem
$icpDocker=Get-Command docker -ErrorAction SilentlyContinue
$icpPython=Get-Command py -ErrorAction SilentlyContinue
$icpWsl=Get-Command wsl -ErrorAction SilentlyContinue
$icpWslReady=$false
if ($icpWsl) { & $icpWsl.Source --status *> $null; $icpWslReady=($LASTEXITCODE -eq 0) }
$icpRoot=Split-Path -Parent $PSScriptRoot
[PSCustomObject]@{
 Project=$icpRoot
 MemoryGiB=[math]::Round($icpComputer.TotalPhysicalMemory/1GB,1)
 PythonLauncher=[bool]$icpPython
 ProjectVenv=Test-Path -LiteralPath (Join-Path $icpRoot '.venv\Scripts\python.exe')
 DockerCommand=[bool]$icpDocker
 WSLReady=$icpWslReady
 Knowledge=Test-Path -LiteralPath (Join-Path $icpRoot 'data\processed\knowledge.json')
 Frontend=Test-Path -LiteralPath (Join-Path $icpRoot 'frontend\dist\index.html')
 OllamaInstaller=Test-Path -LiteralPath (Join-Path $icpRoot '.tools\ollama-v0.40.6\OllamaSetup.exe')
} | ConvertTo-Json
