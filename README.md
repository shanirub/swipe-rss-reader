# Swipe RSS Reader

<p align="center"><img src="docs/logo.png" alt="Swipe RSS Reader logo" width="240"></p>

A personal, single-user RSS reader. A Python backend on a small server fetches feeds on a schedule and serves an API. An Android app shows the headlines as swipeable cards, and each card gets one of three actions: *never*, *save for later* or *read now*. Every swipe is logged as a training label, so a model can later learn what's interesting and rank the queue.

Everything runs over Tailscale, a private WireGuard network; nothing is exposed to the public internet.

## Status and design

- **Design, decisions and stages:** [`PROJECT_PLAN.md`](PROJECT_PLAN.md) is the source of truth. §5 lists the stages.
- **How the code works:** [`docs/architecture.md`](docs/architecture.md) has diagrams of the system, modules, database, request flows and lifecycles.
- **Current progress:** [`task_plan.md`](task_plan.md) shows which stage is in progress and what comes next. [`progress.md`](progress.md) is the session log and [`findings.md`](findings.md) collects research and server facts.

The three tracking files (`task_plan.md`, `progress.md`, `findings.md`) and the way they are kept up to date are not my invention: they come from [planning-with-files](https://github.com/OthmanAdi/planning-with-files), a Claude Code skill for Manus-style "working memory on disk". The design document `PROJECT_PLAN.md` is this project's own.

## Repository structure

```
.
├── PROJECT_PLAN.md          design source of truth
├── task_plan.md             execution tracking (phases, next step, decisions)    ┐ planning-with-files
├── progress.md              session log, test results, errors                   │ (Claude Code skill)
├── findings.md              research notes, server inventory                    ┘
├── .github/workflows/ci.yml  CI jobs, see backend/tests/README.md
├── compose.yaml             Docker Compose: migrate, scheduler, api (127.0.0.1:8001), log rotation, backup mount
├── .env.example             template for .env (API token; optional log level, backup host dir); never committed
├── docs/
│   ├── architecture.md      diagrams: system, modules, schema, flows, lifecycles
│   └── logo.png             project logo (used in this README)
├── api/
│   └── openapi.yaml         API contract (OpenAPI 3.1), single source of truth for backend ↔ app
├── config/
│   └── feeds.toml           feed definitions, mounted read-only into the containers
└── backend/                 Python backend (uv project)
    ├── pyproject.toml       dependencies, ruff, pytest, model generator config
    ├── Dockerfile           image for all backend services
    ├── crontab              supercronic schedule: fetch every 15 min, extract every minute, prune + backup hourly
    ├── alembic/             database migrations (SQLite): 0001 baseline, 0002 cap items, 0003 swipes/saved, 0004 swipes append-only
    ├── src/swipe_rss/
    │   ├── api.py           HTTP API (FastAPI app factory, bearer-token auth, routes)
    │   ├── queue.py         swipe queue: unswiped items, round-robin across feeds
    │   ├── swipes.py        recording swipes: idempotent batch insert, saved entries
    │   ├── saved.py         read-later list and extracted content
    │   ├── feed_health.py   per-feed fetch health for GET /feeds
    │   ├── extraction.py    extraction job: saved articles → text (trafilatura), retries
    │   ├── safe_fetch.py    SSRF guard: fetches untrusted URLs only from public addresses
    │   ├── prune.py         retention job: items 2 days after fetch, saved 2 weeks after the save
    │   ├── backup.py        on-server snapshots (VACUUM INTO) outside the Docker volume, check, rotation
    │   ├── cli.py           `swipe-rss` command: `fetch`, `extract`, `prune`, `backup`
    │   ├── config.py        settings from environment variables
    │   ├── logs.py          logging setup: SWIPE_RSS_LOG_LEVEL, no health checks in the access log
    │   ├── timestamps.py    the API's timestamp range (1970 ≤ t < 3000), shared by API and fetcher
    │   ├── db.py            SQLite engine: WAL, busy_timeout, BEGIN IMMEDIATE
    │   ├── models.py        database tables (SQLAlchemy, STRICT)
    │   ├── api_models.py    API models, GENERATED from api/openapi.yaml (do not edit)
    │   ├── feeds.py         feeds.toml loader and validation
    │   ├── fetcher.py       fetch run: conditional GET → parse → filter → dedup → store
    │   ├── dedup.py         per-feed deduplication keys
    │   └── text.py          HTML → plain text, truncation
    ├── scripts/
    │   ├── mutants.py       curated mutation checks for the guard tests (see backend/tests/README.md)
    │   └── run_mutmut.py    generated mutation testing with mutmut (see backend/tests/README.md)
    └── tests/               pytest suite, see backend/tests/README.md
```

`android/` holds the Android app (stage 5, being set up; see [`android/README.md`](android/README.md)).

## Development

All development happens on the desktop; the server only pulls committed code and runs the containers.

Prerequisites: [uv](https://docs.astral.sh/uv/) (it installs the pinned Python, 3.14) and Docker with Compose.

### Setup

```sh
cp .env.example .env     # then set SWIPE_RSS_API_TOKEN (see the comments in the file)
cd backend && uv sync    # create the virtual environment
```

### Everyday commands

From `backend/`:

```sh
uv run ruff check . && uv run ruff format --check .   # lint
uv run pytest                                         # tests
uv run datamodel-codegen                              # regenerate the API models after editing api/openapi.yaml
```

Run the API alone, or the whole stack:

```sh
SWIPE_RSS_API_TOKEN=... uv run uvicorn swipe_rss.api:create_app --factory --port 8001   # from backend/
docker compose up --build                                                              # from the repo root
```

Mutation testing (`scripts/mutants.py`, `scripts/run_mutmut.py`) and the CI jobs are described in [`backend/tests/README.md`](backend/tests/README.md).

### Deploy

On the server, from the repo root:

```sh
git pull && docker compose up -d --build
```

The `api` service needs `.env` with `SWIPE_RSS_API_TOKEN`; without it only the API refuses to start, the other services keep running.

Backups: the scheduler writes an hourly database snapshot to `/home/srub/swipe-rss-backups` on the server (outside the repo and every Docker volume). Create that directory once, owned by the container user: `sudo install -d -o 10001 -g 10001 /home/srub/swipe-rss-backups`.

Each snapshot is checked right after it's written: it is opened as a separate, read-only database and queried (integrity check, migration version, row count of every table); the log line `backup succeeded: …` shows the counts. The production database is only read, by the backup itself.

Restoring is manual for now. With the stack stopped, so that nothing writes to the database:

```sh
docker compose stop api scheduler
docker compose run --rm --no-deps scheduler sh -c \
  'cp /backups/swipe_rss-20261006T1537Z.db /data/swipe_rss.db && rm -f /data/swipe_rss.db-wal /data/swipe_rss.db-shm'
docker compose up -d        # also runs migrate, in case the snapshot is older than the code
```

Everything written after the snapshot is lost (at most an hour).
