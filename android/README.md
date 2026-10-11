# Android app

The swipe client for the backend's API (`../api/openapi.yaml`). Stage 5 in [`PROJECT_PLAN.md`](../PROJECT_PLAN.md); design and status in [`task_plan.md`](../task_plan.md).

Current state: the project skeleton from Android Studio's "Empty Activity" wizard (one "Hello Android!" screen, one example JVM test), reviewed and trimmed. Libraries (kotlinx.serialization, Retrofit + OkHttp, Room, Custom Tabs) are added together with the code that first uses them.

## Build

Open `android/` in Android Studio, or from a terminal:

```
cd android
JAVA_HOME=~/.local/share/JetBrains/Toolbox/apps/android-studio/jbr ./gradlew assembleDebug testDebugUnitTest
```

`JAVA_HOME` only has to point to a JDK that can start Gradle (17 or newer; Android Studio's bundled one is used above). Gradle itself then runs on JDK 25, as pinned in `gradle/gradle-daemon-jvm.properties`, and downloads it if the machine doesn't have it. The debug APK lands in `app/build/outputs/apk/debug/app-debug.apk`; JVM test reports in `app/build/reports/tests/`.

CI: `.github/workflows/android.yml` runs the same command on GitHub (JDK 25, the runner's preinstalled Android SDK) when `android/`, `api/openapi.yaml` or the workflow itself changes.

## Layout

```
android/
├── settings.gradle.kts        project name, modules, where plugins and libraries come from
├── build.gradle.kts           plugins shared by modules (declared, applied in app/)
├── gradle.properties          Gradle's own settings (daemon memory, configuration cache)
├── gradlew, gradle/wrapper/   Gradle wrapper: pins the Gradle version (9.8.1)
├── gradle/
│   ├── libs.versions.toml     version catalog: every dependency version in one place
│   └── gradle-daemon-jvm.properties   JDK version Gradle runs on (25)
└── app/                       the app module
    ├── build.gradle.kts       SDK levels (min 36, target/compile 37), versionCode, dependencies
    └── src/
        ├── main/AndroidManifest.xml   app declaration: name, icon, screens, permissions; backup off
        ├── main/java/         Kotlin sources (Android's convention keeps the java/ name),
        │                      in package io/github/shanirub/swiperss/ (= the applicationId)
        ├── main/res/          resources: strings, launcher icon (still the default), base theme
        ├── main/keepRules/    rules for R8, the release-build shrinker (empty so far)
        └── test/java/         local JVM tests (JUnit): fast, no phone; no instrumented
                               (on-device) tests for now, see findings.md
```

Secrets stay out of git: `local.properties` (holds the API token) and the signing keystore are ignored by the root `.gitignore`.
