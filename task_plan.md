# Task Plan: Swipe RSS Reader implementation

Design source of truth: `PROJECT_PLAN.md`. This file tracks execution only; design changes go into the plan first.

## Goal

A working single-user RSS reader: backend on `my-first-server` (Docker Compose, Tailscale Serve) and a sideloaded Android swipe app, implemented in the stages of `PROJECT_PLAN.md` §5.

## Next Step

Start Phase 2 (stage 2, API): read `PROJECT_PLAN.md` §3 (Swipe log fields, Swipe recording, Content extraction, API auth) and §5 stage 2, then write the OpenAPI contract before any endpoint code.

## Current Phase

Phase 2 (not started)

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
5. ~~nginx on public :80/:443~~ → MCP connector front, intentional exception; Serve on :443 conflicts with it → RSS on Serve :8443.
6. ~~Deploy key~~ → dropped; anonymous HTTPS clone.
7. ~~ACLs~~ → keep allow-all.
8. ~~mekomit (Cloudflare 403 from server IP)~~ → commented out with TODO, like the7eye.
9. Deploy step (`git pull && docker compose up -d --build` on the server): does Claude keep running it over SSH, or does the user run it? (Asked 2026-10-02, unanswered.)
10. Stage 2: where does the OpenAPI spec live in the monorepo (e.g. `api/openapi.yaml`)? Decide at the start of Phase 2.

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
- Inspect server state read-only (logs, `docker compose exec -T scheduler …` queries); never edit files in the server checkout. Root commands go to the user.
- Server facts: RSS API URL `https://my-first-server.porcupine-celsius.ts.net:8443` → `127.0.0.1:8001` (nothing listening until stage 2). DB in named volume `swipe-rss-reader_data` at `/data/swipe_rss.db`.
