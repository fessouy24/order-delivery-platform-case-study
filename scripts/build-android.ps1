param([switch]$Release,[switch]$Production,[string]$ServerUrl,[string]$SdkPath,[string]$JavaPath)
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
$taskAndroid=Join-Path $taskProject 'android'
if(-not $JavaPath){
    foreach($taskCandidate in @($env:JAVA_HOME,'C:/Program Files/Android/Android Studio/jbr',"$env:USERPROFILE/AppData/Local/Programs/Android Studio/jbr")){
        if($taskCandidate -and (Test-Path -LiteralPath (Join-Path $taskCandidate 'bin/java.exe'))){$JavaPath=$taskCandidate;break}
    }
}
if(-not $SdkPath){
    foreach($taskCandidate in @($env:ANDROID_HOME,$env:ANDROID_SDK_ROOT,"$env:USERPROFILE/AppData/Local/Android/Sdk")){
        if($taskCandidate -and (Test-Path -LiteralPath $taskCandidate)){$SdkPath=$taskCandidate;break}
    }
}
if(-not $JavaPath){throw 'Install Android Studio and finish its Standard setup, or supply -JavaPath pointing to its jbr folder.'}
if(-not $SdkPath){throw 'Finish Android Studio SDK setup, or supply -SdkPath pointing to the Android SDK folder.'}
if(-not(Test-Path -LiteralPath (Join-Path $SdkPath 'platforms/android-36/android.jar'))){
    if(-not(Test-Path -LiteralPath (Join-Path $SdkPath 'licenses/android-sdk-license'))){throw 'In Android Studio SDK Manager install Android 16 (API 36) and accept its SDK licence, then run this build again.'}
    Write-Output 'The build will download the missing Android SDK 36 using the SDK licence already accepted in Android Studio.'
}
if($Release -and -not $ServerUrl){throw 'An employee release requires -ServerUrl https://YOUR_COMPANY_HOST.'}
$taskOldJava=$env:JAVA_HOME;$taskOldAndroid=$env:ANDROID_HOME;$taskOldGradle=$env:GRADLE_USER_HOME
try{
    $env:JAVA_HOME=$JavaPath;$env:ANDROID_HOME=$SdkPath;$env:GRADLE_USER_HOME=Join-Path $taskProject '.local-runtime/gradle'
    if(-not $Release){
        $taskKey=Join-Path $taskProject '.local-runtime/android-debug.keystore'
        if(-not(Test-Path -LiteralPath $taskKey)){
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $taskKey)|Out-Null
            & (Join-Path $JavaPath 'bin/keytool.exe') -genkeypair -keystore $taskKey -storepass android -keypass android -alias androiddebugkey -dname 'CN=OPSDEMO Local Trial,O=OPSDEMO,C=EG' -keyalg RSA -keysize 2048 -validity 3650
            if($LASTEXITCODE -ne 0){throw 'Trial signing key could not be created.'}
        }
    }
    Push-Location $taskAndroid
    try{
        $taskModule=if($Production){'production'}else{'app'}
        $taskArguments=@('--no-daemon',":${taskModule}:$(if($Release){'assembleRelease'}else{'assembleDebug'})")
        if($Release){$taskArguments+="-PcompanyServerUrl=$ServerUrl"}
        & './gradlew.bat' @taskArguments
        if($LASTEXITCODE -ne 0){throw 'Android build failed. Review the diagnostics above.'}
        $taskVariant=if($Release){'release'}else{'debug'}
        $taskApk=Join-Path $taskAndroid "$taskModule/build/outputs/apk/$taskVariant/$taskModule-$taskVariant.apk"
        if(-not(Test-Path -LiteralPath $taskApk)){throw 'No signed APK was produced.'}
        $taskOutput=Join-Path $taskProject 'outputs/android';New-Item -ItemType Directory -Force -Path $taskOutput|Out-Null
        $taskDestination=Join-Path $taskOutput $(if($Production){if($Release){'OPSDEMO-Production.apk'}else{'OPSDEMO-Production-Trial.apk'}}elseif($Release){'OPSDEMO.apk'}else{'OPSDEMO-Trial.apk'})
        Copy-Item -LiteralPath $taskApk -Destination $taskDestination
        Write-Output "APK created: $taskDestination"
    }finally{Pop-Location}
}finally{$env:JAVA_HOME=$taskOldJava;$env:ANDROID_HOME=$taskOldAndroid;$env:GRADLE_USER_HOME=$taskOldGradle}
