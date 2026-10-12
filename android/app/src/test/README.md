# Android tests

How the app is tested and what each test file covers. The backend's equivalent is [`backend/tests/README.md`](../../../../backend/tests/README.md).

## Running the tests

From `android/`:

```sh
JAVA_HOME=~/.local/share/JetBrains/Toolbox/apps/android-studio/jbr ./gradlew testDebugUnitTest
```

In Android Studio: right-click `test/java` → Run 'Tests in …'. Reports land in `app/build/reports/tests/testDebugUnitTest/`. CI (`.github/workflows/android.yml`) runs the same tests on every change to `android/` or `api/openapi.yaml`.

Gradle skips a test run whose inputs haven't changed ("UP-TO-DATE"). The spec is one of the inputs (`app/build.gradle.kts`), so editing `api/openapi.yaml` re-runs the tests; `--rerun` forces a run anyway.

## Where tests live

| Folder | Runs on | Here |
|---|---|---|
| `src/test/` | the desktop's JVM (local unit tests): fast, no phone | all current tests |
| `src/androidTest/` | a phone or emulator (instrumented tests) | none: JVM tests only (`PROJECT_PLAN.md` §3 Android) |

Test files mirror the package of the code they test: `test/java/io/github/shanirub/swiperss/api/` tests `main/java/.../api/`.

## Test categories

| Category | Answers the question | Where |
|---|---|---|
| Client behaviour | Does the app send the right requests and read the answers? | `ApiClientTest.kt` |
| Spec conformance | Do the hand-written models match `api/openapi.yaml`? | `SpecConformanceTest.kt` |
| Mutants (by hand) | Can each test actually fail? | see [below](#mutation-testing) |

- **Client behaviour.** Example: the interceptor stops adding the token → `queueSendsTokenAndLimitAndReadsNulls` fails.
- **Spec conformance.** The backend generates its models from the spec; the app's models are written by hand (`PROJECT_PLAN.md` §3), so this test is what keeps them in sync. Example: the spec makes `FeedStatus.url` nullable while Kotlin keeps `String` → `feedListResponsesDecode` fails, before the phone crashes on a `null`.

## Strategy

- **No network, no server.** API calls go to MockWebServer, a fake HTTP server inside the test (the backend's `httpx.MockTransport` counterpart).
- **The real JSON settings.** Tests encode and decode with the app's own `apiJson`, not a test copy.
- **The spec is the oracle.** The conformance test validates JSON against the spec's schemas with a JSON Schema validator (networknt, test-only, never in the APK), with `format` checks (date-time, uuid) switched on. Which response fields may be null comes from the spec, not from the samples.
- **The validator is tested too.** `validatorRejectsInvalidSwipes` feeds it broken swipes; if it ever accepted everything (unresolved schema, formats off), the other conformance tests would pass for nothing.

## Mutation testing

By hand only: no maintained tool fits this setup (`PROJECT_PLAN.md` §3; mutflow is noted in §7 for later). After writing a guard test, break the code or the spec it protects on purpose, run the tests, expect a failure, and restore the file. Mutants checked so far:

| Mutant | Caught by |
|---|---|
| the token interceptor removed | `queueSendsTokenAndLimitAndReadsNulls` |
| `explicitNulls = false` (null fields left out of requests) | `postSwipesSendsEveryFieldIncludingNulls` |
| `ignoreUnknownKeys = false` | `unknownResponseFieldsAreIgnored` |
| a wrong action `@SerialName` | `actionsUseTheSpecNames` |
| a model field's spec name changed (`item_key`) | `swipeBatchTheAppSendsIsValid`, `queueResponsesDecode` |
| a nullable model field made non-null (`Card.link`, `FeedStatus.name`) | `queueResponsesDecode`, `feedListResponsesDecode` |
| an action's spec name changed (`read_now`) | `swipeBatchTheAppSendsIsValid` |
| spec: a limit lowered (`tz_offset_minutes` max) | `swipeBatchTheAppSendsIsValid` |
| spec: an action renamed | `swipeBatchTheAppSendsIsValid` |
| spec: a new required swipe field | `swipeBatchTheAppSendsIsValid` |
| spec: a response field renamed (`items`) | `queueResponsesDecode` |
| spec: a response field made nullable (`url`, `headline`) | `feedListResponsesDecode`, `queueResponsesDecode` |

## Test files

All in `java/io/github/shanirub/swiperss/api/`.

**`ApiClientTest.kt`**: the API client against MockWebServer.
- `GET /queue` sends the token and `limit`, and reads cards with nulls
- `POST /swipes` sends every field under its spec name, nulls included, the card unchanged
- Actions are sent as `never`, `save`, `read_now`
- `GET /feeds` reads never-fetched feeds
- Unknown response fields are ignored (the server may add fields)
- Error statuses (401, 422, 503) throw with their code

**`SpecConformanceTest.kt`**: the models against `api/openapi.yaml`.
- A `SwipeBatch` the app builds (every action, null and filled fields) is valid for the spec
- Every response the app reads (`QueueResponse`, `SwipeBatchResult`, `FeedListResponse`), all null and all filled, is valid for the spec, decodes, and encodes back to the same JSON
- Every field the spec allows to be null decodes as null, one field at a time
- The validator rejects broken swipes: unknown or missing field, unknown action, bad date-time, bad uuid, offset out of range, renamed card field
