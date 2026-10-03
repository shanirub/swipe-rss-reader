# Backend tests

Run from `backend/`: `uv run pytest`.

## Strategy

- **Real SQLite, real migrations.** Tests that touch the database get a fresh file database in a temp dir, migrated with Alembic to `head` (`conftest.py`: `engine` fixture). The schema under test is exactly what production runs, including WAL mode, STRICT tables and `BEGIN IMMEDIATE`. No database mocks.
- **No network.** Feed fetching uses `httpx.MockTransport`, so HTTP behaviour (200, 304, errors, oversized bodies) is simulated deterministically. Feed bodies are built with the `rss()` helper in `conftest.py`.
- **Test the guarantee, not just the code.** Where a rule protects against a whole class of bugs, the test checks the rule itself: for example, that every ingested item is a valid API `Card`, or that the generated API models match the spec.
- **Mutation checks for guard tests.** A new guard test is verified by deliberately breaking the code it protects (e.g. raising a limit above the spec) and confirming the test fails. This also catches tests that pass for the wrong reason. Results are logged in `progress.md`.
- **Lint is part of the check:** `uv run ruff check . && uv run ruff format --check .`.

Still to be decided before the API's contract tests are written: the strategy for must-fail tests, route coverage and property-based testing (Schemathesis). See Key Question 11 in [`task_plan.md`](../../task_plan.md).

## Test files

| File | What it tests |
|---|---|
| `conftest.py` | Shared fixtures: temp database migrated to `head` (`engine`, `db_path`) and the `rss()` feed builder. |
| `test_db.py` | SQLite settings: WAL, busy_timeout and foreign keys are on; every table is STRICT and STRICT is enforced; every transaction takes the write lock up front (`BEGIN IMMEDIATE`). |
| `test_feeds.py` | `feeds.toml` loading: the repo's real file is valid; invalid files (no feeds, duplicate ids, bad slugs, non-http(s) URLs, typo'd keys in feeds or `[defaults]`, TOML syntax errors) and a missing file raise instead of being read as "no feeds". |
| `test_dedup.py` | Deduplication keys: link normalization, rule order (GUID → normalized link → title+date hash), the `dedup = "link"` override, blank GUIDs. |
| `test_text.py` | HTML → plain text (tags, entities, script/style removal) and truncation, including the exact length boundary. |
| `test_fetcher.py` | The fetch run end to end: age filter and normalization, rerun inserts nothing, tombstones block re-insertion after pruning, first version wins, same article in two feeds kept twice, conditional GET (304), failing/unparseable/oversized feeds are recorded without stopping the run. Card limits: fetcher limits ≤ the API spec's limits, an oversized entry becomes a valid `Card`, and a dropped link still determines the item's key. |
| `test_migrations.py` | Migrations: `models.py` and the migrations describe the same schema (Alembic's comparison; it does not compare CHECK constraints, hence the explicit constraint tests); downgrade to `0002` and upgrade again; `0002` caps items stored before the card limits existed and leaves valid rows untouched; `0003` swipe/saved rows join, and its CHECK and foreign-key constraints reject bad rows. |
| `test_api.py` | API skeleton and auth: `/health` is public and matches the spec; FastAPI's own `/docs`, `/redoc`, `/openapi.json` are off; the app refuses to start without a ≥32-char token; only `/health` is public, every other route answers 401 without the token (the test asserts it actually sees routes); routers included later are protected too; bad credentials (missing, wrong, prefix, wrong scheme, no scheme) get 401 with `WWW-Authenticate: Bearer` and the spec's `Error` body. |
| `test_api_models.py` | `src/swipe_rss/api_models.py` is up to date with `api/openapi.yaml` (runs the generator's `--check`). |
