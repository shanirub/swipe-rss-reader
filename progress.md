# Progress Log

## Session: 2026-10-01

- Design review of `PROJECT_PLAN.md`: all decisions needed for stages 0–2 resolved and recorded in the plan.

## Session: 2026-10-02

### Pre-implementation: feeds

- **Status:** complete
- Actions taken:
  - Converted `tech_privacy_rss_feeds.md` → `feeds.toml` (30 active feeds; main Ars/Wired, wired-ideas, the7eye commented out).
  - Added `max_item_age_hours = 24` to `[defaults]`; documented in `PROJECT_PLAN.md` §3 Feeds.
  - Verified all feeds fetch and parse; measured category overlap.
  - Set up planning files.
- Files created/modified:
  - `feeds.toml`, `PROJECT_PLAN.md`, `task_plan.md`, `findings.md`, `progress.md`

### Phase 0: Server foundation

- **Status:** complete
- Actions taken:
  - Repo initialized; `.gitignore`; public GitHub repo created.
  - Non-root server inventory over Tailscale SSH (see findings.md).
  - Probed public IP from desktop: 22 filtered, 80/443 open (nginx), 8000 filtered.
  - User disabled mcp-server + nginx; re-probe: 22/80/443 all filtered.
  - User installed Docker; verified as srub (version, hello-world, loopback publish on 8001).
  - User enabled HTTPS certs, set srub as tailscale operator, disabled ssh.socket (verified: nothing on :22).
  - Server cloned repo anonymously over HTTPS into ~/swipe-rss-reader.
  - Serve tested on 443/8443/9443 against a temp container; kept only 443 → localhost:8001.
  - nginx failed to start (bind 0.0.0.0:443 in use by tailscaled) → moved Serve to 8443 → localhost:8001; 443 freed.
  - User re-started nginx: starts cleanly alongside Serve :8443; stopped again.
  - ACL: user keeps allow-all; plan updated (SSRF guard blocks Tailscale v4+v6 ranges).
- Files created/modified:
  - `.gitignore`, `findings.md`, `task_plan.md`, `progress.md`

### Phase 1: Ingest

- **Status:** complete
- Actions taken:
  - `backend/` uv project (Python 3.14): config, db (WAL/busy_timeout/BEGIN IMMEDIATE), STRICT models, Alembic baseline 0001, feeds loader, dedup, html→text, async fetcher, `swipe-rss fetch` CLI.
  - 36 tests (pytest). A mutation check (plain BEGIN) makes the lock test fail as expected.
  - Real local run: 30 feeds, 0 failed, 58 new items; second run 8×304, 0 new.
  - Dockerfile (python:3.14-slim, uv, supercronic v0.2.49 pinned by sha256), crontab, compose.yaml (migrate + scheduler).
  - Moved `feeds.toml` → `config/feeds.toml`.
  - Deployed on server: migrate OK, scheduler up; manual fetch 58 new, mekomit 403 (Cloudflare challenge for datacenter IP).
- Files created/modified:
  - `backend/**`, `compose.yaml`, `config/feeds.toml`, `PROJECT_PLAN.md`

### Session wrap-up

- **Status:** complete
- Actions taken:
  - Commented out mekomit in `config/feeds.toml` with a TODO (user decision).
  - Recorded the user's workflow rule: all dev on the desktop, the server only pulls and runs (task_plan.md Decisions + Notes).
  - Confirmed the server checkout has no local changes (`git status` clean) and the desktop has Docker + Compose for local container tests.
  - Updated all three planning files for a fresh session.
- Files created/modified:
  - `config/feeds.toml`, `task_plan.md`, `findings.md`, `progress.md`

## Session: 2026-10-02 (continued)

### Phase 2 design review

