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
    from a fresh bytecode cache, see SAFETY); about a minute for all of them.

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
        original="        dependencies=[Depends(_require_token(settings.api_token))],\n",
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
        original="if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected_bytes):",
        mutated="if credentials is None:",
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
