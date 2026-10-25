$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path -LiteralPath '.env.shopify-test')) {
    throw 'Create .env.shopify-test using docs/shopify-local-test.md first. Keep credentials out of chat.'
}
$dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
$env:PATH = "$dockerBin;$env:PATH"
$dockerExe = Join-Path $dockerBin 'docker.exe'
# Credentials apply only to this disposable check container, not the running worker/scheduler.
& $dockerExe compose --env-file .env.trial -f compose.trial.yaml -f compose.shopify-check.yaml run --build --rm --no-deps api python manage.py check_shopify
if ($LASTEXITCODE -ne 0) { throw 'Shopify check did not pass. See the message above.' }