- **Status:** complete
- Decisions (recorded in `PROJECT_PLAN.md` + `task_plan.md`):
  1. Item identity `(feed_id, item_key)`, `item_key` = dedup key.
  2. Swipe flags `items.swiped_at` (no delete); `saved` PK `(feed_id, item_key)`, `swipe_id` → `swipes`, `next_attempt_at`.
  3. Hand-written `api/openapi.yaml` (3.1) + contract tests.
  4. Deploys: Claude runs deploy command + read-only checks after per-deploy approval.
  5. Swipe-log completeness: add `fetched_at`, `tz_offset_minutes`, `app_version`; stage 6 logs reads permanently; no impressions.
  6. Queue endpoint: `limit`, stateless, phone dedups by `(feed_id, item_key)`.
  7. `api` Compose service added in Phase 2.
- Created branch `phase2`; committed and pushed the design review.

## Session: 2026-10-03

### Phase 2 design review, part 2 + OpenAPI spec

- **Status:** complete
- Added Key Question 11: testing-coverage discussion (to do before contract tests).
- Checked PyPI: datamodel-code-generator, openapi-core, schemathesis all declare Python 3.14.
- Trial-generated Pydantic v2 models from a sample 3.1 spec (findings.md).
- Decision 8: generate API models from the spec (committed file + freshness test).
- Drafted `api/openapi.yaml` (7 endpoints incl. `/health`); validated with `openapi-spec-validator` (OK); generated models in scratchpad (OK). Found: ingest caps only `summary` (findings.md). Then reviewed with the user point by point (below).
- Spec review point 1 decided: option A, ingest caps = generous spec limits (headline 1000, author 500, tags 50×200, link 4096 → null); spec updated.
- Spec review point 2 decided: option A, all-or-nothing `422` + phone single-swipe fallback + dead-letter store; "never tighten validation" rule added.
- Dead-letter handling recorded: 422 logging (stage 2), retry on app update + debug retry/export (stage 5), lenient upload endpoint only if needed (stage 8).
- Spec review point 3 decided: swipe nests `card`.
- Spec review point 4 decided: API conventions (`/health` without auth, wrapped lists, content always 200, required-but-nullable, new request fields optional, Kotlin `encodeDefaults` note).
- Rechecked all planning files and `PROJECT_PLAN.md` for stale data; committed spec + plan updates (`be77e33`) and pushed `phase2`.
- Files created/modified: `api/openapi.yaml` (new), `PROJECT_PLAN.md`, `task_plan.md`, `findings.md`, `progress.md`

### Phase 2: generate API models

- **Status:** complete
- Added `datamodel-code-generator[ruff]` 0.83.0 as dev dependency; config in `[tool.datamodel-codegen]`; generated `src/swipe_rss/api_models.py`.
- Spec: renamed schema `ValidationError` → `HTTPValidationError`.
- `tests/test_api_models.py`: freshness via `--check`; mutation-checked (stale spec → fails, restored → passes).
- Docker image builds; models import; generator not in image.
- Committed `5c136af` and pushed.
- Files created/modified: `backend/pyproject.toml`, `backend/uv.lock`, `backend/src/swipe_rss/api_models.py` (new), `backend/tests/test_api_models.py` (new), `api/openapi.yaml`, `PROJECT_PLAN.md`, planning files

### Phase 2: fetcher ingest caps

- **Status:** complete
- `text.truncate()` shared helper; fetcher caps headline 1000, author 500 (stripped), tags 50×200; link > 4096 → `null`. Dedup key uses the full title and link.
- Migration `0002_cap_items`: caps rows stored before the limits (constants copied, downgrade no-op). Ran on local dev DB: 58 items unchanged (all within limits).
- Tests: limits ≤ generated model limits, oversized entry → valid `Card`, dropped link still keys the item, `truncate` boundary, migration caps old rows and leaves valid rows untouched. Mutation-checked 4 sabotages (all caught).
- Committed with the plan recheck as `aa2e70d` and pushed.
- Files created/modified: `backend/src/swipe_rss/{fetcher,text}.py`, `backend/alembic/versions/0002_cap_items.py` (new), `backend/tests/{conftest,test_fetcher,test_text}.py`, `backend/tests/test_migrations.py` (new), plan files

