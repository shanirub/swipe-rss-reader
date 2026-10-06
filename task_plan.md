# Task Plan: Swipe RSS Reader implementation

Design source of truth: `PROJECT_PLAN.md`. This file tracks execution only; design changes go into the plan first.

## Goal

A working single-user RSS reader: backend on `my-first-server` (Docker Compose, Tailscale Serve) and a sideloaded Android swipe app, implemented in the stages of `PROJECT_PLAN.md` §5.

## Next Step

Phase 3 pruning job built and tested (branch `stage3-retention`, uncommitted): `.md` recheck, commit, PR, merge; then deploy with a dry run first (`docker compose run --rm scheduler swipe-rss prune --dry-run`), with user approval. Server still runs `7531460` (PR #3 changed only tests/docs).

## Current Phase

Phase 3 (in progress: retention designed and built test-first, not yet deployed; Phase 2 complete 2026-10-06, PR #3 merged as `977c094`)

## Phases

### Phase 0: Server foundation (stage 0)

- [x] Repo: `git init`, `.gitignore`, GitHub repo (public), first commit
- [x] Inventory current server state (see findings.md)
- [x] Verify which HTTPS ports Tailscale Serve accepts (443/8443/9443 all work)
- [x] Tailscale Serve: `:8443` → `localhost:8001` (persistent, `--bg`)
- [x] Serve :443 vs nginx: conflict confirmed (nginx bind() fails) → RSS moved to Serve :8443
- [x] Verify nginx starts with Serve on :8443 (user confirmed, nginx stopped again)
- [x] ~~Tailscale ACLs~~ kept allow-all by user decision; SSRF guard must block Tailscale v4+v6 ranges
- [x] Firewall verification: IPv4 22/80/443/8000 filtered; OpenSSH disabled (IPv6 not probeable from desktop)
- [x] Automatic security updates (already enabled)
- [x] Install Docker + Compose
- [x] ~~Deploy key~~ dropped (public repo); server cloned `~/swipe-rss-reader` over HTTPS
- **Status:** complete

### Phase 1: Ingest (stage 1)

- [x] Python project skeleton (uv, ruff, pytest)
- [x] SQLite engine: WAL, busy_timeout, STRICT, `BEGIN IMMEDIATE` recipe
- [x] Alembic baseline + `feed_status` / `items` / `tombstones`
- [x] `feeds.toml` loader (tomllib + Pydantic; invalid → abort run)
- [x] Fetcher: conditional GET → parse → `max_item_age_hours` filter → dedup → store
- [x] Scheduler container (supercronic) + migrate service, `compose.yaml`
- [x] Deploy on server and verify a scheduled fetch run (12:00 UTC run: job succeeded)
- **Status:** complete

### Phase 2: API (stage 2)

- [x] Design review (2026-10-02): item identity, swipe flag, `saved` design, contract approach, deploy rule, swipe-log completeness, queue semantics
- [x] OpenAPI contract `api/openapi.yaml` drafted, reviewed (ingest caps, invalid swipes/dead letters, nested card, API conventions), validated (2026-10-03)
- [x] Generate API models from the spec + freshness test (2026-10-03): `backend/src/swipe_rss/api_models.py`, config in `pyproject.toml`, `tests/test_api_models.py` uses `--check`
- [x] Fetcher ingest caps = spec limits (headline 1000, author 500, tags 50×200, link 4096 → null) + test fetcher limits ≤ model limits; migration `0002` caps existing rows (2026-10-03)
- [x] `swipes` / `saved` migration (`0003`), incl. `items.swiped_at` (2026-10-03)
- [x] FastAPI app skeleton (2026-10-03): `fastapi`/`uvicorn` deps (`trafilatura` deferred to the extraction job); `api.create_app` factory; FastAPI's `/docs`, `/redoc`, `/openapi.json` off; `GET /health`; `.env.example`
- [x] Bearer token auth (2026-10-03): app-level dependency + `PUBLIC_PATHS` allowlist, constant-time compare, fails closed without a ≥32-char token
- [x] Mutation checks as a script (2026-10-03): `backend/scripts/mutants.py`, curated mutants (57 by 2026-10-06), all killed; documented in `backend/tests/README.md`
- [x] mutmut adopted as an exploration tool (2026-10-03): `backend/scripts/run_mutmut.py`, config in `pyproject.toml`; gaps it found closed with tests (empty query params, golden dedup keys, redirects, User-Agent, missing author, updated-only date, truncation whitespace)
- [x] Contract coverage, input hardening, Schemathesis, CI; merged via PR #2 (2026-10-05, `7531460`), server back on `main`
- [x] Triage the remaining mutmut survivors by impact before finishing stage 2 (2026-10-05/06, policy in findings.md): 347 survivors at the start (79%); `models`, `feeds`, `text` accepted; `fetcher` 10 tests; `api`, `config`, `logs`, `timestamps` 7 tests; everything else accepted with a reason per category. Modules triaged 2026-10-04 (`queue`, `swipes`, `saved`, `feed_health`, `safe_fetch`, `extraction`) unchanged since
- [x] `GET /queue` + `POST /swipes` (2026-10-04): `queue.py` (round-robin), `swipes.py` (idempotent batch, item flag, saved on save), per-request transaction; smoke-tested on a copy of the dev DB
- [x] Log every `422` on `POST /swipes` (swipe_ids, errors, body) via an exception handler (2026-10-04)
- [x] Architecture diagrams (2026-10-04): `docs/architecture.md`, Mermaid diagrams (18 by 2026-10-04, incl. read endpoints, extraction job, SSRF guard), validated with Mermaid 10 and 11
- [x] Read endpoints (2026-10-04): `GET /feeds` (`feed_health.py`, 503 on invalid `feeds.toml`), `GET /saved` + `GET /saved/{feed_id}/{item_key}/content` (`saved.py`); path params validated with patterns from the generated `Card`; smoke-tested
- [x] Extraction job + SSRF guard (2026-10-04): `safe_fetch.py` (connect-time IP check, pinned connection, manual redirects, limits), `extraction.py` (claim + lease, backoff 5/30 min, permanent vs temporary), `swipe-rss extract` every minute; `trafilatura` added; tested on real articles (Ars Technica: AWS WAF captcha → always `failed`, see findings)
- [x] `api` Compose service (2026-10-04): uvicorn on `127.0.0.1:8001`, health check, only `api` gets `.env` (optional for Compose); tested locally: no token → only `api` fails; with token → healthy, 401/200, loopback-only bind, scheduler can't see the token
- [x] First stage 2 deploy (2026-10-04): server on `phase2`, `.env` by the user; stack healthy, migrations `0001`→`0003` on a fresh DB (old volume lost in a Docker cleanup, no swipes existed), first fetch 29 feeds / 0 failed / 20 items, `/queue` with token over the tailnet OK
- [x] Dockerfile: `COPY --chmod=a+rX` so the image doesn't inherit the checkout's file modes (deploy broke on files checked out under umask 077); tested with owner-only sources; redeployed 2026-10-04 (`28c1ce8`), files `644` in the image
- [x] Merge `phase2` into `main` via PR (2026-10-04, merge commit; deployed code = `main`). Open stage 2 items continue on a new branch
- [x] Contract coverage (2026-10-04, branch `stage2-contract-tests`): `tests/contract.py` + hooks in `conftest.py` (records every test-client response; after a full green run, recorded set must equal the spec's documented responses), `tests/test_contract.py` (route-set equality, matcher, recorder); 3 curated mutants; all documented responses were already covered
- [x] Spec's global rules (2026-10-04): 404 unknown path, 405 wrong method, 422 unknown query parameter (not `/health`); `api._reject_unknown_query_params` app-level dependency after auth; spec `info.description` + 422 on `/saved`, `/feeds`; recorder accepts wrong-method 405s; 5 curated mutants (one survived first: dependency-declared parameters were untested → test added)
- [x] Must-fail tests for input that never reaches the models (2026-10-05, `test_api_input.py`): malformed JSON, wrong/missing content type, empty body, invalid UTF-8
- [x] Token check as ASGI middleware before routing (1a); 413 above 10 MB (2); logging: `SWIPE_RSS_LOG_LEVEL`, Compose rotation, capped body + errors without repeated input, no health-check access log (3) (2026-10-05)
- [x] Schemathesis (2026-10-05, `test_schemathesis.py`): found 5 bugs, all fixed: lax types, 64-bit overflow → 500, invalid UTF-8 → 500, non-UTF-8 `feeds.toml` → 500, timestamps leaving datetime's range → 500 (next item); plus integral floats wrongly rejected by my first strict-mode fix; regression tests + curated mutants for each
- [x] Timestamp range (2026-10-05, user: option b+ after option a turned out to break the generated models): 1970 ≤ t < 3000 in the spec's descriptions, `timestamps.py`, Pydantic `ge`/`lt` in the API, fetcher stores out-of-range dates as null, Schemathesis `map_body` hook moves generated years into the range (a filter discarded ~9/10 and failed Hypothesis' health check); 5 runs × 500 examples clean
- [x] CI workflow (2026-10-05, `.github/workflows/ci.yml`): jobs backend + docker; first run failed on `setup-uv@v10` (no such tag), fixed (`88dc588`); second run: lint, tests, curated mutants green on GitHub
- [x] Compose smoke job (2026-10-05, user request): `docker compose up --wait api` (migrate + api, no scheduler), checks migrate exit 0, health, 401/200, `/feeds`; same steps verified locally in a git worktree
- [x] Test deploy of the branch (2026-10-05, user OK incl. one-time branch switch by Claude): server on `stage2-contract-tests` at `88dc588`; api healthy, no token → 401 for every path/method/body, rotation + log level active, scheduler without token, DB intact
- [x] Test categories table in `backend/tests/README.md` (timeless examples; spec → code and code → spec as two rows; Schemathesis row)
- [x] Merged via PR #3 (2026-10-06, `977c094`): triage tests + README rewrite
- **Status:** complete

### Phase 3: Retention (stage 3)

- [x] Retention design, part 1 (2026-10-06, user): unswiped items 2 days from `fetched_at`; saved entries 2 weeks from the save swipe's `received_at`, read or unread alike (rule: whatever keeps data longest)
- [x] Swiped items: same 2-day rule from `fetched_at` as unswiped (2026-10-06, user: option B); pruning job built test-first (user)
- [x] Job mechanics (2026-10-06, user approved the bundle): hourly at :07, one transaction, no VACUUM, one INFO line, `--dry-run`, `now` passed in
- [x] Swipe safety, all three layers (2026-10-06, user): trigger in migration `0004` (no DELETE/UPDATE on `swipes`), survival test, curated mutant
- [x] Tombstones kept forever (2026-10-06, user: option B; ~3 MB/year)
- [x] Pruning job, test-first (2026-10-06): tests first (red against a do-nothing stub: 10 failures), then migration `0004` (append-only triggers on `swipes`), `prune.py`, `swipe-rss prune [--dry-run]`, crontab `7 * * * *` (green); 2 curated mutants killed; mutmut on `prune`: 35/35 killed (100%); smoke on a dev-DB copy: dry run 58 counted, real run 58 deleted, tombstones kept
- [ ] Commit, PR, merge; deploy (first prune on the server: dry run first)
- **Status:** in_progress

### Phase 4: Deployment & backups (stage 4)

- [ ] Full Compose setup
- [ ] Record the backend git commit with each stored swipe (`swipes.backend_commit`, nullable; earlier rows NULL). Commit captured at build time; preferred: a `deploy.sh` that computes `git rev-parse HEAD` and passes it as a build arg (alternatives: bare build arg in the deploy command; BuildKit additional context reading `.git`). API logs its commit at startup; local dev stores NULL. Changes the deploy rule to `git pull && ./deploy.sh` (user to confirm then). Parked 2026-10-04.
- [ ] Encrypted rclone backup to Google Drive
- **Status:** pending

### Phase 5: Android MVP (stage 5)

- [ ] kotlinx.serialization sends nulls/defaults (`encodeDefaults = true`), so required-but-nullable fields are never dropped
- [ ] Swipe sync: single-swipe fallback on `422`; dead-letter store (with `app_version` + error), retry once on new app version, debug screen with retry/export
- **Status:** pending

### Phase 6: Read-later view (stage 6)

- [ ] Log opening a saved item as a permanent, append-only event (not only `saved.read_at`)
- **Status:** pending

### Phase 7: Ranking (stage 7)

- **Status:** pending

### Phase 8: Iterate (stage 8)

- [ ] Lenient `POST /dead-letters` + import command, only if dead letters occur
- **Status:** pending

## Key Questions

1. ~~GitHub repo~~ → public monorepo.
2. ~~Server access~~ → Claude via Tailscale SSH (non-root); user runs root commands and reports output.
3. ~~Server state~~ → see findings.md inventory.
4. ~~Commit planning files~~ → yes.
5. ~~nginx on public :80/:443~~ → MCP connector front, intentional exception; Serve on :443 conflicts with it → RSS on Serve :8443.
6. ~~Deploy key~~ → dropped; anonymous HTTPS clone.
7. ~~ACLs~~ → keep allow-all.
8. ~~mekomit (Cloudflare 403 from server IP)~~ → commented out with TODO, like the7eye.
9. ~~Deploy step~~ → Claude runs the exact deploy command + read-only checks over Tailscale SSH, only after the user approves each deploy; everything else on the server stays with the user.
10. ~~OpenAPI spec location~~ → `api/openapi.yaml`, hand-written, contract tests.
11. **Testing-coverage discussion (started 2026-10-04):** (c) + (d) decided and implemented: route-set equality + recording fixture with a full-run check (option "A + B1"; alternatives were explicit `@covers` markers, which can lie, or route equality only). (a)/(b) decided and implemented 2026-10-04: unknown path 404, wrong method 405 (+ `Allow`), unknown query parameter 422 except `/health`, stated as global rules in the spec's `info.description` (OpenAPI can't attach them to operations) and tested; strict query parameters via an app-level dependency (secure by default, like auth; per-route `extra="forbid"` models rejected because new routes would be lenient by default). (i) done 2026-10-05 (malformed input, strict JSON types, size, token before body); (e) Schemathesis and (g) CI adopted 2026-10-05; (h) Android conformance waits for stage 5. Original list: (a) must-fail tests (404 unknown endpoint, 405 wrong method, 401 token, 422 invalid body/params); (b) unknown fields: request bodies are strict (decided, `additionalProperties: false`); unknown query params still open (FastAPI ignores them by default); (c) route-set equality; (d) coverage meta-check: every documented (path, method, status) exercised at least once; (e) Schemathesis property-based + negative testing in stage 2 (4.29.0 declares Python 3.14; confirm in practice); (f) behavior tests from PROJECT_PLAN; (g) optional GitHub Actions CI running ruff + pytest; (h) Android-side conformance (stage 5); (i) with generated models, only per-response shape validation becomes redundant: must-fail tests still verify wiring (route uses the right model), status codes and error-body format, input outside Pydantic (malformed JSON, wrong content type, empty/oversized batch, huge body), and that the generator translated each constraint correctly; (j) mutation testing: curated `scripts/mutants.py` exists (run it after changing guard tests); mutmut experiment (findings.md) ran the whole backend in ~6 s and found real gaps (blank query params in link normalization, no golden dedup-key test, fetcher redirects/User-Agent/missing author) plus noise. → Decided 2026-10-03: mutmut adopted as an exploration tool (`scripts/run_mutmut.py`, not a gate); gaps it found are fixed; remaining survivors to triage before finishing stage 2.
12. ~~Generate Pydantic models?~~ → yes: `datamodel-code-generator`, committed output, freshness test (2026-10-03).

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| **All dev work on the desktop; server only runs committed code** | User's rule (2026-10-02): code is written, tested (`uv run pytest`, `ruff`) and container-tested (`docker compose up --build`, desktop has Docker 29.8 + Compose v5) on the desktop, committed and pushed; the server only does `git pull && docker compose up -d --build`. No editing or debugging on the server. |
| Comment out mekomit with TODO | Cloudflare challenges the server's datacenter IP; evasion is off the table |
| Planning files at repo root (legacy single-plan mode) | One task; named `.planning/` plans only needed for parallel tasks |
| Phases mirror `PROJECT_PLAN.md` stages | Avoid a second, diverging roadmap |
| Keep allow-all tailnet ACL | User controls tailnet membership; SSRF guard becomes sole server→tailnet barrier |
| RSS API on Serve :8443 | Serve on :443 blocks nginx's 0.0.0.0:443 bind |
| `feeds.toml` → `config/feeds.toml`, dir bind mount | Single-file bind mounts go stale after git pull |
| Fetch every 15 min | Conditional GET keeps it cheap; easy to change in `backend/crontab` |
| One-shot `migrate` Compose service | Avoids migration races once the API container exists |
| Python 3.14 | Matches desktop; container uses python:3.14-slim |
| Summaries capped at 2000 chars (since 2026-10-03 all card fields, see card-limits row); entries without a title skipped | Card text, not full articles; untitled entries can't be shown |
| Drop deploy key | Public repo: anonymous HTTPS clone, no secret on server |
| Disable OpenSSH (ssh.socket) | Tailscale SSH used; server has public IPv6 we can't probe; Hetzner console is fallback |
| Item identity `(feed_id, item_key)`; `item_key` = `items.dedup_key`, in queue response and swipe log | Needed for "latest swipe per item wins" and queue removal; rowid can be reused after pruning (decided 2026-10-02, PROJECT_PLAN §3 Swipe recording) |
| Swipe flags the item (`items.swiped_at`), doesn't delete it | Matches stage 3 rules; keeps undo possible (deleted+tombstoned items can't return) |
| `saved`: PK `(feed_id, item_key)`, `swipe_id` → `swipes`, display fields via join; `next_attempt_at` for backoff | One entry per article; no duplicated data; works when the item is already pruned |
| Hand-written `api/openapi.yaml` (3.1) + contract tests (route set, must-fail, status codes) | Spec stays the single source of truth; exact diff vs FastAPI output is brittle; shapes covered by generated models |
| Deploys: Claude runs only the deploy command + read-only checks, per-deploy user approval | Fast feedback without giving Claude broad server authority; docker group is root-equivalent |
| Swipe log adds `fetched_at`, `tz_offset_minutes`, `app_version`; stage 6 logs reads permanently | Completeness check: data only available at swipe time is otherwise lost; impressions not needed in a swipe UI |
| Queue endpoint: `limit`, stateless, phone dedups by `(feed_id, item_key)` | Server can't know what the phone holds (offline, unsynced swipes) |
| `api` Compose service added in Phase 2, not Phase 4 | Stage 2 must be testable with `curl` over the tailnet |
| Generate API Pydantic models from `api/openapi.yaml` (committed, freshness test) | Request/response shapes defined once; drift impossible while fresh; must-fail and route tests still required |
| Card length limits enforced at ingest = spec limits (generous) | Every served card must pass swipe validation; otherwise a swipe is stuck forever. Local data: max headline 108, max 25 tags |
| Batch `POST /swipes` all-or-nothing `422`; phone falls back to single swipes, dead-letters a swipe that fails alone | Strict contract fits generated models; one bad swipe never blocks the queue |
| Never tighten request validation without considering queued swipes | Tightening turns queued swipes into dead letters |
| Dead letters: server logs 422s (stage 2); phone retries on app update + debug retry/export (stage 5); lenient upload endpoint only if needed (stage 8) | Rejections are noticed and never permanently lost, without loosening the strict contract |
| Swipe JSON nests `card` (= queue object); `swipes` table flat | `Card` schema and its limits defined once |
| API conventions: unauthenticated fixed `/health`; wrapped list responses; content endpoint always 200 for saved items; required-but-nullable fields; new request fields optional | Recorded in PROJECT_PLAN §3 Repository |
| Dedup key from the full title/link, card fields capped separately | Identity must not change because of a display limit |
| Data migrations copy their constants instead of importing app code | A migration must keep doing what it did when written |
| Root `README.md` links to `PROJECT_PLAN.md` + `task_plan.md` for status instead of stating it | A hardcoded status line would go stale with every commit |
| `backend/tests/README.md` documents every test file and the test strategy; update it when tests are added | Keeps the test suite understandable; the API contract-test strategy (Key Question 11) gets added there once decided |
| `0003`: CHECK constraints on `swipes.action` and `saved.extraction_status`; FK `saved.swipe_id` → `swipes`; index `(feed_id, item_key)` on `swipes`; `saved.next_attempt_at` NULL = due now | The DB rejects invalid states itself; index serves "latest swipe per item" and queue removal |
| Test: `models.py` matches the migrations (Alembic `compare_metadata`) | Code and schema can't silently drift |
| Recheck all maintained `.md` files before every commit | User rule (2026-10-03): every recheck so far found stale or missing data |
| Auth as app-level dependency with `PUBLIC_PATHS` allowlist; routes registered directly on the app | Secure by default (no route can forget auth); FastAPI 0.142 hides included routers' routes from `app.routes`, which tests must enumerate |
| API fails closed without a ≥32-char `SWIPE_RSS_API_TOKEN` | A missing or weak token must never mean an open API |
| `trafilatura` added with the extraction job, not the skeleton | Vertical slice; no unused dependency in the image |
| Curated mutation checks in `backend/scripts/mutants.py`, each mutant naming the test that must kill it | Rerunnable proof that each guard test can fail; KILLED-OTHER catches tests passing for the wrong reason; STALE catches outdated snippets |
| mutmut as an exploration tool (`scripts/run_mutmut.py`), not a gate; curated `mutants.py` stays the guard list | mutmut finds unknown gaps in seconds but its survivors need triage (noise, equivalent mutants); curated mutants prove specific rules |
| Golden test pins exact dedup keys | Keys are permanent item identity; any change to the key function must be deliberate and come with a migration plan |
| Request transaction opened inside each endpoint (`with Session(engine) as s, s.begin()`), not a `yield` dependency | Commit is guaranteed before the response is sent, independent of FastAPI's dependency-teardown timing |
| Queue skips (and logs) rows that are not valid `Card`s | One bad row must not block every card; ingest caps make it unexpected |
| `feeds.toml` ids ≤ 100 chars | Same rule as ingest caps: everything stored must be a valid `Card` |
| Architecture diagrams as Mermaid in `docs/architecture.md` (rendered by GitHub), part of the mandatory `.md` recheck | Versioned with the code; validated by rendering with Mermaid 10 and 11 |
| `GET /feeds` → 503 with reason on invalid `feeds.toml` (added to the spec first) | The feed-health screen should show a broken config, not a bare 500 |
| Path params validated with patterns read from the generated models | No hand-copied rules that could drift from the spec |
| SSRF check inside the connection (custom httpcore network backend), connect to the checked IP | Closes the DNS-rebinding window of resolve-then-connect; TLS still verifies the hostname |
| `is_global` plus explicit category checks and range list | Python counts multicast as global; defence in depth across Python versions |
| Extraction: attempt counted at claim time + 10-min lease; permanent errors fail at once | Crash-loops use up attempts; overlapping cron runs can't double-process; no hammering of 4xx sites |
| `.env` optional for Compose, given to `api` only | A missing token stops only the API (fails closed), not fetching; least privilege for the secret |
| First stage 2 deploy: server checkout switched to `phase2`; merged to `main` the same day | User choice (2026-10-04); server switches back to `main` after the merge |
| Merge `phase2` before contract tests and survivor triage | Those finish stage 2, they don't gate the merge (no CI); `main` = what's deployed. Merge commit keeps the step-by-step history |
| Dockerfile `COPY --chmod=a+rX` | Containers run as non-root; the image must not depend on the server checkout's umask |
| Phase 2 work on branch `phase2` (merged into `main` 2026-10-04) | User request (2026-10-02) |
| Remaining stage 2 work on branch `stage2-contract-tests` | New branch from `main` after the merge; `phase2` deleted (user OK) |
| Contract coverage by recording real responses (A + B1), checked only after a full green run | Checks what tests actually got, not what they claim; finds undocumented responses too; partial runs can't judge coverage |
| Unknown path 404 / wrong method 405 / unknown query parameter 422 (not `/health`), as global rules in the spec | User + Claude (2026-10-04): strict like request bodies, typos visible; OpenAPI has no per-operation place for non-existent operations; done before any client existed |
| Token check moves to ASGI middleware before routing (2026-10-05, user: option 1a) | FastAPI parsed JSON bodies before dependencies: unauthenticated broken bodies got 422, were read into memory and logged. Now nothing but `/health` is read or routed without the token; consequence: unknown path without token → 401 (no route probing) |
| Request bodies limited to 10 MB → 413 (2026-10-05, user) | Largest legitimate batch ≈ 9 MB (500 × ~18 KB, ASCII); uvicorn has no limit |
| Logging (2026-10-05, user: all four) | `SWIPE_RSS_LOG_LEVEL` (default INFO) for jobs and API; Compose log rotation (json-file 10 MB × 3); rejected-batch body capped in the log (single-swipe resends carry the full data); health checks dropped from the access log (99.8% of API log lines) |
| CI on GitHub Actions on every push (2026-10-05, user) | Public repo, standard runners: ruff, pytest (incl. Schemathesis), curated mutants, Docker build, compose smoke test. mutmut (report, not a gate) only on pull requests + manual trigger (user, 2026-10-05): ~10–15 min on a runner |
| Schemathesis added (2026-10-05, user) | Property-based: generated inputs from the spec find cases no example test covers |
| Strict JSON validation of swipe bodies, integral floats normalized (2026-10-05) | Spec types are JSON Schema; FastAPI's lax mode converted `0` into a timestamp and `"5"` into an integer (found by Schemathesis) |
| Integer maxima in the spec: `app_version` ≤ 2147483647, `time_to_swipe_ms` ≤ 2⁶³−1 (2026-10-05) | 2⁶³ crashed the insert with 500; Android's versionCode is 32-bit, SQLite INTEGER is 64-bit |
| Timestamp range 1970 ≤ t < 3000, in descriptions + `timestamps.py` (option b+) | JSON Schema has no date-range keyword; `pattern` on date-time makes the generated models raise TypeError on every value; Pydantic `ge`/`lt` works in our validation step |
| Schemathesis `map_body` (not `filter_body`) for the documented timestamp range | A filter discarded ~90% of bodies (Hypothesis `filter_too_much`); `2000 + year % 400` keeps leap days valid |
| Compose smoke job in CI without the scheduler | Tests the system start as on the server; the scheduler would fetch real feeds (flaky, external) |
| Test deploy by Claude switching the server checkout to the branch (2026-10-05) | User approved as a one-time exception to the deploy rule |
| Schemathesis registered auth, not a forced header | It must be able to leave the token out on purpose |
| Strict query parameters via app-level dependency, own walk of the dependency tree | Secure by default (new routes strict automatically); FastAPI's flattening helper is internal (`get_flat_dependant` gone in 0.142) |
| PyYAML as an explicit dev dependency; `pythonpath = ["tests"]` for test helpers | Tests declare what they use (was only transitive via the model generator); `conftest.py` imports `tests/contract.py` |
| README conventions (2026-10-05, user request after research) | Tables only for rows with several short attributes, cells about one line; one description per item → bullet list; reference parts (what each test file covers) terse and grouped like the code; short table of contents only in long READMEs (tests README), GitHub's outline covers the rest |
| Retention (2026-10-06, user: whatever keeps data longest): items 2 days from `fetched_at`, swiped or not; saved 2 weeks from the save's `received_at`, read or not; tombstones and swipes forever | One rule per table; undo stays possible until expiry; tombstones ≈ 3 MB/year; server clock for saves |
| Pruning job hourly at :07, one transaction, no VACUUM, `--dry-run`, built test-first (2026-10-06, user) | ~3 rows per run; freed pages are reused; look before the first real delete |
| `swipes` append-only by triggers (migration `0004`) + survival test + curated mutant (2026-10-06, user) | The training data can't be refetched; the trigger also covers code and manual sessions outside our tests. A table rebuild drops triggers, so the tests check them at head |
| Keep mcp-server + nginx installed, currently disabled | User's MCP connector, idle until hardware arrives; RSS API on 127.0.0.1:8001 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| nginx: `bind() to 0.0.0.0:443 failed (98: Address already in use)` | 1 | Serve holds :443 on the Tailscale IP; moved RSS to Serve :8443 |
| mekomit fetch: `403 Forbidden` (Cloudflare challenge) on server only | 1 | Not fixable without evasion; feed commented out |
| `--check` mutation test reported exit 0 on a stale spec (2026-10-03) | 1 | Measurement error: `$?` came from `tail` in a pipe; without the pipe exit 1 |
| Constraint test passed with the action CHECK removed (2026-10-03) | 1 | Test reused an existing `swipe_id` (PK clash); fixed with a distinct id |
| Suite failed after `mutants.py` with no source change (2026-10-03) | 1 | Stale `.pyc` from a same-length mutant restored within one second; script now uses a fresh `PYTHONPYCACHEPREFIX` per test run |
| Guard design: assumed `is_global` excludes multicast (2026-10-04) | 1 | Guard tests showed multicast is "global" in Python; explicit category checks added |
| Guard test made a real network connection when the guard was mutated (2026-10-04) | 1 | The socket call is replaced in that test too; no test may reach the network |
| Test URL assumed invalid caused a real DNS lookup (2026-10-04) | 1 | Replaced with URLs that fail while parsing |
| My `pkill -f` cleanup matched its own shell and killed it (2026-10-04) | 1 | Harmless (background loop stopped anyway); don't `pkill -f` patterns contained in the same command |
| `invalid project name " swipe-rss-apitest"` (2026-10-04) | 1 | zsh doesn't word-split `$P="-p name"`; used `COMPOSE_PROJECT_NAME` |
| Server: parallel image build failed at "exporting to image" after a full Docker cleanup (2026-10-04) | 1 | Actual error not captured; worked around with `docker compose build migrate` then `up -d`; not reproducible on the desktop, warm-cache parallel builds work on the server |
| Server: `migrate` exit 1, `PermissionError: 'pyproject.toml'` (2026-10-04) | 1 | Checkout files were `600` (umask 077 during `git switch`); `COPY` keeps modes and the app user can't read root-owned `600` files. User chmod-ed the checkout; Dockerfile now `COPY --chmod=a+rX` |
| Server: DB volume deleted during Docker cleanup (2026-10-04) | 1 | Not recoverable (snapshot was in the same volume); no swipes existed, items refetched. Backups are stage 4 |
| `ModuleNotFoundError: contract` loading `conftest.py` (2026-10-04) | 1 | pytest 9 doesn't put `tests/` on `sys.path`; `pythonpath = ["tests"]` |
| `ImportError: get_flat_dependant` (FastAPI 0.142, 2026-10-04) | 1 | Internal helper renamed; own walk of the dependency tree with public attributes |
| CI: mutmut step hung 22+ min; locally `INTERNALERROR KeyError` on Schemathesis node ids (2026-10-05) | 1 | mutmut ignores `test_schemathesis.py`; hung run cancelled; mutmut now its own CI job (PRs + manual, 30-min limit) |
| CI: `Unable to resolve action astral-sh/setup-uv@v10` (2026-10-05) | 1 | I assumed a major-only tag like `actions/checkout@v7`; setup-uv publishes only full versions → `v10.2.0` |
| Curated mutant "token check removed" STALE after changing `dependencies=[...]` (2026-10-04) | 1 | Snippet updated; the STALE verdict did its job (a stale mutant checks nothing) |
| Unauthenticated broken body got 422 and was logged (2026-10-05) | 1 | FastAPI parses bodies before dependencies; token check moved to ASGI middleware |
| Logged errors repeated the input per error (2026-10-05) | 1 | Found by the log-cap test; errors logged as type/loc/msg |
| `TestClient` has no `adapters` / `Session.request() got 'app'` / real DNS lookup of `testserver` (Schemathesis, 2026-10-05) | 3 | Schemathesis' requests transport ≠ httpx TestClient; its auth check uses the schema's transport → schema built in a fixture with the app attached (`schemathesis.pytest.from_fixture`) |
| My strict validation rejected `180.0` (2026-10-05) | 1 | Found by Schemathesis; integral floats normalized to ints (JSON Schema semantics) |
| My error responses crashed on non-UTF-8 input bytes (2026-10-05) | 1 | Found by Schemathesis; inputs decoded as text |
| Route-auth test passed with an unprotected endpoint (2026-10-03) | 1 | FastAPI 0.142 `include_router` hides routes from `app.routes`, so the test enumerated nothing; redesigned (app-level auth, routes on the app, test asserts it sees `/health`) |

