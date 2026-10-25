$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$targetPath = Join-Path $projectPath '.env'
if (Test-Path -LiteralPath $targetPath) {
    throw 'A local environment already exists. It has not been overwritten.'
}
$template = Get-Content -LiteralPath (Join-Path $projectPath '.env.example') -Raw
$secret = [Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(64))
$databasePassword = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$template = $template.Replace('replace-with-a-generated-random-secret-at-least-50-characters', $secret)
$template = $template.Replace('replace-with-a-generated-database-password', $databasePassword)
[System.IO.File]::WriteAllText($targetPath, $template)
Write-Output 'Created the new local environment with generated secrets. No existing credentials were read.'
