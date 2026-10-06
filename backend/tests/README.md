# Backend tests

How the backend is tested, why, and what each test file covers.

- [Running the tests](#running-the-tests)
- [Test categories](#test-categories)
- [Strategy](#strategy)
- [Mutation testing](#mutation-testing)
- [Test files](#test-files)

## Running the tests

From `backend/`:

```sh
uv run pytest                                            # all tests, incl. contract coverage
uv run ruff check . && uv run ruff format --check .      # lint (part of the check)
uv run python scripts/mutants.py                         # curated mutation checks (~5 min)
uv run python scripts/run_mutmut.py                      # generated mutation testing (~2 min on 28 cores)
```

### CI

`.github/workflows/ci.yml` runs on every push, in parallel jobs:

| Job | Runs on | Checks |
|---|---|---|
| `backend` | every push | lint, the tests, the curated mutation checks; no Docker (the tests call the code directly) |
| `docker` | every push | the image builds and the app imports inside it |
| `compose-smoke` | every push | `docker compose up` of `migrate` + `api` as on the server: migrate exits 0, api becomes healthy, `/health`, auth and `/feeds` answer |
| `mutmut` | pull requests, manual ("Run workflow") | generated mutation testing, ~10–15 min; a report, not a gate |

The smoke job leaves the scheduler off, because it would fetch real feeds.

## Test categories

| Category | Answers the question | Where |
|---|---|---|
| Happy paths | Does each feature do what `PROJECT_PLAN.md` says? | `test_api_endpoints.py`, `test_fetcher.py`, `test_extraction.py`, `test_dedup.py`, `test_text.py` |
| Sad paths (must-fail) | Are bad requests rejected as the contract says? | `test_api.py`, `test_api_input.py`, `test_contract.py`, `test_safe_fetch.py`, `test_feeds.py` |
| Spec → code | Is everything the spec promises implemented and tested? | `test_contract.py`, `contract.py` + `conftest.py`, `test_api_models.py` |
| Code → spec | Does the code do nothing the spec doesn't describe? | `test_contract.py`, `contract.py` + `conftest.py` |
| Property-based | Do generated inputs nobody thought of get documented answers? | `test_schemathesis.py` |
| Curated mutants | Can each important guard test actually fail? | `scripts/mutants.py` |
| Generated mutants | Where is code that no test would notice breaking? | `scripts/run_mutmut.py`, `[tool.mutmut]` in `pyproject.toml` |

Why each category exists, and an example of a failure it catches:

- **Happy paths.** They prove the features work. Example: a change to the queue query makes one feed's cards come before every other feed's → `test_queue_is_round_robin_oldest_first` fails.
- **Sad paths.** Rejections are part of the contract and of security, and bugs like to hide in error handling. Example: someone moves the token check into a route dependency → a broken body without a token gets 422 (and is read and logged) → `test_malformed_body_without_token_is_401_and_not_logged` fails.
- **Spec → code.** The spec is the single source of truth, so nothing may be documented that the code doesn't do or no test checks. Example: a new status code is added to `api/openapi.yaml` without a test → the run ends with `documented but no test produces it: …`. A field limit changed in the spec without regenerating the models → `test_generated_models_are_fresh` fails.
- **Code → spec.** An undocumented endpoint or response is invisible to the app's developer and can't be relied on. Example: a route is added in `api.py` but not in the spec → `test_app_routes_equal_the_spec` fails. An endpoint answers an undocumented status → the run ends with `returned but not documented: …`.
- **Property-based.** Example tests only cover inputs someone thought of. Example: an integer field without a maximum lets a 64-bit overflow reach SQLite → Schemathesis reports a 500 with a `curl` command that reproduces it.
- **Curated mutants.** Tests for the tests. Example: the mutant "unknown query parameters ignored" must be `KILLED` by `test_unknown_query_parameter_is_422`; if it `SURVIVED`, that rule has no working test.
- **Generated mutants.** They find gaps nobody thought to look for. Example: replacing `continue` with `break` in a loop survives → a test is missing for "later items are still processed".

Line coverage (`coverage.py`) is not a category: it measures how much code the tests *run*, not whether they would notice a bug. The two mutation categories cover that.

## Strategy

- **Real SQLite, real migrations.** Tests that touch the database get a fresh file database in a temp dir, migrated with Alembic to `head` (`engine` fixture in `conftest.py`). The schema under test is exactly what production runs, including WAL mode, STRICT tables and `BEGIN IMMEDIATE`. No database mocks.
- **No network.** Feed fetching uses `httpx.MockTransport`, so HTTP behaviour (200, 304, errors, oversized bodies) is simulated deterministically. Feed bodies are built with the `rss()` helper in `conftest.py`.
- **No internet even when the code is wrong.** The SSRF guard tests use a real HTTP server on `127.0.0.1` and fake DNS answers. Where a broken guard could open a real connection, the socket call is replaced too.
- **Test the guarantee, not just the code.** Where a rule protects against a whole class of bugs, the test checks the rule itself, e.g. that every ingested item is a valid API `Card`, or that the generated API models match the spec.
- **Guard tests are checked by mutants.** A new guard test is verified by deliberately breaking the code it protects (e.g. raising a limit above the spec) and confirming the test fails. This also catches tests that pass for the wrong reason. See [Mutation testing](#mutation-testing).
- **Generated findings become example tests.** A case found by Schemathesis is pinned by an example-based regression test (e.g. in `test_api_input.py`), so the fix stays covered even when random generation doesn't hit the case again.

### Contract coverage

The app's routes must equal the spec's operations (`test_contract.py`). Beyond that, `conftest.py` records every response a test client receives as (method, spec path, status). After a **full, green** run, the recorded set must equal the responses documented in `api/openapi.yaml`:

- a documented response that no test produces fails the run (`documented but no test produces it`);
- a response the spec doesn't document fails the run too (`returned but not documented`).

The result appears as a red "contract coverage" section after the test summary, with exit code 1.

The check is skipped when "missing" would only mean "not run this time":

- partial runs: a single file, `-k`, `-m`, deselected tests, mutmut's subsets;
- runs with failing tests.

Requests to paths outside the spec (test-only routes) are not recorded.

### The spec's global rules

Must-fail tests in `test_contract.py` and `test_api_input.py` cover the rules that apply to every operation:

| Request | Response |
|---|---|
| no token, any path except `/health` | 401, before routing; the body is not read |
| body over 10 MB | 413 |
| with token, unknown path | 404 |
| with token, method the spec doesn't list | 405 with `Allow` |
| with token, unknown query parameter | 422, on every operation except `/health` |

For a method the spec doesn't list, 405 and 401 are the only responses the recorder accepts without a per-operation entry, because OpenAPI can't attach responses to operations that don't exist.

### Property-based testing

`test_schemathesis.py` lets Schemathesis generate requests from the spec: 50 per operation per run, in-process against the app. The token is registered as auth, so Schemathesis can also leave it out on purpose.

## Mutation testing

The regular tests check the code; mutation tests check the **tests**: they introduce a bug and expect some test to fail.

### Curated mutants (`mutants.py`)

`scripts/mutants.py` holds a hand-picked list of mutants: one deliberate, realistic bug per important rule (a card limit above the spec, a missing database constraint, an endpoint without auth, a stale generated model, …). For each one it introduces the bug, runs the guard test that should catch it, and restores the file.

```sh
uv run python scripts/mutants.py           # all mutants (~5 min)
uv run python scripts/mutants.py auth      # only mutants whose name contains "auth"
uv run python scripts/mutants.py --list    # what exists, without running
```

| Verdict | Meaning |
|---|---|
| `KILLED` | the named test caught the bug (expected) |
| `KILLED-OTHER` | caught, but by a different test than expected, possibly for the wrong reason |
| `SURVIVED` | no test caught it: the guard test is broken or missing |
| `STALE` | the code changed and the mutant's snippet needs updating |

Run it after changing a guard test or the code a mutant targets, and when adding a guard test (add a mutant for it). It is not part of `pytest`. It covers only the listed rules, so a clean run is not a completeness measure; the script's docstring has the details.

### Generated mutants (mutmut)

`scripts/run_mutmut.py` runs [mutmut](https://github.com/boxed/mutmut), which *generates* mutants across `src/swipe_rss/` (flipped comparisons, negated conditions, changed constants, removed arguments, …) and reports per module how many the tests killed. Where `mutants.py` proves that specific guards work, mutmut *discovers* gaps: its first run found the blank-query-parameter, dedup-key-stability, redirect, User-Agent, missing-author and updated-date gaps, now covered in `test_dedup.py`, `test_fetcher.py` and `test_text.py`.

```sh
uv run python scripts/run_mutmut.py                  # whole backend (~2 min on 28 cores, ~10–15 min on a GitHub runner)
uv run python scripts/run_mutmut.py fetcher --show   # one module, with the diff of every survivor
uv run python scripts/run_mutmut.py --fresh          # delete cached results first
```

Every surviving mutant gets one of three verdicts:

- **real gap**: the change would matter, and no test notices it;
- **equivalent**: the change can't alter behaviour;
- **noise**: the change is harmless, e.g. a log message.

Only real gaps whose regression would corrupt or silently lose data, or weaken security, get a test; the rest are accepted with a one-line reason (triage policy in `findings.md`, Technical Decisions). `no tests` means no test runs that code at all (currently `cli.py`).

The score (killed / (killed + survived)) is a trend to watch, not a target; 100% is not achievable. mutmut is exploration, not a gate: the script exits 0 even with survivors.

Configuration lives in `[tool.mutmut]` in `pyproject.toml`; the script's docstring explains it in detail. Two settings are unusual:

- two path-dependent tests are deselected, because mutmut runs the tests from a copy in `backend/mutants/` (git- and docker-ignored);
- `test_schemathesis.py` is ignored: its generated test ids crash mutmut, and its random inputs would make results non-reproducible.

## Test files

Grouped by the part of the system they test.

### Shared helpers

**`conftest.py`**: shared fixtures and the contract recorder.
- Temp database migrated to `head` (`engine`, `db_path`)
- The `rss()` feed builder

**`contract.py`**: not a test file. Contract-coverage helpers used by `conftest.py` and `test_contract.py`: spec loading, path matching, the response recorder.

### Database

**`test_db.py`**: SQLite settings.
- WAL, `busy_timeout` and foreign keys are on
- Every table is STRICT, and STRICT is enforced
- Every transaction takes the write lock up front (`BEGIN IMMEDIATE`)

**`test_migrations.py`**: Alembic migrations.
- `models.py` and the migrations describe the same schema (Alembic's comparison; it doesn't compare CHECK constraints, hence the explicit constraint tests)
- Downgrade to `0002` and upgrade again
- `0002` caps items stored before the card limits existed and leaves valid rows untouched
- `0003` swipe/saved rows join, and its CHECK and foreign-key constraints reject bad rows
- `0004`: `swipes` rejects every DELETE and UPDATE (checked at head, so a later table rebuild that drops the triggers fails the test), still accepts inserts; the downgrade removes the triggers

### Ingest

**`test_feeds.py`**: loading `feeds.toml`.
- The repo's real file is valid
- Invalid files raise instead of being read as "no feeds": no feeds, duplicate ids, bad slugs, non-http(s) URLs, typo'd keys in feeds or `[defaults]`, TOML syntax errors
- A missing file raises

**`test_fetcher.py`**: the fetch run end to end.
- Age filter and normalization
- A rerun inserts nothing; tombstones block re-insertion after pruning; the first version wins
- A new entry after an already-seen one is still stored
- The same article in two feeds is kept twice
- Conditional GET (304) with ETag and with Last-Modified; a 304 keeps the stored validators
- Failing, unparseable, oversized, hanging (timeout) and unexpectedly crashing feeds are recorded without stopping the run; feed status after a failure and after recovery
- A response in several chunks is read whole; the charset from the HTTP header is used (Hebrew windows-1255)
- Redirects are followed; requests send the reader's User-Agent
- An entry without a `<title>` element is skipped, later entries kept
- Author and tags reach the database; a missing or blank author becomes null
- Dedup inputs: an entry without GUID or link is keyed by title and date; the `dedup = "link"` override ignores the GUID
- An Atom entry with only an updated date gets that date
- A date outside the API's timestamp range becomes null
- Card limits: fetcher limits ≤ the API spec's limits, an oversized entry becomes a valid `Card`, and a dropped link still determines the item's key

**`test_dedup.py`**: deduplication keys.
- Link normalization, incl. empty query parameters kept
- Rule order: GUID → normalized link → title+date hash
- The `dedup = "link"` override; blank GUIDs
- **Golden keys**: exact key values are pinned, because keys are permanent item identity and any change to the key function needs a migration plan

**`test_text.py`**: HTML → plain text and truncation.
- Tags, entities, script/style removal
- Truncation at the exact length boundary, with no whitespace before the `…`

**`test_timestamps.py`**: the API's timestamp range.
- Both boundaries
- Offsets compared in absolute time
- Values that leave `datetime`'s range in UTC (year 0, year 10000) are out of range instead of crashing

### API

**`test_api.py`**: app skeleton and auth (`BearerTokenMiddleware`).
- `/health` is public and matches the spec
- FastAPI's own `/docs`, `/redoc`, `/openapi.json` are off (404 with the token)
- The app refuses to start without a token of at least 32 characters
- Only `/health` is public; every other route answers 401 without the token (the test asserts it actually sees routes); routers included later are protected too
- Bad credentials (missing, wrong, prefix, wrong scheme, no scheme) get 401 with `WWW-Authenticate: Bearer` and the spec's `Error` body
- The right token passes, with the scheme in any case

**`test_api_endpoints.py`**: every endpoint against a real migrated database.
- `GET /queue`:
  - round-robin order, oldest first, undated items by fetch time
  - `limit` bounds
  - served cards are valid swipe cards; a row that isn't a valid card is skipped instead of failing the queue
  - feed ids fit the API
- `POST /swipes`:
  - round trip: stored flat with every field, the item flagged, not deleted
  - idempotent resend; a duplicate doesn't stop the rest of the batch; every new swipe counted
  - one saved entry per article, and only for `save`
  - swipes for pruned items are stored
  - one invalid swipe rejects the whole batch (nothing stored); malformed and oversized batches; a tampered card rejected; rejected batches logged
  - a swipe flags only its own item (not other items of the feed, not the same article in another feed); the first `swiped_at` is kept
- `GET /feeds`:
  - feeds in `feeds.toml` order with name and URL
  - never-fetched feeds with nulls and zero failures; fetch state passed through
  - feeds removed from the file are not listed; the file is re-read per request
  - invalid file → 503 with the reason
- `GET /saved`:
  - newest save first
  - fields from the save swipe (link, `read_at` passed through)
  - still listed after the item is pruned
- Saved content:
  - pending → no text; done → text; failed → error and no text
  - not saved → 404; malformed `feed_id` or `item_key` → 422

**`test_api_input.py`**: input that never reaches the models, and the order of the checks.
- Malformed JSON, missing or wrong content type, empty body, invalid UTF-8 → 422, not 500; `application/json; charset=utf-8` accepted
- Strict JSON types: a number as a timestamp or a string as an integer → 422; `180.0` counts as an integer
- Integers beyond the spec's maximum → 422, not a 500 from SQLite
- Timestamps outside 1970 ≤ t < 3000 → 422 for each of the three fields (incl. year 0 and year 10000 after UTC conversion); the boundaries are accepted
- Body over the limit → 413, for Content-Length and chunked; the exact boundary is allowed
- A body in several ASGI messages (as uvicorn delivers it) is read whole; many small messages over the limit → 413 (TestClient always delivers one message, so these use `run_asgi`)
- Our middleware's 401 and 413 are well-formed: JSON content type, correct Content-Length, the spec's `Error` body
- Without the token the body is not read at all: a broken body gets 401 and isn't logged, an oversized one 401 (not 413)
- A rejected batch is logged with a capped body, and errors without repeated input; the swipe IDs are logged on every rejection path (wrong content type, timestamp range)

**`test_contract.py`**: the spec and the app match.
- The app's (method, path) set equals the spec's; every spec operation documents responses
- Request paths map to the right spec template (a parameter never spans a `/`)
- The recorder reports missing and undocumented responses (a wrong method is accepted only as 405 or 401); status ranges like `4XX` are refused loudly
- The spec's global rules (see [the table above](#the-specs-global-rules)), also for unknown paths and wrong methods without a token, with:
  - the right `Allow` header for every unlisted method
  - only the unknown query parameter reported, and all unknown ones reported
  - declared parameters accepted, also when declared in a dependency
  - the token checked before query parameters

**`test_schemathesis.py`**: property-based tests.
- Generated requests for every spec operation get documented, schema-conformant answers
- No 5xx; invalid data rejected; auth enforced
- A `map_body` hook moves generated timestamps into the range the spec documents in prose (see its comment)

**`test_api_models.py`**: `src/swipe_rss/api_models.py` is up to date with `api/openapi.yaml` (runs the generator's `--check`).

**`test_logs.py`**: logging setup.
- `SWIPE_RSS_LOG_LEVEL` applies to the app and uvicorn; an unknown level fails at startup
- Health checks are dropped from the access log; other requests are kept
- The filter is installed once, on uvicorn's real access logger

**`test_config.py`**: runtime settings.
- Every setting comes from its environment variable (DB path, feeds path, token, log level, backup directory)
- An empty token counts as unset

### Retention

**`test_prune.py`**: the pruning job, written before the code (test-first).
- The limits are the decided ones: items 2 days, saved entries 2 weeks
- Items are deleted 2 days after `fetched_at`, swiped or not; exactly 2 days is kept; the publish date doesn't matter
- Saved entries are deleted 2 weeks after the save swipe's `received_at` (server clock, not the phone's `swiped_at`); exactly 2 weeks is kept; read and unread alike
- Swipes, tombstones and feed status are never deleted
- The result counts what was deleted; a dry run counts the same and deletes nothing

**`test_backup.py`**: on-server snapshots (layer 1), written before the code (test-first).
- Snapshot names carry the UTC time; a snapshot holds every row, the schema and the swipe triggers
- Writes still in the WAL file are included (the reason for `VACUUM INTO` over a file copy)
- The check opens the snapshot as a separate database and queries it (version, row counts); later changes to production don't show up
- An existing snapshot is never overwritten; a damaged file fails the check
- Rotation keeps everything from the last 48 hours and midnight snapshots for 14 days, and never touches files it didn't name

### Extraction

**`test_safe_fetch.py`**: the SSRF guard.
- Every non-public address class is blocked (loopback, private, link-local incl. cloud metadata, Tailscale v4/v6, multicast, reserved, IPv4-mapped, NAT64, 6to4); public ones are allowed
- One bad DNS record blocks the connection
- The socket goes to the checked IP (no second lookup); the real guard refuses a local server
- Redirect hops are checked: blocked address, other schemes, loops; exactly 5 allowed, 6 refused
- HTTP errors classified as permanent or temporary
- Non-HTML and compressed responses refused
- Size limit (incl. the exact boundary), read timeout, connect timeout and the overall deadline (slow drip)
- Request headers: User-Agent, `Accept-Encoding: identity`
- Invalid URLs refused; https allowed

**`test_extraction.py`**: the extraction job, with a fake fetch and clock.
- Text stored; the right link fetched
- Temporary failures retry after 5, then 30 minutes, and fail on the 3rd attempt
- Permanent failures and missing links fail at once; pages without article text fail
- Crashes are logged and count as temporary
- Claimed rows are leased: the attempt is counted at claim time, and the row is picked up again after the lease
- Batch size and oldest-save-first order; finished entries are not reclaimed
- Every claimed row is processed and counted
- Text is capped
