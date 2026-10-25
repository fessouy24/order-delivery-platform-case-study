$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
Push-Location (Join-Path $projectPath 'backend')
try {
    $env:APP_ENV = 'development'
    $env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $env:POSTGRES_DB = 'opsdemo'
    $env:POSTGRES_USER = 'opsdemo'
    $env:POSTGRES_PASSWORD = 'test-only-do-not-deploy'
    $env:POSTGRES_HOST = '127.0.0.1'
    $env:REDIS_URL = 'redis://127.0.0.1:6379/0'
    $env:DJANGO_SETTINGS_MODULE = 'config.settings_unit'
    & $pythonPath manage.py check --settings=config.settings_unit
    if ($LASTEXITCODE -ne 0) { throw 'Django checks failed' }
    & $pythonPath -m pytest --ds=config.settings_unit tests/test_health.py tests/test_environment.py tests/test_calendar.py tests/test_requirements.py tests/test_migrations.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Foundation tests failed' }
} finally {
    Pop-Location
}
