"""Hand-picked mutation checks for the backend's guard tests.

WHAT THIS IS
    Mutation testing checks the *tests*, not the code. A "mutant" is a small, deliberate
    bug: one exact snippet of real code replaced by a broken version. For each mutant this
    script edits the file, runs the tests that are supposed to protect that behaviour, and
    restores the file. If the tests fail, the mutant is "killed": the tests really guard
    that rule. If the tests still pass, the mutant "survived": the tests don't cover it,
    or they pass for the wrong reason.

    The mutants are chosen by hand (see MUTANTS below), one per important rule, e.g.
    "the fetcher's limits must not exceed the API spec's" or "every route except /health
    needs the token". This is not a tool like mutmut that generates hundreds of mutants
    automatically; it is a short, curated list that documents which guard tests exist and
    proves that each one can fail.

HOW TO USE
    cd backend
    uv run python scripts/mutants.py               # run all mutants
    uv run python scripts/mutants.py api auth      # only mutants whose name contains "api" or "auth"
    uv run python scripts/mutants.py --list        # list mutants without running anything

    Run it after changing a guard test or the code a mutant targets, and before relying
    on a guard test you have never seen fail. About 1.5 s per mutant (each run compiles
    from a fresh bytecode cache, see SAFETY); about five minutes for all of them.

    Adding a mutant: append a Mutant(...) to MUTANTS with the file, the exact original
    snippet, its broken replacement, the test file to run and the name of the test that
    should fail. Prefer realistic bugs (an off-by-one, a forgotten check, a removed
    constraint) over syntax errors.

WHAT IT CHECKS
    - Baseline: the target tests pass on the unmodified code. If not, it stops: mutation
      results on a failing suite mean nothing.
    - Each mutant: does the named test fail when this exact bug is introduced?
    - That the original snippet still occurs exactly once. If the code changed and the
      snippet no longer matches, the mutant is reported STALE instead of silently testing
      nothing; update the snippet.
    - That every file is restored byte for byte afterwards.

WHAT IT DOES NOT CHECK
    - Code without a listed mutant. A clean run means "these specific guards work", not
      "the test suite is complete". It is not a coverage or mutation-score measurement.
    - Bugs unlike the listed ones: a guard can kill this mutant and still miss a
      different bug in the same code.
    - Timing or concurrency properties, e.g. that the token comparison is constant-time.
    - The Android app, the deployment or anything outside backend/ and api/.

HOW TO READ THE RESULTS
    KILLED        The expected test failed. The guard works. (Good.)
    KILLED-OTHER  Tests failed, but not the expected one. The bug is caught, but maybe
                  for the wrong reason (e.g. a test failing on an unrelated error). Look
                  at which test failed and fix the mutant's expectation or the test.
    SURVIVED      All target tests passed with the bug in place. The guard is missing or
                  broken: fix the test, not the mutant.
    STALE         The original snippet was not found exactly once. Update the mutant.
    ERROR         pytest could not run normally (collection error, crash, no tests).
                  Often the mutant made the code unimportable: choose a subtler mutant.

    Exit code: 0 if every selected mutant was KILLED, 1 otherwise.

SAFETY
    Files are restored in a `finally` block, also on Ctrl-C. Every test run uses its own
    temporary bytecode cache (PYTHONPYCACHEPREFIX), so compiled mutants never end up in the
    project's __pycache__ (a same-length mutant restored within one second would otherwise
    leave stale bytecode that Python still trusts). If the process is killed hard
    (SIGKILL, power loss) a mutated file can remain: check `git status` / `git diff`.
    Don't run two instances at once, and don't edit target files while it runs.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str  # relative to backend/
    original: str  # must occur exactly once in the file
    mutated: str
    tests: str  # pytest target, relative to backend/
    killed_by: str  # name of the test function expected to fail
    why: str  # the rule this mutant breaks


MUTANTS = [
    # --- database settings ---
    Mutant(
        name="db: deferred BEGIN instead of BEGIN IMMEDIATE",
        file="src/swipe_rss/db.py",
        original='conn.exec_driver_sql("BEGIN IMMEDIATE")',
        mutated='conn.exec_driver_sql("BEGIN")',
        tests="tests/test_db.py",
        killed_by="test_transactions_take_write_lock_immediately",
        why="every transaction must take the write lock up front",
    ),
    # --- text ---
    Mutant(
        name="text: truncation one character too long",
        file="src/swipe_rss/text.py",
        original='return text[: max_chars - 1].rstrip() + "…"',
        mutated='return text[:max_chars].rstrip() + "…"',
        tests="tests/test_text.py",
        killed_by="test_truncate_boundary",
        why="truncated text must not exceed max_chars",
    ),
    # --- fetcher: card limits ---
    Mutant(
        name="fetcher: headline limit above the spec",
        file="src/swipe_rss/fetcher.py",
        original="HEADLINE_MAX_CHARS = 1000",
        mutated="HEADLINE_MAX_CHARS = 1001",
        tests="tests/test_fetcher.py",
        killed_by="test_ingest_limits_fit_the_api_card",
        why="every stored item must be a valid API Card",
    ),
    Mutant(
        name="fetcher: tag count above the spec",
        file="src/swipe_rss/fetcher.py",
        original="TAGS_MAX = 50",
        mutated="TAGS_MAX = 60",
        tests="tests/test_fetcher.py",
        killed_by="test_ingest_limits_fit_the_api_card",
        why="every stored item must be a valid API Card",
    ),
    Mutant(
        name="fetcher: oversized link kept",
        file="src/swipe_rss/fetcher.py",
        original="link=link if link and len(link) <= LINK_MAX_CHARS else None,",
        mutated="link=link,",
        tests="tests/test_fetcher.py",
        killed_by="test_oversized_entry_is_capped_to_a_valid_card",
        why="links over the limit are dropped, not stored",
    ),
    Mutant(
        name="fetcher: dedup key from the dropped link",
        file="src/swipe_rss/fetcher.py",
        original="                    link=link,\n                    title=title,",
        mutated="                    link=None,\n                    title=title,",
        tests="tests/test_fetcher.py",
        killed_by="test_dropped_link_still_keys_the_item",
        why="an item's identity must not depend on the card limits",
    ),
    # --- schema / migrations ---
    Mutant(
        name="schema: model column without a migration",
        file="src/swipe_rss/models.py",
        original="    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # stage 6\n",
        mutated="    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # stage 6\n"
        "    note: Mapped[str | None] = mapped_column(Text)\n",
        tests="tests/test_migrations.py",
        killed_by="test_models_match_migrations",
        why="models.py and the migrations must describe the same schema",
    ),
    Mutant(
        name="schema: saved table not STRICT",
        file="alembic/versions/0003_swipes_saved.py",
        original='sa.PrimaryKeyConstraint("feed_id", "item_key"),\n        sqlite_strict=True,',
        mutated='sa.PrimaryKeyConstraint("feed_id", "item_key"),',
        tests="tests/test_db.py",
        killed_by="test_every_table_is_strict",
        why="every table is STRICT",
    ),
    Mutant(
        name="schema: saved without foreign key to swipes",
        file="alembic/versions/0003_swipes_saved.py",
        original='        sa.ForeignKeyConstraint(["swipe_id"], ["swipes.swipe_id"]),\n',
        mutated="",
        tests="tests/test_migrations.py",
        killed_by="test_0003_constraints_reject_bad_rows",
        why="a saved row needs its save swipe",
    ),
    Mutant(
        name="schema: no CHECK on swipes.action",
        file="alembic/versions/0003_swipes_saved.py",
        original="        sa.CheckConstraint(\"action IN ('never', 'save', 'read_now')\", "
        'name="ck_swipes_action"),\n',
        mutated="",
        tests="tests/test_migrations.py",
        killed_by="test_0003_constraints_reject_bad_rows",
        why="the database rejects unknown actions",
    ),
    Mutant(
        name="schema: no CHECK on saved.extraction_status",
        file="alembic/versions/0003_swipes_saved.py",
        original="        sa.CheckConstraint(\"extraction_status IN ('pending', 'done', 'failed')\", "
        'name="ck_saved_extraction_status"),\n',
        mutated="",
        tests="tests/test_migrations.py",
        killed_by="test_0003_constraints_reject_bad_rows",
        why="the database rejects unknown extraction states",
    ),
    # --- API auth ---
    Mutant(
        name="api auth: app-level token check removed",
        file="src/swipe_rss/api.py",
        original="    app.add_middleware(BearerTokenMiddleware, token=settings.api_token)\n",
        mutated="",
        tests="tests/test_api.py",
        killed_by="test_routes_added_later_are_protected_too",
        why="every route except PUBLIC_PATHS needs the token",
    ),
    Mutant(
        name="api auth: extra public path",
        file="src/swipe_rss/api.py",
        original='PUBLIC_PATHS = frozenset({"/health"})',
        mutated='PUBLIC_PATHS = frozenset({"/health", "/later"})',
        tests="tests/test_api.py",
        killed_by="test_only_health_is_public",
        why="only /health is public",
    ),
    Mutant(
        name="api auth: token value not compared",
        file="src/swipe_rss/api.py",
        original='return scheme.lower() == "bearer" and hmac.compare_digest(credentials.encode(), self._expected)',
        mutated='return scheme.lower() == "bearer"',
        tests="tests/test_api.py",
        killed_by="test_bad_credentials_get_401",
        why="a wrong token gets 401",
    ),
    Mutant(
        name="api auth: no minimum token length",
        file="src/swipe_rss/api.py",
        original="if not settings.api_token or len(settings.api_token) < MIN_TOKEN_LENGTH:",
        mutated="if not settings.api_token:",
        tests="tests/test_api.py",
        killed_by="test_app_refuses_to_start_without_a_strong_token",
        why="the API fails closed without a strong token",
    ),
    Mutant(
        name="api: FastAPI's own openapi.json left on",
        file="src/swipe_rss/api.py",
        original="        openapi_url=None,\n",
        mutated="",
        tests="tests/test_api.py",
        killed_by="test_fastapi_generated_docs_are_off",
        why="the hand-written spec is the only spec served",
    ),
    # --- queue and swipes ---
    Mutant(
        name="queue: swiped items stay in the queue",
        file="src/swipe_rss/queue.py",
        original="        .where(Item.swiped_at.is_(None))\n",
        mutated="",
        tests="tests/test_api_endpoints.py",
        killed_by="test_swipe_round_trip",
        why="a swiped item leaves the queue",
    ),
    Mutant(
        name="queue: oldest first, no round-robin",
        file="src/swipe_rss/queue.py",
        original=".order_by(ranked.c.rank, ranked.c.age, Item.id)",
        mutated=".order_by(ranked.c.age, Item.id)",
        tests="tests/test_api_endpoints.py",
        killed_by="test_queue_is_round_robin_oldest_first",
        why="the queue is round-robin across feeds",
    ),
    Mutant(
        name="queue: no upper bound on limit",
        file="src/swipe_rss/api.py",
        original="Query(ge=1, le=200)",
        mutated="Query(ge=1)",
        tests="tests/test_api_endpoints.py",
        killed_by="test_queue_rejects_bad_limit",
        why="limit is 1..200 as in the spec",
    ),
    Mutant(
        name="swipes: resend is not idempotent",
        file="src/swipe_rss/swipes.py",
        original='            .on_conflict_do_nothing(index_elements=["swipe_id"])\n',
        mutated="",
        tests="tests/test_api_endpoints.py",
        killed_by="test_resent_batch_is_idempotent",
        why="resending a batch is always safe",
    ),
    Mutant(
        name="swipes: every action creates a saved entry",
        file="src/swipe_rss/swipes.py",
        original="        if swipe.action == Action.save:",
        mutated="        if True:",
        tests="tests/test_api_endpoints.py",
        killed_by="test_only_save_creates_a_saved_entry",
        why="only a save swipe creates a saved entry",
    ),
    Mutant(
        name="swipes: rejected batches not logged",
        file="src/swipe_rss/api.py",
        original='        if request.url.path == "/swipes":',
        mutated='        if request.url.path == "/nothing":',
        tests="tests/test_api_endpoints.py",
        killed_by="test_rejected_batches_are_logged",
        why="a rejected batch (future dead letter) is visible in the server log",
    ),
    Mutant(
        name="feeds: feed id longer than the API allows",
        file="src/swipe_rss/feeds.py",
        original="max_length=100)  # = FeedId",
        mutated="max_length=101)  # = FeedId",
        tests="tests/test_api_endpoints.py",
        killed_by="test_feed_ids_fit_the_api",
        why="feed ids from feeds.toml must be valid in every card",
    ),
    # --- read endpoints ---
    Mutant(
        name="saved: oldest save first",
        file="src/swipe_rss/saved.py",
        original=".order_by(Swipe.swiped_at.desc())",
        mutated=".order_by(Swipe.swiped_at)",
        tests="tests/test_api_endpoints.py",
        killed_by="test_saved_list_shows_the_save_swipes_card_newest_first",
        why="the read-later list shows the newest save first",
    ),
    Mutant(
        name="saved: text served before extraction is done",
        file="src/swipe_rss/saved.py",
        original='text=saved.text if saved.extraction_status == "done" else None,',
        mutated="text=saved.text,",
        tests="tests/test_api_endpoints.py",
        killed_by="test_content_failed_has_error_and_no_text",
        why="text is only served for a finished extraction",
    ),
    Mutant(
        name="saved: item_key path not validated",
        file="src/swipe_rss/api.py",
        original='ItemKeyPath = Annotated[str, Path(pattern=_constraint("item_key", "pattern"))]',
        mutated="ItemKeyPath = Annotated[str, Path()]",
        tests="tests/test_api_endpoints.py",
        killed_by="test_content_rejects_malformed_path",
        why="path parameters follow the spec's patterns",
    ),
    Mutant(
        name="feeds: invalid feeds.toml is a 500",
        file="src/swipe_rss/api.py",
        original="status_code=status.HTTP_503_SERVICE_UNAVAILABLE",
        mutated="status_code=status.HTTP_500_INTERNAL_SERVER_ERROR",
        tests="tests/test_api_endpoints.py",
        killed_by="test_invalid_feeds_file_is_503",
        why="a broken feeds.toml is reported as 503 with a reason",
    ),
    # --- SSRF guard and extraction job ---
    Mutant(
        name="ssrf: only the first resolved address checked",
        file="src/swipe_rss/safe_fetch.py",
        original="        for address in addresses:  # every address",
        mutated="        for address in addresses[:1]:  # every address",
        tests="tests/test_safe_fetch.py",
        killed_by="test_any_bad_address_blocks_the_connection",
        why="a mix of public and private DNS records is refused",
    ),
    Mutant(
        name="ssrf: connects to the hostname (second DNS lookup)",
        file="src/swipe_rss/safe_fetch.py",
        original="return super().connect_tcp(addresses[0], port,",
        mutated="return super().connect_tcp(host, port,",
        tests="tests/test_safe_fetch.py",
        killed_by="test_connection_goes_to_the_checked_address",
        why="the socket goes to the checked IP (no DNS rebinding window)",
    ),
    Mutant(
        name="ssrf: is_global trusted alone",
        file="src/swipe_rss/safe_fetch.py",
        original="        or ip.is_multicast\n",
        mutated="",
        tests="tests/test_safe_fetch.py",
        killed_by="test_non_public_addresses_are_blocked",
        why="multicast counts as global in Python; it must be blocked explicitly",
    ),
    Mutant(
        name="extraction: attempt not counted at claim time",
        file="src/swipe_rss/extraction.py",
        original="            saved.attempts += 1\n",
        mutated="",
        tests="tests/test_extraction.py",
        killed_by="test_claimed_rows_are_leased",
        why="a URL that crashes the job still uses up its attempts",
    ),
    Mutant(
        name="extraction: no lease on claimed rows",
        file="src/swipe_rss/extraction.py",
        original="            saved.next_attempt_at = now + LEASE\n",
        mutated="",
        tests="tests/test_extraction.py",
        killed_by="test_claimed_rows_are_leased",
        why="overlapping runs don't process the same row",
    ),
    Mutant(
        name="extraction: permanent failures retried",
        file="src/swipe_rss/extraction.py",
        original="give_up = permanent or job.attempts >= MAX_ATTEMPTS",
        mutated="give_up = job.attempts >= MAX_ATTEMPTS",
        tests="tests/test_extraction.py",
        killed_by="test_permanent_failure_gives_up_at_once",
        why="retrying can't fix e.g. a 404 or a blocked address",
    ),
    Mutant(
        name="extraction: no backoff between retries",
        file="src/swipe_rss/extraction.py",
        original="saved.next_attempt_at = now + BACKOFF[job.attempts - 1]",
        mutated="saved.next_attempt_at = now",
        tests="tests/test_extraction.py",
        killed_by="test_temporary_failures_retry_with_backoff_then_fail",
        why="retries wait 5, then 30 minutes",
    ),
    # --- spec / generated models ---
    Mutant(
        name="api models: spec edited without regenerating",
        file="../api/openapi.yaml",
        original="          minLength: 1\n          maxLength: 1000",
        mutated="          minLength: 1\n          maxLength: 999",
        tests="tests/test_api_models.py",
        killed_by="test_generated_models_are_fresh",
        why="the generated models must match the spec",
    ),
    # --- contract coverage (tests/contract.py) ---
    Mutant(
        name="contract: endpoint in the app but not in the spec",
        file="src/swipe_rss/api.py",
        original='    @app.get("/health", response_model=Health)\n',
        mutated='    @app.get("/health", response_model=Health)\n    @app.get("/status", response_model=Health)\n',
        tests="tests/test_contract.py",
        killed_by="test_app_routes_equal_the_spec",
        why="the app's routes must equal the spec's operations",
    ),
    Mutant(
        name="contract: undocumented responses not reported",
        file="tests/contract.py",
        original="undocumented = sorted(self.seen - self.documented)",
        mutated="undocumented = sorted(set())",
        tests="tests/test_contract.py",
        killed_by="test_recorder_reports_missing_and_undocumented_responses",
        why="a response the spec doesn't document must fail the run",
    ),
    Mutant(
        name="contract: path parameter matches across slashes",
        file="tests/contract.py",
        original='"[^/]+".join',
        mutated='".+".join',
        tests="tests/test_contract.py",
        killed_by="test_url_paths_map_to_spec_templates",
        why="requests must be attributed to the right spec path",
    ),
    Mutant(
        name="contract: any response to a wrong method accepted",
        file="tests/contract.py",
        original="not in self._operations and status in GLOBAL_RULE_STATUSES:",
        mutated="not in self._operations:",
        tests="tests/test_contract.py",
        killed_by="test_recorder_reports_missing_and_undocumented_responses",
        why="only 405 is the documented answer to a method the spec doesn't list",
    ),
    # --- API conventions: unknown query parameters ---
    Mutant(
        name="api: unknown query parameters ignored",
        file="src/swipe_rss/api.py",
        original="dependencies=[Depends(_reject_unknown_query_params)],",
        mutated="dependencies=[],",
        tests="tests/test_contract.py",
        killed_by="test_unknown_query_parameter_is_422",
        why="a typo like ?limt=5 must be a 422, not silently the default",
    ),
    # --- API: token before body, size limit, logging ---
    Mutant(
        name="api: size limit before the token check",
        file="src/swipe_rss/api.py",
        original=(
            "    app.add_middleware(BodySizeLimitMiddleware, limit=MAX_BODY_BYTES)\n"
            "    app.add_middleware(BearerTokenMiddleware, token=settings.api_token)\n"
        ),
        mutated=(
            "    app.add_middleware(BearerTokenMiddleware, token=settings.api_token)\n"
            "    app.add_middleware(BodySizeLimitMiddleware, limit=MAX_BODY_BYTES)\n"
        ),
        tests="tests/test_api_input.py",
        killed_by="test_without_token_the_body_is_not_read",
        why="without the token, not a single body byte is read",
    ),
    Mutant(
        name="api: bearer scheme case-sensitive",
        file="src/swipe_rss/api.py",
        original='return scheme.lower() == "bearer" and',
        mutated='return scheme == "Bearer" and',
        tests="tests/test_api.py",
        killed_by="test_right_token_passes",
        why="the auth scheme is case-insensitive (RFC 7235)",
    ),
    Mutant(
        name="api: body limit only checks Content-Length",
        file="src/swipe_rss/api.py",
        original="            if size > self.limit:",
        mutated="            if False:",
        tests="tests/test_api_input.py",
        killed_by="test_chunked_body_over_the_limit_is_413",
        why="chunked bodies have no Content-Length; the limit must hold while streaming",
    ),
    Mutant(
        name="api: body limit off by one",
        file="src/swipe_rss/api.py",
        original="int(declared) > self.limit",
        mutated="int(declared) >= self.limit",
        tests="tests/test_api_input.py",
        killed_by="test_body_of_exactly_the_limit_passes_the_size_check",
        why="a body of exactly the limit is allowed",
    ),
    Mutant(
        name="api: logged body of a rejected batch not capped",
        file="src/swipe_rss/api.py",
        original="            if len(body_text) > LOG_BODY_MAX_CHARS:",
        mutated="            if False:",
        tests="tests/test_api_input.py",
        killed_by="test_logged_body_of_a_rejected_batch_is_capped",
        why="one request must not be able to write megabytes into the log",
    ),
    Mutant(
        name="api: logged errors repeat the input",
        file="src/swipe_rss/api.py",
        original='errors = [{key: e[key] for key in ("type", "loc", "msg") if key in e} for e in exc.errors()]',
        mutated="errors = exc.errors()",
        tests="tests/test_api_input.py",
        killed_by="test_logged_body_of_a_rejected_batch_is_capped",
        why="Pydantic repeats the input in every error, which would multiply the log line",
    ),
    # --- strict JSON types (findings of Schemathesis) ---
    Mutant(
        name="api: swipe body validated in lax mode",
        file="src/swipe_rss/api.py",
        original="SwipeBatch.model_validate_json(source, strict=True)",
        mutated="SwipeBatch.model_validate_json(source)",
        tests="tests/test_api_input.py",
        killed_by="test_values_of_the_wrong_json_type_are_422",
        why='0 is not a timestamp and "180" is not an integer in the spec\'s JSON types',
    ),
    Mutant(
        name="api: integral floats not normalized",
        file="src/swipe_rss/api.py",
        original="parsed = json.loads(body, parse_float=_integral_float_as_int)",
        mutated="parsed = json.loads(body)",
        tests="tests/test_api_input.py",
        killed_by="test_integral_floats_count_as_integers",
        why="JSON Schema counts 180.0 as an integer",
    ),
    Mutant(
        name="api: validation error input left as bytes",
        file="src/swipe_rss/api.py",
        original='"input": _text(error["input"])',
        mutated='"input": error["input"]',
        tests="tests/test_api_input.py",
        killed_by="test_invalid_utf8_body_is_422_not_500",
        why="the error response must not crash on input that isn't UTF-8",
    ),
    Mutant(
        name="api: timestamp range not checked",
        file="src/swipe_rss/api.py",
        original="    if errors := _timestamp_range_errors(batch):",
        mutated="    if errors := []:",
        tests="tests/test_api_input.py",
        killed_by="test_timestamps_outside_the_range_are_422_not_500",
        why="a timestamp that leaves datetime's range in UTC crashed the insert (500)",
    ),
    Mutant(
        name="fetcher: feed date outside the API range kept",
        file="src/swipe_rss/fetcher.py",
        original="    return value if in_range(value) else None",
        mutated="    return value",
        tests="tests/test_fetcher.py",
        killed_by="test_date_outside_the_api_range_becomes_null",
        why="a stored date outside the range would make the card unswipeable",
    ),
    Mutant(
        name="feeds: file in another encoding crashes",
        file="src/swipe_rss/feeds.py",
        original="except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as e:",
        mutated="except (OSError, tomllib.TOMLDecodeError, ValidationError) as e:",
        tests="tests/test_feeds.py",
        killed_by="test_file_that_is_not_utf8_is_invalid",
        why="a wrongly encoded feeds.toml is invalid (503 / fetch aborted), not a crash",
    ),
    Mutant(
        name="logs: health checks kept in the access log",
        file="src/swipe_rss/logs.py",
        original='return not (len(args) >= 3 and str(args[2]).split("?")[0] == "/health")',
        mutated="return True",
        tests="tests/test_logs.py",
        killed_by="test_health_checks_are_dropped_from_the_access_log",
        why="health checks were 99.8% of the API's log",
    ),
    Mutant(
        name="api: /health strict about query parameters",
        file="src/swipe_rss/api.py",
        original='LENIENT_QUERY_PATHS = frozenset({"/health"})',
        mutated="LENIENT_QUERY_PATHS = frozenset()",
        tests="tests/test_contract.py",
        killed_by="test_health_ignores_unknown_query_parameters",
        why="a liveness probe must not fail over an extra parameter",
    ),
    Mutant(
        name="api: query parameters of dependencies not counted as declared",
        file="src/swipe_rss/api.py",
        original="        stack.extend(current.dependencies)\n",
        mutated="",
        tests="tests/test_contract.py",
        killed_by="test_query_parameter_declared_in_a_dependency_is_accepted",
        why="a parameter declared anywhere in the route's dependencies is allowed",
    ),
    # --- retention ---
    Mutant(
        name="prune: deletes old swipes too",
        file="src/swipe_rss/prune.py",
        original="        saved = session.execute(delete(Saved).where(expired_save)).rowcount\n",
        mutated="        saved = session.execute(delete(Saved).where(expired_save)).rowcount\n"
        "        session.execute(delete(Swipe).where(Swipe.received_at < now - SAVED_MAX_AGE))\n",
        tests="tests/test_prune.py",
        killed_by="test_swipes_tombstones_and_feed_status_are_never_deleted",
        why="the swipe log is permanent training data",
    ),
    Mutant(
        name="migration 0004: swipes not protected by triggers",
        file="alembic/versions/0004_swipes_append_only.py",
        original='    for event in ("DELETE", "UPDATE"):\n',
        mutated="    for event in ():\n",
        tests="tests/test_migrations.py",
        killed_by="test_0004_swipes_can_never_be_deleted_or_changed",
        why="no code path, not even a manual sqlite3 session, can delete or change a swipe",
    ),
    # --- backups ---
    Mutant(
        name="backup: plain file copy instead of VACUUM INTO",
        file="src/swipe_rss/backup.py",
        original='        conn.execute("VACUUM INTO ?", (str(target),))\n',
        mutated='        __import__("shutil").copyfile(db_path, target)\n',
        tests="tests/test_backup.py",
        killed_by="test_backup_includes_writes_still_in_the_wal_file",
        why="in WAL mode a plain copy misses recent writes; the snapshot must be consistent",
    ),
    Mutant(
        name="backup: rotation deletes files it didn't name",
        file="src/swipe_rss/backup.py",
        original="        if not match:\n            continue  # not ours: never touched\n",
        mutated="        if not match:\n            path.unlink()\n            continue\n",
        tests="tests/test_backup.py",
        killed_by="test_rotation_never_touches_files_it_did_not_name",
        why="rotation must never delete a manual copy or any file it didn't create",
    ),
]

_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) \S+::(\w+)", re.MULTILINE)


def _run_tests(target: str) -> tuple[int, set[str]]:
    # Fresh bytecode cache per run. Python trusts a cached .pyc if the source's mtime (whole seconds) and
    # size match; a same-length mutant written and restored within one second would otherwise leave
    # mutated bytecode behind (or run against the original bytecode and falsely "survive").
    with tempfile.TemporaryDirectory(prefix="mutants-pycache-") as pycache:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q", "-rfE", "-p", "no:cacheprovider", "-W", "ignore"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPYCACHEPREFIX": pycache},
        )
    return result.returncode, set(_FAILED_LINE.findall(result.stdout))


def _verdict(mutant: Mutant) -> tuple[str, str]:
    path = BACKEND_DIR / mutant.file
    original_text = path.read_text()
    if original_text.count(mutant.original) != 1:
        return "STALE", f"original snippet found {original_text.count(mutant.original)}x in {mutant.file}"
    try:
        path.write_text(original_text.replace(mutant.original, mutant.mutated))
        code, failed = _run_tests(mutant.tests)
    finally:
        path.write_text(original_text)
    if path.read_text() != original_text:
        raise SystemExit(f"FATAL: {mutant.file} was not restored; check `git diff`")

    if code == 0:
        return "SURVIVED", "all target tests passed"
    if code != 1:  # 2 interrupted/collection error, 3 internal, 4 usage, 5 no tests collected
        return "ERROR", f"pytest exit code {code}"
    if mutant.killed_by in failed:
        return "KILLED", f"by {mutant.killed_by}"
    return "KILLED-OTHER", f"expected {mutant.killed_by}, failed: {', '.join(sorted(failed)) or '?'}"


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for m in MUTANTS:
            print(f"{m.name}\n    {m.file} -> {m.tests}::{m.killed_by}\n    rule: {m.why}")
        return 0

    selected = [m for m in MUTANTS if not argv or any(a.lower() in m.name.lower() for a in argv)]
    if not selected:
        print("no mutant matches", argv)
        return 1

    for target in sorted({m.tests for m in selected}):
        code, failed = _run_tests(target)
        if code != 0:
            print(f"baseline failed: {target} (exit {code}; {', '.join(sorted(failed))}). Fix the tests first.")
            return 1

    started = time.monotonic()
    results = []
    for m in selected:
        t0 = time.monotonic()
        verdict, detail = _verdict(m)
        results.append(verdict)
        print(f"{verdict:<13} {m.name}  ({time.monotonic() - t0:.1f}s)  {detail}")

    killed = results.count("KILLED")
    print(f"\n{killed}/{len(results)} killed in {time.monotonic() - started:.1f}s")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
