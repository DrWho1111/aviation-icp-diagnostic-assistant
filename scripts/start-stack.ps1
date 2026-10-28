param()
$ErrorActionPreference='Stop'
$icpProjectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $icpProjectRoot
$icpCommand=Get-Command docker -ErrorAction SilentlyContinue
$icpDocker=if ($icpCommand) { $icpCommand.Source } else { 'D:\Apps\Docker\resources\bin\docker.exe' }
if (-not (Test-Path -LiteralPath $icpDocker)) { throw 'Install Docker Desktop first; see docs/部署运行手册.md.' }
$icpEngine=& $icpDocker info --format '{{.OSType}}' 2>$null
if ($LASTEXITCODE -ne 0 -or $icpEngine -ne 'linux') { throw 'Open Docker Desktop and wait for Engine running, then retry.' }
foreach ($icpFile in @('.env','.secrets\dify-tools.json','.models\bge-reranker-v2-m3\model.safetensors')) {
 if (-not (Test-Path -LiteralPath $icpFile)) { throw ('Deployment input missing: '+$icpFile+'. Complete the deployment manual first.') }
}
& (Join-Path $PSScriptRoot 'start-ollama.ps1')
& (Join-Path $PSScriptRoot 'start-platform.ps1') -Platform dify
& (Join-Path $PSScriptRoot 'start-platform.ps1') -Platform ragflow
& $icpDocker compose -f deploy/reranker.compose.yaml up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw 'Build the reranker image as described in the deployment manual, then retry.' }
$icpComposeFiles=@('-f','deploy/compose.yaml')
if (Test-Path -LiteralPath '.secrets\dify-native-main-production.json') {
 $icpComposeFiles+=@('-f','deploy/native.override.yaml')
}
& $icpDocker compose --env-file .env @icpComposeFiles up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw 'Application startup failed. Check port 8000 and the private configuration.' }
$icpDeadline=(Get-Date).AddMinutes(3)
$icpReady=$false
while ((Get-Date) -lt $icpDeadline) {
 try {
  $icpApp=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 5
  $icpRag=Invoke-WebRequest -Uri 'http://127.0.0.1:9380/api/v1/system/ping' -TimeoutSec 5 -UseBasicParsing
  $icpDify=Invoke-WebRequest -Uri 'http://127.0.0.1:8081/console/api/setup' -TimeoutSec 5 -UseBasicParsing
  $icpRerank=Invoke-RestMethod -Uri 'http://127.0.0.1:9390/health' -TimeoutSec 5
  if ($icpApp.status -eq 'ok' -and $icpRag.StatusCode -eq 200 -and $icpDify.StatusCode -eq 200 -and $icpRerank.status -eq 'ready') { $icpReady=$true; break }
 } catch { }
 Start-Sleep -Seconds 3
}
if (-not $icpReady) { throw 'Services did not become ready within three minutes. Use scripts/platform_logs.py for sanitized logs.' }
$icpPython=Join-Path $icpProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $icpPython)) { throw 'Prepare the project Python environment as described in docs/部署运行手册.md before running the startup probe.' }
& $icpPython (Join-Path $PSScriptRoot 'service_readiness.py')
if ($LASTEXITCODE -ne 0) { throw 'The model planner is not ready even though HTTP endpoints respond. Wait for the model/platform and rerun startup.' }
$icpStartupReport=[PSCustomObject]@{
 date=(Get-Date -Format o)
 passed=$true
 checks=[PSCustomObject]@{workbench=($icpApp.status -eq 'ok'); dify=($icpDify.StatusCode -eq 200); ragflow=($icpRag.StatusCode -eq 200); reranker=($icpRerank.status -eq 'ready')}
 scope='Service endpoints and actual planner readiness; not model accuracy, cold-start performance or empty-volume recovery.'
}
$icpStartupReport | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $icpProjectRoot 'outputs/stack-startup.json') -Encoding UTF8
Write-Output 'Workbench: http://127.0.0.1:8000/'
Write-Output 'Dify: http://127.0.0.1:8081/ | RAGFlow: http://127.0.0.1:8180/'
Write-Output 'Startup checks passed. Full acceptance results are recorded separately in docs/测试报告.md.'
