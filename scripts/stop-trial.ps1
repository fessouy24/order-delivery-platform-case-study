$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$dockerPath = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $dockerPath) {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    )) {
        if (Test-Path -LiteralPath $candidate) { $dockerPath = $candidate; break }
    }
}
if (-not $dockerPath) { throw 'Docker was not found.' }
Push-Location $projectPath
try {
    & $dockerPath compose --env-file .env.trial -f compose.trial.yaml stop
    if ($LASTEXITCODE -ne 0) { throw 'Trial services could not stop.' }
    Write-Output 'Trial stopped. Database and accounts are retained.'
} finally { Pop-Location }
