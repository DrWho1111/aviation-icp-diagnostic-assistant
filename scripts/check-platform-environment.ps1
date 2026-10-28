$ErrorActionPreference='Stop'
$icpProjectRoot=Split-Path -Parent $PSScriptRoot
$icpCandidates=@(
 'D:\Apps\Docker\resources\bin\docker.exe',
 'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
 (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe')
)
$icpDockerCommand=Get-Command docker -ErrorAction SilentlyContinue
if ($icpDockerCommand) { $icpCandidates=@($icpDockerCommand.Source)+$icpCandidates }
$icpDocker=$icpCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
& wsl --status *> $null
$icpWslReady=($LASTEXITCODE -eq 0)
$icpLinux=$false
$icpCompose=$null
$icpComposeSupported=$false
if ($icpDocker) {
 $icpDockerOS=& $icpDocker info --format '{{.OSType}}' 2>$null
 $icpLinux=($LASTEXITCODE -eq 0 -and $icpDockerOS -eq 'linux')
 $icpCompose=& $icpDocker compose version --short 2>$null
 if ($icpCompose -match '\d+\.\d+\.\d+') { $icpComposeSupported=([version]$Matches[0] -ge [version]'2.26.1') }
}
$icpResult=[PSCustomObject]@{
 WSLReady=$icpWslReady
 DockerCLI=[bool]$icpDocker
 LinuxEngineReady=$icpLinux
 ComposeVersion=$icpCompose
 ComposeSupported=$icpComposeSupported
 Ready=($icpWslReady -and $icpLinux -and $icpComposeSupported)
}
$icpResult | ConvertTo-Json
New-Item -ItemType Directory -Path (Join-Path $icpProjectRoot 'outputs') -Force | Out-Null
$icpResult | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $icpProjectRoot 'outputs/platform-preflight.json') -Encoding UTF8
if (-not $icpResult.Ready) { exit 1 }
