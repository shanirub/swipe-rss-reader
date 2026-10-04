# Task Plan: Swipe RSS Reader implementation

Design source of truth: `PROJECT_PLAN.md`. This file tracks execution only; design changes go into the plan first.

## Goal

A working single-user RSS reader: backend on `my-first-server` (Docker Compose, Tailscale Serve) and a sideloaded Android swipe app, implemented in the stages of `PROJECT_PLAN.md` §5.

## Next Step

Redeploy the Dockerfile permission fix (`git pull && docker compose up -d --build`, after user approval). Then: testing-coverage discussion (Key Question 11) → contract + must-fail tests; triage remaining mutmut survivors. Branch `phase2`.

## Current Phase

Phase 2 (in progress: design, spec, generated models, ingest caps, migration `0003`, app skeleton + auth, mutation testing, `/queue` + `/swipes`, architecture diagrams, `/feeds` + `/saved`, extraction job + SSRF guard, `api` Compose service, first stage 2 deploy done; next testing-coverage discussion)

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
- [x] Mutation checks as a script (2026-10-03): `backend/scripts/mutants.py`, curated mutants (35 by 2026-10-04), all killed; documented in `backend/tests/README.md`
- [x] mutmut adopted as an exploration tool (2026-10-03): `backend/scripts/run_mutmut.py`, config in `pyproject.toml`; gaps it found closed with tests (empty query params, golden dedup keys, redirects, User-Agent, missing author, updated-only date, truncation whitespace)
- [ ] Triage the remaining mutmut survivors before finishing stage 2 (2026-10-04: 147 total, 79% score; `queue`/`swipes`/`api`/`saved`/`feed_health`/`safe_fetch`/`extraction` already triaged as noise/equivalent in findings.md; open: `fetcher` 108, `models` 5, `text` 4, `feeds` 1)
- [x] `GET /queue` + `POST /swipes` (2026-10-04): `queue.py` (round-robin), `swipes.py` (idempotent batch, item flag, saved on save), per-request transaction; smoke-tested on a copy of the dev DB
- [x] Log every `422` on `POST /swipes` (swipe_ids, errors, body) via an exception handler (2026-10-04)
- [x] Architecture diagrams (2026-10-04): `docs/architecture.md`, Mermaid diagrams (18 by 2026-10-04, incl. read endpoints, extraction job, SSRF guard), validated with Mermaid 10 and 11
- [x] Read endpoints (2026-10-04): `GET /feeds` (`feed_health.py`, 503 on invalid `feeds.toml`), `GET /saved` + `GET /saved/{feed_id}/{item_key}/content` (`saved.py`); path params validated with patterns from the generated `Card`; smoke-tested
- [x] Extraction job + SSRF guard (2026-10-04): `safe_fetch.py` (connect-time IP check, pinned connection, manual redirects, limits), `extraction.py` (claim + lease, backoff 5/30 min, permanent vs temporary), `swipe-rss extract` every minute; `trafilatura` added; tested on real articles (Ars Technica: AWS WAF captcha → always `failed`, see findings)
- [x] `api` Compose service (2026-10-04): uvicorn on `127.0.0.1:8001`, health check, only `api` gets `.env` (optional for Compose); tested locally: no token → only `api` fails; with token → healthy, 401/200, loopback-only bind, scheduler can't see the token
- [x] First stage 2 deploy (2026-10-04): server on `phase2`, `.env` by the user; stack healthy, migrations `0001`→`0003` on a fresh DB (old volume lost in a Docker cleanup, no swipes existed), first fetch 29 feeds / 0 failed / 20 items, `/queue` with token over the tailnet OK
- [x] Dockerfile: `COPY --chmod=a+rX` so the image doesn't inherit the checkout's file modes (deploy broke on files checked out under umask 077); tested with owner-only sources
- [ ] Contract + must-fail tests (strategy per Key Question 11); document the strategy in `backend/tests/README.md`
- **Status:** in_progress

### Phase 3: Retention (stage 3)

- [ ] Pruning job: unswiped, swiped (`items.swiped_at`), saved, tombstone expiry
- **Status:** pending

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
11. **To discuss (before writing contract tests):** test coverage strategy: (a) must-fail tests (404 unknown endpoint, 405 wrong method, 401 token, 422 invalid body/params); (b) unknown fields: request bodies are strict (decided, `additionalProperties: false`); unknown query params still open (FastAPI ignores them by default); (c) route-set equality; (d) coverage meta-check: every documented (path, method, status) exercised at least once; (e) Schemathesis property-based + negative testing in stage 2 (4.29.0 declares Python 3.14; confirm in practice); (f) behavior tests from PROJECT_PLAN; (g) optional GitHub Actions CI running ruff + pytest; (h) Android-side conformance (stage 5); (i) with generated models, only per-response shape validation becomes redundant: must-fail tests still verify wiring (route uses the right model), status codes and error-body format, input outside Pydantic (malformed JSON, wrong content type, empty/oversized batch, huge body), and that the generator translated each constraint correctly; (j) mutation testing: curated `scripts/mutants.py` exists (run it after changing guard tests); mutmut experiment (findings.md) ran the whole backend in ~6 s and found real gaps (blank query params in link normalization, no golden dedup-key test, fetcher redirects/User-Agent/missing author) plus noise. → Decided 2026-10-03: mutmut adopted as an exploration tool (`scripts/run_mutmut.py`, not a gate); gaps it found are fixed; remaining survivors to triage before finishing stage 2.
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
| First stage 2 deploy: server checkout switched to `phase2` (not merged to `main` yet) | User choice (2026-10-04); merge to `main` and switch back later |
| Dockerfile `COPY --chmod=a+rX` | Containers run as non-root; the image must not depend on the server checkout's umask |
| Phase 2 work on branch `phase2` | User request (2026-10-02) |
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
| Route-auth test passed with an unprotected endpoint (2026-10-03) | 1 | FastAPI 0.142 `include_router` hides routes from `app.routes`, so the test enumerated nothing; redesigned (app-level auth, routes on the app, test asserts it sees `/health`) |

## Notes

- Update phase status as work progresses: `pending` → `in_progress` → `complete`.
- Re-read `PROJECT_PLAN.md` before each phase; don't re-open settled decisions.
- Dev loop: edit on desktop → `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest` → when guard tests or the code they protect changed: `uv run python scripts/mutants.py` → optional local `docker compose up --build` → **recheck all `.md` files** → commit + push → server `cd ~/swipe-rss-reader && git pull && docker compose up -d --build`.
- **Mandatory before every commit (user rule, 2026-10-03): recheck all maintained `.md` files for stale or missing data**: `PROJECT_PLAN.md`, `task_plan.md`, `progress.md`, `findings.md`, `README.md`, `backend/tests/README.md`, `docs/architecture.md` (diagrams must match the code flow). Read them in full, compare with what changed, fix, then commit. It catches something nearly every time.
- Deploy: Claude runs `cd ~/swipe-rss-reader && git pull && docker compose up -d --build` over Tailscale SSH **only after the user approves that deploy**, then read-only checks (`docker compose ps`, logs, read-only DB queries). Never edit files in the server checkout. Root, Tailscale and system changes go to the user. Note: `srub` is in group `docker` (root-equivalent).
- Server facts: RSS API URL `https://my-first-server.porcupine-celsius.ts.net:8443` → `127.0.0.1:8001` (`api` container, live since 2026-10-04). DB in named volume `swipe-rss-reader_data` at `/data/swipe_rss.db`.
