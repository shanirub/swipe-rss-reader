# Task Plan: Swipe RSS Reader implementation

Design source of truth: `PROJECT_PLAN.md`. This file tracks execution only; design changes go into the plan first.

## Goal

A working single-user RSS reader: backend on `my-first-server` (Docker Compose, Tailscale Serve) and a sideloaded Android swipe app, implemented in the stages of `PROJECT_PLAN.md` §5.

## Next Step

Phase 1: create the Python project skeleton (uv, ruff, pytest) under `backend/`.

## Current Phase

Phase 1

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

- [ ] Python project skeleton (uv, ruff, pytest)
- [ ] SQLite engine: WAL, busy_timeout, STRICT, `BEGIN IMMEDIATE` recipe
- [ ] Alembic baseline + `feed_status` / `items` / `tombstones`
- [ ] `feeds.toml` loader (tomllib + Pydantic; invalid → abort run)
- [ ] Fetcher: conditional GET → parse → `max_item_age_hours` filter → dedup → store
- [ ] Scheduler container (supercronic)
- **Status:** in_progress

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
6. ~~Deploy key~~ → dropped; anonymous HTTPS clone.
7. ~~ACLs~~ → keep allow-all.
5. ~~nginx on public :80/:443~~ → MCP connector front, intentional; recorded in PROJECT_PLAN as an accepted exception. Open: does Serve on tailnet :443 coexist with nginx on 0.0.0.0:443?

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Planning files at repo root (legacy single-plan mode) | One task; named `.planning/` plans only needed for parallel tasks |
| Phases mirror `PROJECT_PLAN.md` stages | Avoid a second, diverging roadmap |
| Keep allow-all tailnet ACL | User controls tailnet membership; SSRF guard becomes sole server→tailnet barrier |
| RSS API on Serve :8443 | Serve on :443 blocks nginx's 0.0.0.0:443 bind |
| Drop deploy key | Public repo: anonymous HTTPS clone, no secret on server |
| Disable OpenSSH (ssh.socket) | Tailscale SSH used; server has public IPv6 we can't probe; Hetzner console is fallback |
| Keep mcp-server + nginx installed, currently disabled | User's MCP connector, idle until hardware arrives; RSS API on 127.0.0.1:8001 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
|       |         |            |

## Notes

- Update phase status as work progresses: `pending` → `in_progress` → `complete`.
- Re-read `PROJECT_PLAN.md` before each phase; don't re-open settled decisions.
