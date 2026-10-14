"""Hand-picked mutation checks for the Android app's guard tests.

WHAT THIS IS
    The Android counterpart of backend/scripts/mutants.py. A "mutant" is a small, deliberate
    bug: one exact snippet of real code replaced by a broken version. For each mutant this
    script edits the Kotlin file, runs the JVM tests that are supposed to protect that
    behaviour, and restores the file. If the expected test fails, the mutant is "killed":
    the test really guards that rule. If every test passes, the mutant "survived".

    The list is curated (see MUTANTS below), not generated: no maintained mutation tool fits
    this Android setup (PROJECT_PLAN.md §3 Android). It holds the swipe-sync mutants for now;
    the older hand-made mutants are listed in app/src/test/README.md and get added here when
    their code changes.

HOW TO USE
    cd android
    python3 scripts/mutants.py                 # run all mutants
    python3 scripts/mutants.py refill dead     # only mutants whose name contains "refill" or "dead"
    python3 scripts/mutants.py --list          # list mutants without running anything

    Uses $JAVA_HOME if set, otherwise Android Studio's bundled JDK (see android/README.md).
    Each mutant is one Gradle test run of its test class: about 10-20 s, so the whole list
    takes several minutes. Run it after changing sync code or its tests. Not part of CI.

    Adding a mutant: append a Mutant(...) with the file, the exact original snippet, its
    broken replacement, the test class to run and the test that should fail. Prefer
    realistic bugs (a forgotten call, a swapped order, a removed condition) over ones that
    don't compile.

WHAT IT CHECKS
    - Baseline: the selected test classes pass on the unmodified code. If not, it stops.
    - Each mutant: does the named test fail when this exact bug is introduced?
    - That the original snippet still occurs exactly once, and that the named test still
      exists. Otherwise the mutant is STALE instead of silently testing nothing.
    - That every file is restored byte for byte afterwards.

WHAT IT DOES NOT CHECK
    - Code without a listed mutant: a clean run means "these guards work", not "the tests
      are complete".
    - Anything only visible on the phone (drawing, animation, real network).

HOW TO READ THE RESULTS
    KILLED        The expected test failed. The guard works.
    KILLED-OTHER  Tests failed, but not the expected one: maybe caught for the wrong reason.
    SURVIVED      All tests passed with the bug in place: fix the test, not the mutant.
    ACCEPTED      Survived, as expected: a mutant deliberately accepted as harmless (the
                  reason is in `why`). If one of these is ever KILLED, the test got stricter;
                  drop the `accepted` flag.
    STALE         The snippet wasn't found exactly once, or the named test doesn't exist.
    ERROR         Gradle failed without failing tests (usually a compile error).

    Exit code: 0 if every selected mutant was KILLED or ACCEPTED, 1 otherwise.

SAFETY
    Files are restored in a `finally` block, also on Ctrl-C. Gradle compares file contents,
    not timestamps, so a restored file is never mistaken for the mutant. If the process is
    killed hard, a mutated file can remain: check `git status` / `git diff`. Don't run two
    instances at once, and don't edit target files while it runs.
"""

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ANDROID_DIR = Path(__file__).resolve().parents[1]
MAIN = "app/src/main/java/io/github/shanirub/swiperss/"
TEST = "app/src/test/java/io/github/shanirub/swiperss/"
STUDIO_JDK = Path.home() / ".local/share/JetBrains/Toolbox/apps/android-studio/jbr"


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str  # relative to android/
    original: str  # must occur exactly once in the file
    mutated: str
    tests: str  # test class, relative to the test package, e.g. "swipe.SwipeSyncTest"
    killed_by: str  # name of the test function expected to fail
    why: str  # the rule this mutant breaks (for accepted ones: why surviving is fine)
    accepted: bool = False  # a known, accepted survivor


SYNC = MAIN + "swipe/SwipeSync.kt"
REPO = MAIN + "swipe/SwipeRepository.kt"
VM = MAIN + "swipe/SwipeViewModel.kt"
DB = MAIN + "db/LocalStore.kt"

