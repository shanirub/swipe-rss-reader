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

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-10-02 | nginx: bind() to 0.0.0.0:443 failed (98: Address already in use) | 1 | Serve holds 443 on the Tailscale IP; moved RSS to Serve :8443 |
| 2026-10-02 | mekomit: 403 Forbidden (Cloudflare challenge) from server | 1 | Datacenter-IP block; feed commented out |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phases 0–1 complete; Phase 2 (stage 2, API) not started |
| Where am I going? | Phase 2 API → 3 retention → 4 deployment & backups → 5–6 Android → 7 ranking → 8 iterate |
| What's the goal? | Single-user swipe RSS reader: backend on `my-first-server`, sideloaded Android app |
| What have I learned? | See findings.md (current state, server inventory, stage 1 research) |
| What have I done? | Server foundation; ingest pipeline deployed and fetching every 15 min |
