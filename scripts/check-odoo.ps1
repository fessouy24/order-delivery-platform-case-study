$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path -LiteralPath '.env.odoo-test')) { throw 'Configure the private .env.odoo-test file first.' }
$dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
$env:PATH = "$dockerBin;$env:PATH"
& (Join-Path $dockerBin 'docker.exe') compose --env-file .env.trial -f compose.trial.yaml -f compose.odoo-check.yaml run --build --rm --no-deps api python manage.py check_odoo
if ($LASTEXITCODE -ne 0) { throw 'Odoo connection check did not pass. See the message above.' }
