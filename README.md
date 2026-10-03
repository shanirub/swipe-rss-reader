# Swipe RSS Reader

A personal, single-user RSS reader. A Python backend on a small server fetches feeds on a schedule and serves an API. An Android app shows the headlines as swipeable cards, and each card gets one of three actions: *never*, *save for later* or *read now*. Every swipe is logged as a training label, so a model can later learn what's interesting and rank the queue.

Everything runs over Tailscale, a private WireGuard network; nothing is exposed to the public internet.

## Status and design

- **Design, decisions and stages:** [`PROJECT_PLAN.md`](PROJECT_PLAN.md) is the source of truth. §5 lists the stages.
- **Current progress:** [`task_plan.md`](task_plan.md) shows which stage is in progress and what comes next. [`progress.md`](progress.md) is the session log and [`findings.md`](findings.md) collects research and server facts.

## Repository structure

```
.
├── PROJECT_PLAN.md          design source of truth
├── task_plan.md             execution tracking (phases, next step, decisions)
├── progress.md              session log, test results, errors
├── findings.md              research notes, server inventory
├── compose.yaml             Docker Compose: migrate + scheduler (API service from stage 2)
├── api/
│   └── openapi.yaml         API contract (OpenAPI 3.1), single source of truth for backend ↔ app
├── config/
│   └── feeds.toml           feed definitions, mounted read-only into the containers
└── backend/                 Python backend (uv project)
    ├── pyproject.toml       dependencies, ruff, pytest, model generator config
    ├── Dockerfile           image for all backend services
    ├── crontab              schedule run by supercronic in the scheduler container
    ├── alembic/             database migrations (SQLite): 0001 baseline, 0002 cap items, 0003 swipes/saved
    ├── src/swipe_rss/
    │   ├── cli.py           `swipe-rss` command (e.g. `swipe-rss fetch`)
    │   ├── config.py        settings from environment variables
    │   ├── db.py            SQLite engine: WAL, busy_timeout, BEGIN IMMEDIATE
    │   ├── models.py        database tables (SQLAlchemy, STRICT)
    │   ├── api_models.py    API models, GENERATED from api/openapi.yaml (do not edit)
    │   ├── feeds.py         feeds.toml loader and validation
    │   ├── fetcher.py       fetch run: conditional GET → parse → filter → dedup → store
    │   ├── dedup.py         per-feed deduplication keys
    │   └── text.py          HTML → plain text, truncation
    └── tests/               pytest suite, see backend/tests/README.md
```

An `android/` directory follows in stage 5.

## Development

All development happens on the desktop; the server only pulls committed code and runs the containers.

```sh
cd backend
uv run ruff check . && uv run ruff format --check . && uv run pytest   # lint + tests
uv run datamodel-codegen                                                # regenerate API models after editing api/openapi.yaml
docker compose up --build                                               # run the stack locally (from the repo root)
```

Deploy on the server: `git pull && docker compose up -d --build`.
