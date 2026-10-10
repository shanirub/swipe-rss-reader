# Android app

The swipe client for the backend's API (`../api/openapi.yaml`). Stage 5 in [`PROJECT_PLAN.md`](../PROJECT_PLAN.md); design and status in [`task_plan.md`](../task_plan.md).

Not buildable yet: the Gradle files come with the project setup (Phase 5 task list). Layout, as Android Studio creates it:

```
android/
├── settings.gradle.kts        project name, modules              (planned)
├── build.gradle.kts           plugins shared by modules          (planned)
├── gradle/
│   └── libs.versions.toml     version catalog: every dependency version in one place   (planned)
└── app/                       the app module
    ├── build.gradle.kts       SDK levels, versionCode, dependencies                    (planned)
    └── src/
        ├── main/java/         Kotlin sources (Android's convention keeps the java/ name),
        │                      in package io/github/shanirub/swiperss/ (= the applicationId)
        ├── main/res/          resources: strings, icons, themes
        ├── test/java/         local JVM tests (JUnit): fast, no phone
        └── androidTest/java/  instrumented tests: run on an emulator or device
```

Secrets stay out of git: `local.properties` (holds the API token) and the signing keystore are ignored by the root `.gitignore`.
