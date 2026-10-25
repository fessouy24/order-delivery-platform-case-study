param([switch]$Release,[string]$ServerUrl)
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
if(-not $ServerUrl){if($Release){throw 'Supply the company HTTPS ServerUrl for a release.'}else{$ServerUrl='http://localhost:8080'}}
$taskUri=[uri]$ServerUrl
if($taskUri.UserInfo -or $taskUri.Query -or $taskUri.Fragment -or $taskUri.AbsolutePath -ne '/' -or ($taskUri.Scheme -ne 'https' -and -not(-not $Release -and $taskUri.Scheme -eq 'http' -and $taskUri.Host -eq 'localhost'))){throw 'Use a company HTTPS origin (or localhost for a trial).'}
$taskVersion='1.0.4258.31'
$taskCache=Join-Path $taskProject ".local-runtime/windows-sdk/$taskVersion"
$taskDll=Join-Path $taskCache 'lib/net462/Microsoft.Web.WebView2.WinForms.dll'
if(-not(Test-Path -LiteralPath $taskDll)){
 New-Item -ItemType Directory -Force -Path $taskCache|Out-Null
 $taskPackage=Join-Path $taskCache 'sdk.nupkg'
 Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$taskVersion/microsoft.web.webview2.$taskVersion.nupkg" -OutFile $taskPackage
 [IO.Compression.ZipFile]::ExtractToDirectory($taskPackage,$taskCache,$true)
}
$taskOut=Join-Path $taskProject $(if($Release){'outputs/windows/OPSDEMO'}else{'outputs/windows/OPSDEMO-Trial'})
New-Item -ItemType Directory -Force -Path $taskOut|Out-Null
$taskConfig=Join-Path $taskProject '.local-runtime/windows-config.cs'
$taskTitle=if($Release){'OPSDEMO Operations'}else{'OPSDEMO Trial'}
$taskTrial=if($Release){'false'}else{'true'}
Set-Content -LiteralPath $taskConfig -Encoding utf8 -Value "namespace OpsdemoDesktop { internal static class CompanyConfig { internal const string ServerUrl = `"$($taskUri.GetLeftPart([UriPartial]::Authority))`"; internal const string Title = `"$taskTitle`"; internal const bool Trial=$taskTrial; } }"
$taskCore=Join-Path $taskCache 'lib/net462/Microsoft.Web.WebView2.Core.dll'
$taskCompiler='C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskExe=Join-Path $taskOut 'OPSDEMO.exe'
& $taskCompiler /nologo /target:winexe /platform:x64 "/out:$taskExe" "/win32icon:$(Join-Path $taskProject 'windows/opsdemo.ico')" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/reference:$taskCore" "/reference:$taskDll" (Join-Path $taskProject 'windows/Program.cs') $taskConfig
if($LASTEXITCODE -ne 0){throw 'Windows EXE compilation failed.'}
Copy-Item -LiteralPath $taskCore,$taskDll -Destination $taskOut
Copy-Item -LiteralPath (Join-Path $taskProject 'windows/opsdemo.ico') -Destination $taskOut
Copy-Item -LiteralPath (Join-Path $taskCache 'runtimes/win-x64/native/WebView2Loader.dll') -Destination $taskOut
$taskCheck=Start-Process -FilePath $taskExe -ArgumentList '--check' -WindowStyle Hidden -Wait -PassThru
if($taskCheck.ExitCode -ne 0){throw 'Microsoft WebView2 Runtime is missing on this laptop.'}
Get-Content (Join-Path $taskOut 'runtime-check.txt')
Set-Content -LiteralPath (Join-Path $taskOut 'Read me.txt') -Value "Open OPSDEMO.exe. Keep the DLL files beside it. Sign in with your assigned department account. This app uses the shared server database and requires a network connection. Trial server: $ServerUrl. Microsoft WebView2 Runtime is required."
Compress-Archive -Path "$taskOut/*" -DestinationPath "$taskOut.zip" -Force
Write-Output "Windows app: $taskExe"
