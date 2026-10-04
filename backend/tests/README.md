# Backend tests

Run from `backend/`: `uv run pytest`.

## Strategy

- **Real SQLite, real migrations.** Tests that touch the database get a fresh file database in a temp dir, migrated with Alembic to `head` (`conftest.py`: `engine` fixture). The schema under test is exactly what production runs, including WAL mode, STRICT tables and `BEGIN IMMEDIATE`. No database mocks.
- **No network.** Feed fetching uses `httpx.MockTransport`, so HTTP behaviour (200, 304, errors, oversized bodies) is simulated deterministically. Feed bodies are built with the `rss()` helper in `conftest.py`.
- **Test the guarantee, not just the code.** Where a rule protects against a whole class of bugs, the test checks the rule itself: for example, that every ingested item is a valid API `Card`, or that the generated API models match the spec.
- **Mutation checks for guard tests.** A new guard test is verified by deliberately breaking the code it protects (e.g. raising a limit above the spec) and confirming the test fails. This also catches tests that pass for the wrong reason. See *Mutation checks* below.
- **Lint is part of the check:** `uv run ruff check . && uv run ruff format --check .`.

Still to be decided before the API's contract tests are written: the strategy for must-fail tests, route coverage and property-based testing (Schemathesis). See Key Question 11 in [`task_plan.md`](../../task_plan.md).

## Mutation checks

The regular tests check the code; the mutation checks check the **tests**. `backend/scripts/mutants.py` holds a curated list of "mutants": one deliberate, realistic bug per important rule (a card limit above the spec, a missing database constraint, an endpoint without auth, a stale generated model, ...). For each one it introduces the bug, runs the guard test that should catch it, and restores the file.

```sh
cd backend
uv run python scripts/mutants.py           # all mutants (~1 min)
uv run python scripts/mutants.py auth      # only mutants whose name contains "auth"
uv run python scripts/mutants.py --list    # what exists, without running
```

Every mutant should be `KILLED` by its named test. `SURVIVED` means a guard test is broken or missing; `KILLED-OTHER` means the bug is caught, but by a different test than expected (possibly for the wrong reason); `STALE` means the code changed and the mutant's snippet needs updating. The script's docstring explains the details, including what it does *not* check: it covers only the listed rules, so a clean run is not a completeness or coverage measure.

When to run it: after changing a guard test or the code a mutant targets, and when adding a new guard test (add a mutant for it). It is not part of `pytest`.

### Generated mutants (mutmut)

`backend/scripts/run_mutmut.py` runs [mutmut](https://github.com/boxed/mutmut), which *generates* several hundred mutants across `src/swipe_rss/` (flipped comparisons, negated conditions, changed constants, removed arguments, ...) and reports per module how many the tests killed. Where `mutants.py` proves that specific guards work, mutmut *discovers* gaps nobody thought of; its first run found the blank-query-parameter, dedup-key-stability, redirect, User-Agent, missing-author and updated-date gaps now covered in `test_dedup.py`, `test_fetcher.py` and `test_text.py`.

```sh
cd backend
uv run python scripts/run_mutmut.py                  # whole backend (~7 s)
uv run python scripts/run_mutmut.py fetcher --show   # one module, with the diff of every survivor
```

Surviving mutants need triage: a **real gap** (write a test), **equivalent** (the change can't alter behaviour) or **noise** (e.g. a log message). `no tests` means no test runs that code at all (currently `cli.py`, `config.py`). The score (killed / (killed + survived)) is a trend to watch, not a target; 100% is not achievable. Configuration: `[tool.mutmut]` in `pyproject.toml`; two path-dependent tests are deselected there because mutmut runs the tests from a copy in `backend/mutants/` (git- and docker-ignored). The script's docstring explains all of this in detail. Exploration, not a gate: it exits 0 even with survivors.

## Test files

| File | What it tests |
|---|---|
| `conftest.py` | Shared fixtures: temp database migrated to `head` (`engine`, `db_path`) and the `rss()` feed builder. |
| `test_db.py` | SQLite settings: WAL, busy_timeout and foreign keys are on; every table is STRICT and STRICT is enforced; every transaction takes the write lock up front (`BEGIN IMMEDIATE`). |
| `test_feeds.py` | `feeds.toml` loading: the repo's real file is valid; invalid files (no feeds, duplicate ids, bad slugs, non-http(s) URLs, typo'd keys in feeds or `[defaults]`, TOML syntax errors) and a missing file raise instead of being read as "no feeds". |
| `test_dedup.py` | Deduplication keys: link normalization (incl. empty query parameters kept), rule order (GUID → normalized link → title+date hash), the `dedup = "link"` override, blank GUIDs; **golden keys**: exact key values pinned, because keys are permanent item identity and any change to the key function needs a migration plan. |
| `test_text.py` | HTML → plain text (tags, entities, script/style removal) and truncation, including the exact length boundary and no whitespace before the `…`. |
| `test_fetcher.py` | The fetch run end to end: age filter and normalization, rerun inserts nothing, tombstones block re-insertion after pruning, first version wins, same article in two feeds kept twice, conditional GET (304), failing/unparseable/oversized feeds are recorded without stopping the run, redirects are followed, requests send the reader's User-Agent, a missing/blank author becomes null, an Atom entry with only an updated date gets that date. Card limits: fetcher limits ≤ the API spec's limits, an oversized entry becomes a valid `Card`, and a dropped link still determines the item's key. |
| `test_migrations.py` | Migrations: `models.py` and the migrations describe the same schema (Alembic's comparison; it does not compare CHECK constraints, hence the explicit constraint tests); downgrade to `0002` and upgrade again; `0002` caps items stored before the card limits existed and leaves valid rows untouched; `0003` swipe/saved rows join, and its CHECK and foreign-key constraints reject bad rows. |
| `test_api.py` | API skeleton and auth: `/health` is public and matches the spec; FastAPI's own `/docs`, `/redoc`, `/openapi.json` are off; the app refuses to start without a ≥32-char token; only `/health` is public, every other route answers 401 without the token (the test asserts it actually sees routes); routers included later are protected too; bad credentials (missing, wrong, prefix, wrong scheme, no scheme) get 401 with `WWW-Authenticate: Bearer` and the spec's `Error` body. |
| `test_api_endpoints.py` | `GET /queue` and `POST /swipes` against a real migrated database: round-robin order (oldest first, undated items by fetch time), `limit` bounds, served cards are valid swipe cards, a row that isn't a valid card is skipped instead of failing the queue, feed ids fit the API; swipe round trip (stored flat with every field, item flagged not deleted), idempotent resend, a duplicate doesn't stop the rest of the batch, every new swipe counted, one saved entry per article and only for `save`, swipes for pruned items stored, one invalid swipe rejects the whole batch (nothing stored), malformed/oversized batches, a tampered card rejected, rejected batches logged; a swipe flags only its own item (not other items of the feed, not the same article in another feed), the first `swiped_at` is kept. |
| `test_api_models.py` | `src/swipe_rss/api_models.py` is up to date with `api/openapi.yaml` (runs the generator's `--check`). |