### Docs: READMEs

- **Status:** complete
- Root `README.md`: short description, links to `PROJECT_PLAN.md` (design, stages) and `task_plan.md` (live status), repo structure, dev commands.
- `backend/tests/README.md`: test strategy (real SQLite + migrations, no network, guarantee tests, mutation checks) and what each test file covers; contract-test strategy pending (Key Question 11). Test descriptions checked against the test code.
- Files created/modified: `README.md` (new), `backend/tests/README.md` (new), `task_plan.md`, `findings.md`, `progress.md`
- Committed `45c2c61` and pushed.

### Phase 2: migration 0003

- **Status:** complete
- ORM models `Swipe`, `Saved`, `Item.swiped_at`; migration `0003_swipes_saved` (CHECKs, FK, index; `items.swiped_at` via plain `ADD COLUMN`).
- Tests: models match migrations, every table STRICT, downgrade/upgrade round trip, swipe/saved join, constraints reject bad rows.
- Mutation checks: model column without migration, `saved` not STRICT, no FK, no action CHECK, no status CHECK → all caught. The action-CHECK mutation first passed: the test reused `swipe_id` `s1` and failed on the primary key instead; fixed with a distinct id.
- Dev DB upgraded to `0003`.
- Files created/modified: `backend/src/swipe_rss/models.py`, `backend/alembic/versions/0003_swipes_saved.py` (new), `backend/tests/{test_db,test_migrations}.py`, `backend/tests/README.md`, `PROJECT_PLAN.md`, plan files
- Mandatory `.md` recheck before commit (new user rule): fixed `findings.md` (current state, resources), `task_plan.md` (errors table, skeleton item), `PROJECT_PLAN.md` (`next_attempt_at` NULL = due now). Committed `66bb4b8` and pushed.

### Phase 2: FastAPI app skeleton + auth

- **Status:** complete
- Added `fastapi` 0.142.2, `uvicorn` 0.54.0. `swipe_rss/api.py`: `create_app` factory, FastAPI docs/openapi off, `GET /health`, token check as app-level dependency with `PUBLIC_PATHS` allowlist, fails closed without a ≥32-char token. `config.api_token` from `SWIPE_RSS_API_TOKEN`; `.env.example`.
- First design (protected `APIRouter`) had a vacuous route test: FastAPI 0.142 hides included routes from `app.routes`. Found by mutation check; redesigned.
- Tests (`test_api.py`): health public, docs off, refuses weak/missing token, only `/health` public, every other route needs the token (asserts the enumeration sees `/health`), later-included routers protected, bad credentials → 401 with `WWW-Authenticate` and spec `Error` body, correct token passes.
- Mutation checks (6): auth removed, extra public path, routes hidden from enumeration, openapi left on, no min token length, token value not checked → all caught.
- Smoke test on a real uvicorn server: as expected (findings.md).
- Files created/modified: `backend/src/swipe_rss/api.py` (new), `backend/src/swipe_rss/config.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/test_api.py` (new), `.env.example` (new), `README.md`, `backend/tests/README.md`, `PROJECT_PLAN.md`, plan files
- `.md` recheck before commit: fixed misplaced file list (again), `findings.md` current state + resources, `task_plan.md` checklist order, duplicate auth line in `PROJECT_PLAN.md`. Committed `9a586bf` and pushed.

### Testing: mutation checks script + mutmut experiment