MUTANTS = [
    # --- sending (SwipeSync) ---
    Mutant(
        name="sync: swipes deleted before the server answered",
        file=SYNC,
        original="                post(batch)\n                outbox.delete(batch.map { it.swipeId })",
        mutated="                outbox.delete(batch.map { it.swipeId })\n                post(batch)",
        tests="swipe.SwipeSyncTest",
        killed_by="failuresStopSendingAndKeepEverything",
        why="a swipe leaves the phone only after a 200",
    ),
    Mutant(
        name="sync: a 422 on a batch stops sending",
        file=SYNC,
        original="                if (e.code() != 422) throw e\n                sendOneByOne(batch)",
        mutated="                throw e",
        tests="swipe.SwipeSyncTest",
        killed_by="aRejectedBatchIsSentOneByOneAndOnlyTheBadSwipeIsKept",
        why="one bad swipe must not block the others",
    ),
    Mutant(
        name="sync: the whole rejected batch becomes dead letters",
        file=SYNC,
        original="                sendOneByOne(batch)",
        mutated='                batch.forEach { outbox.markDead(it.swipeId, "x", appVersion) }',
        tests="swipe.SwipeSyncTest",
        killed_by="aRejectedBatchIsSentOneByOneAndOnlyTheBadSwipeIsKept",
        why="only a swipe rejected on its own is a dead letter",
    ),
    Mutant(
        name="sync: any failure while sending one by one makes a dead letter",
        file=SYNC,
        original="                if (e.code() != 422) throw e\n                outbox.markDead",
        mutated="                outbox.markDead",
        tests="swipe.SwipeSyncTest",
        killed_by="aFailureWhileSendingOneByOneStopsWithoutDeadLetters",
        why="a lost connection is not a rejection: the swipe stays pending",
    ),
    Mutant(
        name="sync: dead letter without the app version",
        file=SYNC,
        original="outbox.markDead(row.swipeId, errorText(e), appVersion)",
        mutated="outbox.markDead(row.swipeId, errorText(e), 0)",
        tests="swipe.SwipeSyncTest",
        killed_by="aRejectedBatchIsSentOneByOneAndOnlyTheBadSwipeIsKept",
        why="the version decides the automatic retry after an update",
    ),
    Mutant(
        name="sync: dead letter without the server's error",
        file=SYNC,
        original='return "HTTP ${e.code()}: ${body.take(ERROR_MAX_CHARS)}"',
        mutated='return "HTTP ${e.code()}"',
        tests="swipe.SwipeSyncTest",
        killed_by="aRejectedBatchIsSentOneByOneAndOnlyTheBadSwipeIsKept",
        why="the error says why the server rejected the swipe",
    ),
    Mutant(
        name="sync: batches above the spec's 500",
        file=SYNC,
        original="outbox.pending(BATCH_LIMIT)",
        mutated="outbox.pending(1000)",
        tests="swipe.SwipeSyncTest",
        killed_by="aLargeOutboxGoesInBatchesOf500",
        why="the spec allows at most 500 swipes per request",
    ),
    Mutant(
        name="sync: only the first swipe of a batch sent",
        file=SYNC,
        original="rows.joinToString",
        mutated="rows.take(1).joinToString",
        tests="swipe.SwipeSyncTest",
        killed_by="pendingSwipesAreSentAsStoredAndThenDeleted",
        why="every swipe deleted after a 200 must have been in the request",
    ),
    # --- the lock (SwipeRepository) ---
    Mutant(
        name="repository: refill outside the lock",
        file=REPO,
        original="suspend fun refill() = network.withLock {",
        mutated="suspend fun refill() = run {",
        tests="swipe.SwipeRepositoryTest",
        killed_by="refillAndSyncNeverOverlap",
        why="a stale /queue answer must not bring back a just-sent swipe's card",
    ),
    Mutant(
        name="repository: sync outside the lock",
        file=REPO,
        original="suspend fun sync() = network.withLock { sender.sendPending() }",
        mutated="suspend fun sync() = sender.sendPending()",
        tests="swipe.SwipeRepositoryTest",
        killed_by="refillAndSyncNeverOverlap",
        why="a stale /queue answer must not bring back a just-sent swipe's card",
    ),
    # --- triggers and screen state (SwipeViewModel) ---
    Mutant(
        name="viewmodel: refill before sending",
        file=VM,
        original="        sync()\n        refill()\n",
        mutated="        refill()\n        sync()\n",
        tests="swipe.SwipeViewModelTest",
        killed_by="comingBackSendsSwipesBeforeAskingForCards",
        why="the server should have the swipes before it is asked for cards",
    ),
    Mutant(
        name="viewmodel: no sync after swiping",
        file=VM,
        original="        }\n        syncSoon()\n",
        mutated="        }\n",
        tests="swipe.SwipeViewModelTest",
        killed_by="swipesAreSentShortlyAfterSwiping",
        why="swipes go to the server a few seconds after the last one",
    ),
    Mutant(
        name="viewmodel: no refill after the delayed sync",
        file=VM,
        original="            viewModelScope.launch { syncThenRefill() }\n",
        mutated="            viewModelScope.launch { sync() }\n",
        tests="swipe.SwipeViewModelTest",
        killed_by="cardsAreAskedForAgainAfterSwipesAreSent",
        why="a stack left empty by unsent swipes' cards fills again once they are sent",
    ),
    Mutant(
        name="viewmodel: 'not sent yet' shown without a failed sync",
        file=VM,
        original="pending.takeIf { s.syncFailed && it > 0 }",
        mutated="pending.takeIf { it > 0 }",
        tests="swipe.SwipeViewModelTest",
        killed_by="unsentSwipesAreShownWhileSendingFails",
        why="pending swipes are normal during the 5-second wait",
    ),
    Mutant(
        name="viewmodel: dead letters from an older version never revived",
        file=VM,
        original="                repository.reviveOlderDeadLetters()\n",
        mutated="",
        tests="swipe.SwipeViewModelTest",
        killed_by="deadLettersFromAnOlderVersionGetOneMoreTry",
        why="a release that fixes the cause gets one more try",
    ),
    Mutant(
        name="viewmodel: Retry doesn't revive dead letters",
        file=VM,
        original="            repository.reviveAllDeadLetters()\n",
        mutated="",
        tests="swipe.SwipeViewModelTest",
        killed_by="deadLettersAreShownAndCanBeRetried",
        why="Retry sends the dead letters again",
    ),
    Mutant(
        name="viewmodel: dead letters not counted on the screen",
        file=VM,
        original="pending.takeIf { s.syncFailed && it > 0 }, dead)",
        mutated="pending.takeIf { s.syncFailed && it > 0 }, 0)",
        tests="swipe.SwipeViewModelTest",
        killed_by="deadLettersAreShownAndCanBeRetried",
        why="rejected swipes must be visible",
    ),
    # --- counts and revive (LocalStore) ---
    Mutant(
        name="db: pending count includes dead letters",
        file=DB,
        original='@Query("SELECT COUNT(*) FROM outbox WHERE dead_error IS NULL")',
        mutated='@Query("SELECT COUNT(*) FROM outbox")',
        tests="db.LocalStoreTest",
        killed_by="theScreenSeesHowManySwipesArePendingAndDead",
        why="'not sent yet' counts only swipes that will be sent",
    ),
    Mutant(
        name="db: Retry revives only older versions' dead letters",
        file=DB,
        original='WHERE dead_error IS NOT NULL")\n    suspend fun reviveAllDeadLetters',
        mutated='WHERE dead_app_version < 5")\n    suspend fun reviveAllDeadLetters',
        tests="db.LocalStoreTest",
        killed_by="retryRevivesEveryDeadLetterWhateverItsVersion",
        why="Retry means every dead letter",
    ),
    # --- accepted survivors (harmless; the code edit each needs is unlikely) ---
    Mutant(
        name="viewmodel: the 'sending failed' flag never cleared",
        file=VM,
        original="            repository.sync()\n            false\n",
        mutated="            repository.sync()\n            status.value.syncFailed\n",
        tests="swipe.SwipeViewModelTest",
        killed_by="unsentSwipesAreShownWhileSendingFails",
        why="cosmetic: 'not sent yet' would also show during the normal 5-second wait",
        accepted=True,
    ),
    Mutant(
        name="viewmodel: the 5-second wait not restarted by a new swipe",
        file=VM,
        original="        syncTimer?.cancel()\n",
        mutated="",
        tests="swipe.SwipeViewModelTest",
        killed_by="swipesAreSentShortlyAfterSwiping",
        why="more, smaller requests; nothing is lost",
        accepted=True,
    ),
    Mutant(
        name="viewmodel: a later swipe can cancel a sync on its way",
        file=VM,
        original="            viewModelScope.launch { syncThenRefill() }\n",
        mutated="            syncThenRefill()\n",
        tests="swipe.SwipeViewModelTest",
        killed_by="swipesAreSentShortlyAfterSwiping",
        why="a wasted request; resends are safe (swipe_id)",
        accepted=True,
    ),
]

