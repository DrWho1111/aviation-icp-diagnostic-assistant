param([Parameter(Mandatory=$true)][ValidateSet('dify','ragflow')][string]$Platform)
$ErrorActionPreference='Stop'
$icpProjectRoot=Split-Path -Parent $PSScriptRoot
$icpWorkspace=Join-Path $icpProjectRoot ('.secrets\platforms\'+$Platform)
$icpComposePath=Join-Path $icpWorkspace 'icp.compose.yaml'
if (-not (Test-Path -LiteralPath $icpComposePath)) { throw 'Run scripts/prepare_platform_sources.py and scripts/configure_platforms.py first.' }
$icpCandidates=@('D:\Apps\Docker\resources\bin\docker.exe','C:\Program Files\Docker\Docker\resources\bin\docker.exe',(Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'))
$icpCommand=Get-Command docker -ErrorAction SilentlyContinue
if ($icpCommand) { $icpCandidates=@($icpCommand.Source)+$icpCandidates }
$icpDocker=$icpCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $icpDocker) { throw 'Install and start Docker Desktop first; see docs/平台环境安装与接续.md.' }
$icpEngine=& $icpDocker info --format '{{.OSType}}' 2>$null
if ($LASTEXITCODE -ne 0 -or $icpEngine -ne 'linux') { throw 'Docker Linux engine is not ready. Start Docker Desktop and wait for Engine running.' }
$icpComposeVersion=& $icpDocker compose version --short 2>$null
if ($LASTEXITCODE -ne 0 -or $icpComposeVersion -notmatch '\d+\.\d+\.\d+') { throw 'Docker Compose version cannot be verified.' }
if ([version]$Matches[0] -lt [version]'2.26.1') { throw 'Docker Compose 2.26.1 or later is required.' }
Push-Location -LiteralPath $icpWorkspace
try {
 # --quiet prevents Compose from printing the fully resolved secret environment.
 & $icpDocker compose -p ('icp-'+$Platform) --env-file .env -f icp.compose.yaml config --quiet
 if ($LASTEXITCODE -ne 0) { throw 'Docker Compose validation failed. Platform was not started.' }
 & $icpDocker compose -p ('icp-'+$Platform) --env-file .env -f icp.compose.yaml up -d
 if ($LASTEXITCODE -ne 0) { throw 'Platform startup failed. Existing data retained; inspect service status.' }
} finally { Pop-Location }
Write-Output ('Startup requested for '+$Platform+'. A successful command does not replace application health checks.')
