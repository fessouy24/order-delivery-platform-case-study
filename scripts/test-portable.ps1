param([switch]$GenerateMigrations, [Parameter(ValueFromRemainingArguments=$true)][string[]]$TestArgs)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
Push-Location (Join-Path $projectPath 'backend')
try {
    $env:APP_ENV = 'development'
    $env:DJANGO_SECRET_KEY = 'test-configuration-only-0000000000000000000000000000000000000'
    $env:POSTGRES_DB = 'opsdemo'
    $env:POSTGRES_USER = 'opsdemo'
    $env:POSTGRES_PASSWORD = 'test-only-do-not-deploy'
    $env:REDIS_URL = 'redis://127.0.0.1:6379/0'
    $env:DJANGO_SETTINGS_MODULE = 'config.settings_test'
    if ($GenerateMigrations) {
        & $pythonPath manage.py makemigrations operations --noinput
        if ($LASTEXITCODE -ne 0) { throw 'Migration generation failed' }
    }
    & $pythonPath manage.py check
    if ($LASTEXITCODE -ne 0) { throw 'Framework checks failed' }
    & $pythonPath -m pytest --ds=config.settings_test @TestArgs
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
} finally { Pop-Location }