_FAILED_LINE = re.compile(r"^(\w+) > (\w+)\S* FAILED", re.MULTILINE)


def _test_class(tests: str) -> str:
    return "io.github.shanirub.swiperss." + tests


def _test_file(tests: str) -> Path:
    return ANDROID_DIR / TEST / (tests.replace(".", "/") + ".kt")


def _run_tests(classes: list[str]) -> tuple[int, set[str], str]:
    java_home = os.environ.get("JAVA_HOME") or str(STUDIO_JDK)
    command = ["./gradlew", "testDebugUnitTest", "--rerun", "--console=plain"]
    for c in classes:
        command += ["--tests", _test_class(c)]
    result = subprocess.run(
        command, cwd=ANDROID_DIR, capture_output=True, text=True, env={**os.environ, "JAVA_HOME": java_home}
    )
    output = result.stdout + result.stderr
    return result.returncode, {test for _, test in _FAILED_LINE.findall(output)}, output


def _verdict(mutant: Mutant) -> tuple[str, str]:
    path = ANDROID_DIR / mutant.file
    original_text = path.read_text()
    count = original_text.count(mutant.original)
    if count != 1:
        return "STALE", f"original snippet found {count}x in {mutant.file}"
    if f"fun {mutant.killed_by}(" not in _test_file(mutant.tests).read_text():
        return "STALE", f"no test {mutant.killed_by} in {mutant.tests}"
    try:
        path.write_text(original_text.replace(mutant.original, mutant.mutated))
        code, failed, output = _run_tests([mutant.tests])
    finally:
        path.write_text(original_text)
    if path.read_text() != original_text:
        raise SystemExit(f"FATAL: {mutant.file} was not restored; check `git diff`")

    if code == 0:
        return ("ACCEPTED", mutant.why) if mutant.accepted else ("SURVIVED", "all target tests passed")
    if not failed:
        errors = re.findall(r"^e: .*", output, re.MULTILINE)
        return "ERROR", errors[0] if errors else f"Gradle exit code {code}, no failed test"
    if mutant.killed_by in failed:
        return "KILLED", f"by {mutant.killed_by}"
    return "KILLED-OTHER", f"expected {mutant.killed_by}, failed: {', '.join(sorted(failed))}"


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for m in MUTANTS:
            flag = "  (accepted survivor)" if m.accepted else ""
            print(f"{m.name}{flag}\n    {m.file} -> {m.tests}.{m.killed_by}\n    {m.why}")
        return 0

    selected = [m for m in MUTANTS if not argv or any(a.lower() in m.name.lower() for a in argv)]
    if not selected:
        print("no mutant matches", argv)
        return 1

    classes = sorted({m.tests for m in selected})
    code, failed, _ = _run_tests(classes)
    if code != 0:
        print(f"baseline failed (exit {code}; {', '.join(sorted(failed)) or 'no failed test, see Gradle'}). Fix the tests first.")
        return 1

    started = time.monotonic()
    results = []
    for m in selected:
        t0 = time.monotonic()
        verdict, detail = _verdict(m)
        results.append(verdict)
        print(f"{verdict:<13} {m.name}  ({time.monotonic() - t0:.0f}s)  {detail}", flush=True)

    good = sum(v in ("KILLED", "ACCEPTED") for v in results)
    print(
        f"\n{results.count('KILLED')} killed, {results.count('ACCEPTED')} accepted, "
        f"{len(results) - good} other, of {len(results)} in {time.monotonic() - started:.0f}s"
    )
    return 0 if good == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
