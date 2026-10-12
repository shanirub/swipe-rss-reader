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
| Local database | Do the Room queries keep the stack and the outbox right? | `LocalStoreTest.kt` |
| Mutants (by hand) | Can each test actually fail? | see [below](#mutation-testing) |

- **Client behaviour.** Example: the interceptor stops adding the token → `queueSendsTokenAndLimitAndReadsNulls` fails.
- **Spec conformance.** The backend generates its models from the spec; the app's models are written by hand (`PROJECT_PLAN.md` §3), so this test is what keeps them in sync. Example: the spec makes `FeedStatus.url` nullable while Kotlin keeps `String` → `feedListResponsesDecode` fails, before the phone crashes on a `null`.
- **Local database.** Example: the swipe stops deleting its card in the same transaction → `aFailedSwipeLeavesTheCardOnTheStack` fails.

## Strategy

- **No network, no server.** API calls go to MockWebServer, a fake HTTP server inside the test (the backend's `httpx.MockTransport` counterpart).
- **The real JSON settings.** Tests encode and decode with the app's own `apiJson`, not a test copy.
- **The spec is the oracle.** The conformance test validates JSON against the spec's schemas with a JSON Schema validator (networknt, test-only, never in the APK), with `format` checks (date-time, uuid) switched on. Which response fields may be null comes from the spec, not from the samples.
- **A real SQLite, no phone.** Room tests run on an in-memory database with the bundled SQLite driver (test-only: its own SQLite for the desktop, since Android's isn't there); the app keeps Android's SQLite. Room's builder wants an Android `Context`; the tests set the two things it would use it for (journal mode, query thread), so an empty placeholder is enough. If a Room update starts using it, the tests fail loudly; the fallback then is Robolectric (`PROJECT_PLAN.md` §3).
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
| card dedup ignores the outbox | `swipedCardsAreNotAddedAgain` |
| card dedup ignores the stack, or `feed_id` | `newCardsKeepTheirOrderAndKnownOnesAreSkipped` |
| stack order reversed | `newCardsKeepTheirOrderAndKnownOnesAreSkipped`, `cardsComeBackUnchanged` |
| `recordSwipe` without `@Transaction` | `aFailedSwipeLeavesTheCardOnTheStack` |
| a swipe doesn't delete its card | `aSwipeMovesTheCardToTheOutbox` and two more |
| `pending` includes dead letters; `delete` hits the wrong rows | `pendingSkipsDeadLettersAndDeleteRemovesOnlyTheSentSwipes` |
| dead letters revived on the same version | `deadLettersAreRetriedOncePerNewAppVersion` |
| revive keeps the old version number | `deadLettersAreRetriedOncePerNewAppVersion` (survived until the test checked the revived row's fields) |

## Test files

In `java/io/github/shanirub/swiperss/api/`:

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

In `java/io/github/shanirub/swiperss/db/`:

**`LocalStoreTest.kt`**: the Room database (`LocalStore.kt`) on an in-memory SQLite.
- New cards keep their order; cards already on the stack are skipped (the same key in another feed is a new card)
- Cards come back exactly as stored, nulls included
- A swipe moves its card from the stack to the outbox, stored exactly as recorded
- A failed swipe (duplicate `swipe_id`) leaves the card on the stack: one transaction
- Swiped cards, pending or dead letter, are not added again
- `pending` skips dead letters and respects the limit; `delete` removes only the sent swipes
- Dead letters are retried once per new app version
