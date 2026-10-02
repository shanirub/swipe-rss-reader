# Task Plan: Swipe RSS Reader implementation

Design source of truth: `PROJECT_PLAN.md`. This file tracks execution only; design changes go into the plan first.

## Goal

A working single-user RSS reader: backend on `my-first-server` (Docker Compose, Tailscale Serve) and a sideloaded Android swipe app, implemented in the stages of `PROJECT_PLAN.md` §5.

## Next Step

Write `api/openapi.yaml` (OpenAPI 3.1) from `PROJECT_PLAN.md` §3 (Swipe log fields, Swipe recording, Content extraction, Queue ordering, API auth) and review it with the user before any endpoint code. Work happens on branch `phase2`.

## Current Phase

Phase 2 (in progress: design review done, contract next)

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
- [ ] OpenAPI contract first (`api/openapi.yaml`)
- [ ] `swipes` / `saved` migration
- [ ] Endpoints: queue, `POST /swipes`, saved list, extracted content, feed status
- [ ] Bearer token auth
- [ ] Extraction job + SSRF guard
- [ ] `api` Compose service on `127.0.0.1:8001`; deploy + `curl` over the tailnet
- [ ] Contract tests (route set + request/response validation against the spec)
- **Status:** in_progress

### Phase 3: Retention (stage 3)

- [ ] Pruning job: unswiped, saved, tombstone expiry
- **Status:** pending

### Phase 4: Deployment & backups (stage 4)

- [ ] Full Compose setup
- [ ] Encrypted rclone backup to Google Drive
- **Status:** pending

### Phase 5: Android MVP (stage 5)

- **Status:** pending

### Phase 6: Read-later view (stage 6)

- **Status:** pending

### Phase 7: Ranking (stage 7)

- **Status:** pending

### Phase 8: Iterate (stage 8)

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
| Summaries capped at 2000 chars; entries without a title skipped | Card text, not full articles; untitled entries can't be shown |
| Drop deploy key | Public repo: anonymous HTTPS clone, no secret on server |
| Disable OpenSSH (ssh.socket) | Tailscale SSH used; server has public IPv6 we can't probe; Hetzner console is fallback |
| Item identity `(feed_id, item_key)`; `item_key` = `items.dedup_key`, in queue response and swipe log | Needed for "latest swipe per item wins" and queue removal; rowid can be reused after pruning (decided 2026-10-02, PROJECT_PLAN §3 Swipe recording) |
| Swipe flags the item (`items.swiped_at`), doesn't delete it | Matches stage 3 rules; keeps undo possible (deleted+tombstoned items can't return) |
| `saved`: PK `(feed_id, item_key)`, `swipe_id` → `swipes`, display fields via join; `next_attempt_at` for backoff | One entry per article; no duplicated data; works when the item is already pruned |
| Hand-written `api/openapi.yaml` (3.1) + contract tests (route set + request/response validation) | Spec stays the single source of truth; exact diff vs FastAPI output is brittle |
| Deploys: Claude runs only the deploy command + read-only checks, per-deploy user approval | Fast feedback without giving Claude broad server authority; docker group is root-equivalent |
| Swipe log adds `fetched_at`, `tz_offset_minutes`, `app_version`; stage 6 logs reads permanently | Completeness check: data only available at swipe time is otherwise lost; impressions not needed in a swipe UI |
| Queue endpoint: `limit`, stateless, phone dedups by `(feed_id, item_key)` | Server can't know what the phone holds (offline, unsynced swipes) |
| `api` Compose service added in Phase 2, not Phase 4 | Stage 2 must be testable with `curl` over the tailnet |
| Phase 2 work on branch `phase2` | User request (2026-10-02) |
| Keep mcp-server + nginx installed, currently disabled | User's MCP connector, idle until hardware arrives; RSS API on 127.0.0.1:8001 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| nginx: `bind() to 0.0.0.0:443 failed (98: Address already in use)` | 1 | Serve holds :443 on the Tailscale IP; moved RSS to Serve :8443 |
| mekomit fetch: `403 Forbidden` (Cloudflare challenge) on server only | 1 | Not fixable without evasion; feed commented out |

## Notes

- Update phase status as work progresses: `pending` → `in_progress` → `complete`.
- Re-read `PROJECT_PLAN.md` before each phase; don't re-open settled decisions.
- Dev loop: edit on desktop → `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest` → optional local `docker compose up --build` → commit + push → server `cd ~/swipe-rss-reader && git pull && docker compose up -d --build`.
- Deploy: Claude runs `cd ~/swipe-rss-reader && git pull && docker compose up -d --build` over Tailscale SSH **only after the user approves that deploy**, then read-only checks (`docker compose ps`, logs, read-only DB queries). Never edit files in the server checkout. Root, Tailscale and system changes go to the user. Note: `srub` is in group `docker` (root-equivalent).
- Server facts: RSS API URL `https://my-first-server.porcupine-celsius.ts.net:8443` → `127.0.0.1:8001` (nothing listening until stage 2). DB in named volume `swipe-rss-reader_data` at `/data/swipe_rss.db`.
