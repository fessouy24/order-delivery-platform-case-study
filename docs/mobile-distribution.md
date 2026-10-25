# Mobile and desktop builds

Android requires a compatible Android SDK, Java 17 and Gradle. Use `scripts/build-android.ps1`
for local builds. The debug apps target localhost and need the local server connection.
Production builds require an HTTPS origin and your own signing keystore supplied through
the `ANDROID_KEYSTORE_*` and related environment variables described in the Gradle files.
Never commit signing keys. Firebase background notifications require your own service account
and mobile app configuration; neither is bundled.

Windows uses `scripts/build-windows.ps1 -Release -ServerUrl https://YOUR_HOST` and Microsoft
WebView2. App identifiers and icons in this public edition use generic demo branding.
The native apps require connectivity for server operations and have no offline delivery queue.