## Notes

- Update phase status as work progresses: `pending` → `in_progress` → `complete`.
- Re-read `PROJECT_PLAN.md` before each phase; don't re-open settled decisions.
- Dev loop: edit on desktop → `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest` → when guard tests or the code they protect changed: `uv run python scripts/mutants.py` → optional local `docker compose up --build` → **recheck all `.md` files** → commit + push → server `cd ~/swipe-rss-reader && git pull && docker compose up -d --build`.
- **Mandatory before every commit (user rule, 2026-10-03): recheck all maintained `.md` files for stale or missing data**: `PROJECT_PLAN.md`, `task_plan.md`, `progress.md`, `findings.md`, `README.md`, `backend/tests/README.md`, `docs/architecture.md` (diagrams must match the code flow). Read them in full, compare with what changed, fix, then commit. It catches something nearly every time.
- Deploy: Claude runs `cd ~/swipe-rss-reader && git pull && docker compose up -d --build` over Tailscale SSH **only after the user approves that deploy**, then read-only checks (`docker compose ps`, logs, read-only DB queries). Never edit files in the server checkout. Root, Tailscale and system changes go to the user. Note: `srub` is in group `docker` (root-equivalent).
- Server facts: RSS API URL `https://my-first-server.porcupine-celsius.ts.net:8443` → `127.0.0.1:8001` (`api` container, live since 2026-10-04). DB in named volume `swipe-rss-reader_data` at `/data/swipe_rss.db`.
