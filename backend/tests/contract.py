"""Contract coverage: which documented API responses the test suite actually produces.

conftest.py wraps the test client, so every response a test receives is recorded as
(method, spec path, status). After a full test run it compares that set with api/openapi.yaml:
- every documented response must have been produced by at least one test (nothing untested);
- a response on a documented path that the spec doesn't document fails the run too (the API
  must not answer in ways the contract doesn't describe).
Requests to paths that aren't in the spec (e.g. test-only routes) are not recorded. For a method the
spec doesn't list on a documented path, the spec's global rules apply (info.description; OpenAPI
can't attach responses to operations that don't exist): 405, or 401 when the token is missing.
"""

import re
from pathlib import Path

import yaml

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
GLOBAL_RULE_STATUSES = {401, 405}  # answers to a method the spec doesn't list (see module docstring)


def find_spec() -> Path:
    # Walk up instead of a fixed relative path: mutmut runs the tests from a copy in backend/mutants/.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "api" / "openapi.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("api/openapi.yaml not found above " + str(Path(__file__).parent))


def load_spec(path: Path | None = None) -> dict:
    return yaml.safe_load((path or find_spec()).read_text())


def operations(spec: dict) -> set[tuple[str, str]]:
    """(METHOD, path) for every operation in the spec."""
    return {(method.upper(), path) for path, ops in spec["paths"].items() for method in ops if method in HTTP_METHODS}


def documented_responses(spec: dict) -> set[tuple[str, str, int]]:
    """(METHOD, path, status) for every documented response. Fails loudly on ranges like 4XX."""
    return {
        (method.upper(), path, int(status))
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method in HTTP_METHODS
        for status in op["responses"]
    }


def path_pattern(template: str) -> re.Pattern:
    """/saved/{feed_id}/{item_key}/content → ^/saved/[^/]+/[^/]+/content$ (a parameter never spans a /)."""
    literal_parts = re.split(r"\{[^/{}]+\}", template)
    return re.compile("^" + "[^/]+".join(re.escape(part) for part in literal_parts) + "$")


class Recorder:
    """Records the (method, spec path, status) of every response a test client receives.

    In test-pattern terms (Meszaros, xUnit Test Patterns) this is a test spy: it records calls for
    later checks. Unlike a classic spy it replaces nothing; conftest.py wraps the real TestClient
    (instrumentation by monkey patching) and passes every call through.
    """

    def __init__(self, spec: dict) -> None:
        self.documented = documented_responses(spec)
        self._operations = operations(spec)
        self._patterns = [(path, path_pattern(path)) for path in spec["paths"]]
        self.seen: set[tuple[str, str, int]] = set()

    def template_for(self, url_path: str) -> str | None:
        for template, pattern in self._patterns:
            if pattern.match(url_path):
                return template
        return None

    def record(self, method: str, url_path: str, status: int) -> None:
        template = self.template_for(url_path)
        if template is None:
            return
        if (method.upper(), template) not in self._operations and status in GLOBAL_RULE_STATUSES:
            return  # wrong method → 405 (401 without token): the spec's global rules
        self.seen.add((method.upper(), template, status))

    def problems(self) -> list[str]:
        missing = sorted(self.documented - self.seen)
        undocumented = sorted(self.seen - self.documented)
        return [f"documented but no test produces it: {m} {p} → {s}" for m, p, s in missing] + [
            f"returned but not documented in the spec: {m} {p} → {s}" for m, p, s in undocumented
        ]
