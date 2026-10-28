param([switch]$PullModel)
$ErrorActionPreference='Stop'
$icpProjectRoot=Split-Path -Parent $PSScriptRoot
$icpCandidates=@(
 (Join-Path $icpProjectRoot '.tools\ollama-v0.40.6\app\ollama.exe'),
 (Join-Path $icpProjectRoot '.tools\ollama-v0.40.6\bin\ollama.exe'),
 (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe')
)
$icpCommand=Get-Command ollama -ErrorAction SilentlyContinue
if ($icpCommand) { $icpCandidates+= $icpCommand.Source }
$icpOllama=$icpCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $icpOllama) { throw 'Ollama is not installed. Manually install the signed installer described in docs/本地模型安装.md.' }
$env:OLLAMA_MODELS=Join-Path $icpProjectRoot '.models\ollama'
$env:OLLAMA_HOST='127.0.0.1:11434'
$env:OLLAMA_CONTEXT_LENGTH='8192'
$env:OLLAMA_NUM_PARALLEL='1'
$env:OLLAMA_MAX_LOADED_MODELS='2'
New-Item -ItemType Directory -Path $env:OLLAMA_MODELS -Force | Out-Null
$icpListening=Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
if ($icpListening) {
 Write-Output 'An Ollama server is already listening. Its model directory must be confirmed before downloading.'
 if ($PullModel) { throw 'Close the existing tray/server, then rerun this script so the model is saved on D drive.' }
} else {
 $icpLogDir=Join-Path $icpProjectRoot 'data\runtime'
 New-Item -ItemType Directory -Path $icpLogDir -Force | Out-Null
 Start-Process -FilePath $icpOllama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $icpLogDir 'ollama.stdout.log') -RedirectStandardError (Join-Path $icpLogDir 'ollama.stderr.log') | Out-Null
 $icpReady=$false
 for ($icpAttempt=0;$icpAttempt -lt 20;$icpAttempt++) {
  try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null; $icpReady=$true; break } catch { Start-Sleep -Milliseconds 500 }
 }
 if (-not $icpReady) { throw 'Ollama did not start. Check data/runtime/ollama.stderr.log.' }
}
if ($PullModel) {
 & $icpOllama pull qwen3:8b
 if ($LASTEXITCODE -ne 0) { throw 'Model download failed; rerun Ollama pull after connection is restored.' }
}
Write-Output 'Local Ollama endpoint: http://127.0.0.1:11434'

