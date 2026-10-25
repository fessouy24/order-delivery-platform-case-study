param([string]$SdkPath,[string]$DeviceId)
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
if(-not $SdkPath){$SdkPath=if($env:ANDROID_HOME){$env:ANDROID_HOME}else{"$env:USERPROFILE/AppData/Local/Android/Sdk"}}
$taskAdb=Join-Path $SdkPath 'platform-tools/adb.exe'
if(-not(Test-Path -LiteralPath $taskAdb)){throw 'Android Platform-Tools are missing. Finish Android Studio setup.'}
$taskApk=Join-Path $taskProject 'outputs/android/OPSDEMO-Trial.apk'
if(-not(Test-Path -LiteralPath $taskApk)){throw 'Build the trial APK with scripts/build-android.ps1 first.'}
$taskDeviceArgs=@();if($DeviceId){$taskDeviceArgs=@('-s',$DeviceId)}
& $taskAdb @taskDeviceArgs devices
& $taskAdb @taskDeviceArgs reverse tcp:8080 tcp:8080
if($LASTEXITCODE -ne 0){throw 'Connect one Android phone by USB, enable USB debugging and approve this laptop on the phone.'}
& $taskAdb @taskDeviceArgs install -r $taskApk
if($LASTEXITCODE -ne 0){throw 'APK installation failed. Check the device prompt and available storage.'}
& $taskAdb @taskDeviceArgs shell am start -n com.opsdemo.operations.trial/com.opsdemo.operations.MainActivity
Write-Output 'Trial app started. Keep USB connected and localhost/Docker running.'
