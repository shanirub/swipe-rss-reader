"""Generated mutation testing with mutmut: find code that no test really checks.

WHAT THIS IS
    Mutation testing checks the *tests*: it introduces small bugs ("mutants") into the code and
    reports which ones the test suite fails to notice. This script runs mutmut, a mutation-testing
    tool that *generates* the mutants automatically: for every function in src/swipe_rss/ it flips
    comparisons (< to <=), negates conditions (in to not in), swaps and/or, changes constants and
    strings, removes arguments, and so on. That is several hundred mutants for this backend.

    It complements scripts/mutants.py, which holds a short hand-picked list of mutants, one per
    important rule. Use mutants.py to prove that specific guard tests work; use this script to
    *discover* gaps nobody thought of. When it finds a real gap, write a test for it (and, if the
    rule is important, add a curated mutant for it to mutants.py).

HOW TO USE
    cd backend
    uv run python scripts/run_mutmut.py                  # all modules
    uv run python scripts/run_mutmut.py fetcher dedup    # only these modules (swipe_rss.<name>)
    uv run python scripts/run_mutmut.py --show           # also print the diff of every survivor
    uv run python scripts/run_mutmut.py --fresh          # discard mutmut's cached results first

    It takes seconds (about 6 s for the whole backend on a 28-core desktop): mutmut 3 compiles all
    mutants into the code once behind a switch, forks workers from a warm process and runs only
    the tests that cover the mutated function. mutmut caches results in backend/mutants/
    (git-ignored) and re-tests only what changed; use --fresh if results look out of date.

    Configuration lives in [tool.mutmut] in backend/pyproject.toml. mutmut copies the code into
    backend/mutants/ and runs the tests there, so tests that find repo files by relative path are
    deselected (the spec-freshness test and the real-feeds-file test); everything else runs.

    Inspect one mutant:  uv run mutmut show "swipe_rss.dedup.x_normalize_link__mutmut_8"
    Browse interactively: uv run mutmut browse

    When to run it: before finishing a stage, after writing a batch of new code or tests, or when
    a module feels under-tested. It is exploration, not a gate: it is not part of pytest and this
    script exits 0 even when mutants survive.

WHAT IT CHECKS
    - For each generated mutant in src/swipe_rss/ (except the generated api_models.py): does at
      least one test fail? It reports per module how many mutants were killed, survived or had no
      test at all, and a mutation score: killed / (killed + survived).

WHAT IT DOES NOT CHECK
    - Whether the *right* test killed a mutant (mutants.py does that for its curated list).
    - Code outside src/swipe_rss/: migrations, the spec, configuration files, the Dockerfile.
    - The two deselected tests (see above); mutants.py covers the spec-freshness test.
    - Bugs that aren't small local edits: missing features, wrong design, concurrency, timing.
    - A high score is not proof of good tests, and 100% is not a goal: some mutants can never be
      killed (see "equivalent" below).

HOW TO READ THE RESULTS
    killed      A test failed on the mutant. Good.
    survived    All tests passed with the bug in place. Triage each one into:
                  - real gap:   the change matters and no test notices. Write a test.
                  - equivalent: the change can't alter behaviour (e.g. "If-None-Match" vs
                                "IF-NONE-MATCH": HTTP header names are case-insensitive). Ignore.
                  - noise:      the change matters only cosmetically (e.g. a log message text).
                                Ignore, or test it if the output is important.
    no tests    No test executes this function at all (currently cli.py and config.py). That is
                a coverage gap, not a test-quality gap; decide whether the code deserves a test.
    timeout / suspicious / segfault
                The mutant made the tests hang, slow down or crash. Usually counts as caught,
                but look at it if there are many.

    Score: killed / (killed + survived). It goes up when real gaps are closed. Compare it run to
    run for the same modules; don't compare it across projects.
"""

import argparse
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
_RESULT_LINE = re.compile(r"^\s*(swipe_rss\.(\w+)\.\S+): (.+)$")


def _mutmut(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "mutmut", *args], cwd=BACKEND_DIR, capture_output=capture, text=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run mutmut and summarize the results (see module docstring).")
    parser.add_argument("modules", nargs="*", help="only mutate these modules, e.g. fetcher dedup")
    parser.add_argument("--show", action="store_true", help="print the diff of every surviving mutant")
    parser.add_argument("--fresh", action="store_true", help="delete backend/mutants/ (cached results) first")
    args = parser.parse_args(argv)

    if args.fresh:
        shutil.rmtree(BACKEND_DIR / "mutants", ignore_errors=True)

    patterns = [f"swipe_rss.{m}.*" for m in args.modules]
    run = _mutmut("run", *patterns, capture=True)
    if run.returncode != 0:
        print(run.stdout[-3000:], run.stderr[-3000:], sep="\n")
        print(f"mutmut run failed (exit {run.returncode}); a failing baseline test is the usual cause.")
        return run.returncode

    results = _mutmut("results", "--all", "true", capture=True).stdout
    per_module: dict[str, Counter] = defaultdict(Counter)
    survivors: list[str] = []
    for line in results.splitlines():
        match = _RESULT_LINE.match(line)
        if not match or (args.modules and match.group(2) not in args.modules):
            continue
        name, module, status = match.groups()
        per_module[module][status] += 1
        if status == "survived":
            survivors.append(name)

    statuses = sorted({s for c in per_module.values() for s in c})
    print(f"{'module':<10}" + "".join(f"{s:>12}" for s in statuses) + f"{'score':>8}")
    total: Counter = Counter()
    for module in sorted(per_module):
        counts = per_module[module]
        total += counts
        print(f"{module:<10}" + "".join(f"{counts[s]:>12}" for s in statuses) + f"{_score(counts):>8}")
    print(f"{'total':<10}" + "".join(f"{total[s]:>12}" for s in statuses) + f"{_score(total):>8}")

    if survivors:
        print(f"\n{len(survivors)} survivors (triage: real gap / equivalent / noise, see the docstring):")
        for name in survivors:
            print(f"  {name}")
            if args.show:
                diff = _mutmut("show", name, capture=True).stdout
                for diff_line in diff.splitlines():
                    if diff_line[:2] in ("- ", "+ ") or (diff_line[:1] in "-+" and diff_line[:3] not in ("---", "+++")):
                        print(f"      {diff_line}")
    return 0


def _score(counts: Counter) -> str:
    tested = counts["killed"] + counts["survived"]
    return f"{100 * counts['killed'] / tested:.0f}%" if tested else "-"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