- **Status:** complete
- `backend/scripts/mutants.py`: documented script (what, how, what it checks / doesn't, how to read results), 17 curated mutants (db, text, fetcher limits, schema, API auth, spec freshness), each with the test expected to kill it. Baseline check, STALE detection, byte-for-byte restore check.
- Run: 17/17 KILLED by the expected test, 8.4 s. Self-test of verdicts: SURVIVED, STALE, ERROR, KILLED-OTHER all reported correctly.
- Bug found afterwards: the normal suite failed (`test_dropped_link_still_keys_the_item`) with no source change. Cause: stale `.pyc` from a same-length mutant restored within the same second (proved by comparing the pyc header with the source mtime/size). Fixed with a fresh `PYTHONPYCACHEPREFIX` per test run; now 27.6 s, project bytecode untouched, suite green right after a run.
- mutmut 3.8.0 experiment on a scratch copy (findings.md): 637 mutants in 5.9 s; 72% score; found real test gaps plus noise. The user then decided to adopt it (next entry).
- Docs: `backend/tests/README.md` (Mutation checks section), root `README.md` (tree + command).
- Files created/modified: `backend/scripts/mutants.py` (new), `backend/tests/README.md`, `README.md`, plan files

### Testing: close mutmut gaps + mutmut script

- **Status:** complete
- New tests: empty query parameter kept by `normalize_link`; golden dedup keys (all three rules); fetcher follows redirects, sends User-Agent, missing/blank author → None, Atom updated-only date used; `truncate` drops whitespace before `…`.
- `backend/scripts/run_mutmut.py`: documented wrapper (what, how, checks/doesn't, how to triage), module filter, `--show` diffs, `--fresh`; summary table per module with score. `mutmut` 3.8.0 dev dependency; `[tool.mutmut]` config; `backend/mutants/` in `.gitignore`; `mutants`, `scripts` in `.dockerignore`.
- Results: all targeted gap mutants killed; score 72% → 75%; 133 survivors left to triage (Phase 2 checklist). Curated `mutants.py` still 17/17; 72 tests pass; Docker image builds.
- Files created/modified: `backend/scripts/run_mutmut.py` (new), `backend/tests/{test_dedup,test_fetcher,test_text}.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/.dockerignore`, `.gitignore`, `backend/tests/README.md`, `README.md`, plan files
- `.md` recheck before commit: fixed stale status lines in `progress.md`, `findings.md` current state + resources, `PROJECT_PLAN.md` tooling line, `task_plan.md` current phase + dev loop. Committed (with the mutation-checks script) and pushed.

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| feeds.toml loads | `tomllib.load` | valid, unique ids | 30 feeds, unique | pass |
| All feeds fetch/parse | httpx + feedparser | 200, bozo=False | all pass | pass |
| Public ports (IPv4) | TCP connect from desktop | all filtered | 22/80/443/8000 filtered | pass |
| Serve HTTPS from desktop | curl https://<host>:{443,8443,9443} | 200, valid cert | 200 ×3, LE cert valid to 2026-12-31 | pass |
| nginx + Serve coexistence | nginx start with Serve on :8443 | starts | started (user-verified) | pass |
| Backend unit/integration tests | `uv run pytest` | all pass | 36 passed | pass |
| Live fetch run (desktop) | 30 real feeds | no failures | 58 new, 0 failed; rerun 0 new, 8×304 | pass |
| Server deploy + manual fetch | compose up; `swipe-rss fetch` in container | runs, items stored | 58 new; mekomit 403 | partial |
| Scheduled fetch (supercronic) | 12:00 UTC cron tick | job runs | job succeeded; 0 new, 7×304, 1 failed (mekomit) | pass |
| Docker loopback publish | nginx:alpine on 127.0.0.1:8001 | 200 on loopback only | 200, bound 127.0.0.1 | pass |
| OpenAPI spec valid | `openapi-spec-validator api/openapi.yaml` | OK | OK | pass |
| Models generate from spec | `datamodel-codegen` (scratchpad) | clean Pydantic v2 models | clean; RootModel wrappers for shared types | pass |
| Backend tests after model generation | `uv run pytest` | all pass | 37 passed | pass |
| Freshness test mutation check | spec `maxLength` 1000 → 999 | test fails | fails with regenerate hint; passes after revert | pass |
| Docker image | `docker compose build scheduler`; import models | builds, imports, no generator | as expected | pass |
| Ingest caps + migration tests | `uv run pytest` | all pass | 42 passed | pass |
| Ingest-cap mutation checks | limit > spec ×2, long link kept, key from dropped link | tests fail | all 4 caught | pass |
| Migration 0002 on dev DB | `alembic upgrade head`, data hash before/after | no change (all within limits) | unchanged, version 0002 | pass |
| Migration 0003 tests | `uv run pytest` | all pass | 49 passed | pass |
| 0003 mutation checks | 5 schema sabotages | each caught | all caught (after fixing a false-positive test) | pass |
| Migration 0003 on dev DB | `alembic upgrade head` | tables STRICT, items intact | version 0003, all STRICT, 58 items | pass |
| API skeleton tests | `uv run pytest` | all pass | 65 passed (1 Starlette deprecation warning) | pass |
| API mutation checks | 6 sabotages | each caught | all caught (after redesign; first route test was vacuous) | pass |
| Real uvicorn smoke test | `/health`, `/docs`, `/openapi.json`, no token | 200 / 404 / 404 / refuses to start | as expected | pass |
| Mutation checks script | `uv run python scripts/mutants.py` | 17/17 KILLED | 17/17 KILLED by expected test (27.6 s with isolated bytecode) | pass |
| Suite right after a mutant run | `pytest` after `mutants.py` | all pass | first failed (stale pyc), after fix 65 passed | pass |
| Mutation script verdict self-test | crafted SURVIVED/STALE/ERROR/KILLED-OTHER mutants | each reported correctly | all 4 correct | pass |
| mutmut on all `src/` (scratch copy) | `mutmut run` | measure speed and score | 637 mutants, 5.9 s, 72% killed | info |
| Gap tests | `uv run pytest` | all pass | 72 passed | pass |
| mutmut after gap fixes | `scripts/run_mutmut.py --fresh` | targeted gap mutants killed | all killed; 75% score, 6.9 s | pass |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-10-02 | nginx: bind() to 0.0.0.0:443 failed (98: Address already in use) | 1 | Serve holds 443 on the Tailscale IP; moved RSS to Serve :8443 |
| 2026-10-02 | mekomit: 403 Forbidden (Cloudflare challenge) from server | 1 | Datacenter-IP block; feed commented out |
| 2026-10-03 | `--check` mutation test reported exit 0 on a stale spec | 1 | My measurement error: `$?` came from `tail` in a pipe; re-run without the pipe gave exit 1 |
| 2026-10-03 | Constraint test passed with the action CHECK removed | 1 | Test reused an existing `swipe_id`, so the insert failed on the PK; fixed with a distinct id, mutation now caught |
| 2026-10-03 | Route-auth test passed with an unprotected endpoint added | 1 | FastAPI 0.142 hides included routes from `app.routes` → test enumerated nothing; redesigned auth (app-level + allowlist), test now asserts it sees `/health` |
| 2026-10-03 | Suite failed after running `mutants.py`, no source change | 1 | Stale `.pyc` of a same-length mutant (mtime-seconds + size match); fresh `PYTHONPYCACHEPREFIX` per test run |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phases 0–1 complete; Phase 2: design, `api/openapi.yaml`, generated models done on branch `phase2`; fetcher ingest caps, migration `0003`, API skeleton + auth, mutation testing (curated script + mutmut) done; next: endpoints (`/queue`, `/swipes`) |
| Where am I going? | Phase 2 API → 3 retention → 4 deployment & backups → 5–6 Android → 7 ranking → 8 iterate |
| What's the goal? | Single-user swipe RSS reader: backend on `my-first-server`, sideloaded Android app |
| What have I learned? | See findings.md (current state, server inventory, stage 1 research, Phase 2 design review) |
| What have I done? | Server foundation; ingest pipeline deployed and fetching every 15 min; Phase 2 design decisions recorded in `PROJECT_PLAN.md`; API contract written and validated; API models generated (freshness test); fetcher ingest caps + migration `0002`; READMEs; migration `0003` (swipes, saved); FastAPI skeleton + secure-by-default auth; mutation testing (`scripts/mutants.py`, `scripts/run_mutmut.py`) and the test gaps mutmut found |
