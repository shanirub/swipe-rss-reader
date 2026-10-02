# Task Plan: Swipe RSS Reader implementation

Design source of truth: `PROJECT_PLAN.md`. This file tracks execution only; design changes go into the plan first.

## Goal

A working single-user RSS reader: backend on `my-first-server` (Docker Compose, Tailscale Serve) and a sideloaded Android swipe app, implemented in the stages of `PROJECT_PLAN.md` §5.

## Next Step

User runs root inventory commands on the server (ufw, ss -p, nginx config) and answers question 5.

## Current Phase

Phase 0

## Phases

### Phase 0: Server foundation (stage 0)

- [x] Repo: `git init`, `.gitignore`, GitHub repo (public), first commit
- [ ] Inventory current server state (non-root part done, see findings.md; root part pending)
- [ ] Verify which HTTPS ports Tailscale Serve accepts (443, 8443, …)
- [ ] Tailscale Serve: `:443` → API loopback port (8000 taken by mcp-server; proposed 8001)
- [ ] Tailscale ACLs: only phone + desktop → `my-first-server`; server cannot initiate to other tailnet devices
- [ ] Firewall verification: no public ports (scan from outside the tailnet)
- [x] Automatic security updates (already enabled)
- [ ] Install Docker + Compose
- [ ] Read-only deploy key for this repo; server clones it
- **Status:** in_progress

### Phase 1: Ingest (stage 1)

- [ ] Python project skeleton (uv, ruff, pytest)
- [ ] SQLite engine: WAL, busy_timeout, STRICT, `BEGIN IMMEDIATE` recipe
- [ ] Alembic baseline + `feed_status` / `items` / `tombstones`
- [ ] `feeds.toml` loader (tomllib + Pydantic; invalid → abort run)
- [ ] Fetcher: conditional GET → parse → `max_item_age_hours` filter → dedup → store
- [ ] Scheduler container (supercronic)
- **Status:** pending

### Phase 2: API (stage 2)

- [ ] OpenAPI contract first
- [ ] `swipes` / `saved` migration
- [ ] Endpoints: queue, `POST /swipes`, saved list, extracted content, feed status
- [ ] Bearer token auth
- [ ] Extraction job + SSRF guard
- **Status:** pending

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
5. What is nginx on public :80/:443 serving, and must it stay public? Conflicts with "no public ports" and possibly with Serve on :443.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Planning files at repo root (legacy single-plan mode) | One task; named `.planning/` plans only needed for parallel tasks |
| Phases mirror `PROJECT_PLAN.md` stages | Avoid a second, diverging roadmap |
| Keep mcp-server running | User's other project; RSS API moves off :8000 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
|       |         |            |

## Notes

- Update phase status as work progresses: `pending` → `in_progress` → `complete`.
- Re-read `PROJECT_PLAN.md` before each phase; don't re-open settled decisions.
