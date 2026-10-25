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
if (-not $dockerPath) { throw 'Docker was not found. Open Docker Desktop first.' }
$previousPath = $env:PATH
$env:PATH = "$(Split-Path -Parent $dockerPath);$previousPath"
Push-Location $projectPath
try {
    & $dockerPath info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0) { throw 'Start the Docker Desktop Linux engine first.' }
    & '.\.venv\Scripts\python.exe' scripts/init-trial.py
    if ($LASTEXITCODE -ne 0) { throw 'Trial configuration failed.' }
    & $dockerPath compose --env-file .env.trial -f compose.trial.yaml up --build -d
    if ($LASTEXITCODE -ne 0) { throw 'Trial services could not start.' }
    Get-Content -LiteralPath '.trial-accounts.json' -Raw | & $dockerPath compose --env-file .env.trial -f compose.trial.yaml exec -T api python manage.py seed_trial
    if ($LASTEXITCODE -ne 0) { throw 'Trial account setup failed.' }
    if (Test-Path -LiteralPath '.env.market-trial') {
        & $dockerPath compose --env-file .env.trial -f compose.trial.yaml -f compose.market-trial.yaml up -d api web
        if ($LASTEXITCODE -ne 0) { throw 'Local Training connection could not start.' }
    }
    Start-Process 'http://localhost:8080'
    Write-Output 'Trial ready at http://localhost:8080. Accounts are in .trial-accounts.json.'
} finally {
    Pop-Location
    $env:PATH = $previousPath
}
