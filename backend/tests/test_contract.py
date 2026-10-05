"""The app matches api/openapi.yaml, and the contract-coverage check itself works (tests/contract.py).

The coverage check (every documented response produced by some test, nothing undocumented
returned) runs in conftest.py after a full, green test run. The second half tests the spec's
global conventions (info.description) that can't be attached to single operations: unknown
paths 404, wrong methods 405, unknown query parameters 422.
"""

from pathlib import Path
from typing import Annotated

import contract
import pytest
from fastapi import Depends
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from swipe_rss.api import LENIENT_QUERY_PATHS, MIN_TOKEN_LENGTH, create_app
from swipe_rss.api_models import Error, HTTPValidationError
from swipe_rss.config import Settings

SPEC = contract.load_spec()
OPERATIONS = sorted(contract.operations(SPEC))
TOKEN = "t" * MIN_TOKEN_LENGTH
AUTH = {"Authorization": f"Bearer {TOKEN}"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]  # HEAD/OPTIONS: no body to check


def test_app_routes_equal_the_spec():
    # Point (c): an endpoint in the code but not in the spec (or the reverse) fails here.
    app = create_app(
        Settings(db_path=Path("unused.db"), feeds_path=Path("unused.toml"), api_token="t" * MIN_TOKEN_LENGTH)
    )
    app_operations = {
        (method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods
    }
    assert app_operations  # the enumeration must see routes, or this test checks nothing
    assert app_operations == contract.operations(SPEC)


def test_spec_documents_responses_for_every_operation():
    assert {(m, p) for m, p, _ in contract.documented_responses(SPEC)} == contract.operations(SPEC)


@pytest.mark.parametrize(
    ("url_path", "template"),
    [
        ("/queue", "/queue"),
        ("/saved", "/saved"),
        ("/saved/feed/key/content", "/saved/{feed_id}/{item_key}/content"),
        ("/saved/feed/k:ey/content", "/saved/{feed_id}/{item_key}/content"),
        ("/saved/a/b/c/content", None),  # a parameter never spans a /
        ("/saved/feed/key", None),
        ("/queue/extra", None),
        ("/queuex", None),
        ("/guarded", None),  # test-only routes are not recorded
    ],
)
def test_url_paths_map_to_spec_templates(url_path, template):
    assert contract.Recorder(SPEC).template_for(url_path) == template


def test_recorder_reports_missing_and_undocumented_responses():
    spec = {"paths": {"/a": {"get": {"responses": {"200": {}, "401": {}}}, "parameters": []}}}
    recorder = contract.Recorder(spec)
    recorder.record("get", "/a", 200)
    recorder.record("GET", "/a", 405)
    recorder.record("GET", "/elsewhere", 500)  # not a spec path: ignored
    recorder.record("DELETE", "/a", 405)  # method not in the spec → 405: the global rule, accepted
    recorder.record("PUT", "/a", 200)  # method not in the spec answering anything but 405: reported
    assert recorder.problems() == [
        "documented but no test produces it: GET /a → 401",
        "returned but not documented in the spec: GET /a → 405",
        "returned but not documented in the spec: PUT /a → 200",
    ]
    recorder.record("GET", "/a", 401)
    recorder.seen -= {("GET", "/a", 405), ("PUT", "/a", 200)}
    assert recorder.problems() == []


def test_documented_responses_reject_status_ranges():
    # "4XX"-style ranges would need explicit handling; fail loudly instead of ignoring them.
    with pytest.raises(ValueError):
        contract.documented_responses({"paths": {"/a": {"get": {"responses": {"4XX": {}}}}}})


# --- global conventions (spec info.description) ---


@pytest.fixture
def client(engine, db_path):  # `engine` migrates the temp database first
    return TestClient(create_app(Settings(db_path=db_path, feeds_path=db_path, api_token=TOKEN)), headers=AUTH)


def concrete(template: str) -> str:
    return template.replace("{feed_id}", "a").replace("{item_key}", "guid:" + "0" * 64)


def test_unknown_path_is_404(client):
    response = client.get("/no-such-path")
    assert response.status_code == 404
    Error.model_validate(response.json())


@pytest.mark.parametrize("path", ["/no-such-path", "/queue"])
def test_without_token_every_path_except_health_is_401(engine, db_path, path):
    # The token is checked before routing: without it, nothing reveals which paths exist.
    client = TestClient(create_app(Settings(db_path=db_path, feeds_path=db_path, api_token=TOKEN)))
    response = client.delete(path)  # wrong method too: still 401, not 405
    assert response.status_code == 401
    Error.model_validate(response.json())


@pytest.mark.parametrize(
    ("method", "path"), [(m, p) for p in SPEC["paths"] for m in METHODS if (m, p) not in contract.operations(SPEC)]
)
def test_method_not_in_spec_is_405_with_allow_header(client, method, path):
    response = client.request(method, concrete(path))
    assert response.status_code == 405
    Error.model_validate(response.json())
    allowed = {m for m, p in contract.operations(SPEC) if p == path}
    assert set(response.headers["Allow"].replace(" ", "").split(",")) == allowed


@pytest.mark.parametrize(("method", "path"), [op for op in OPERATIONS if op[1] not in LENIENT_QUERY_PATHS])
def test_unknown_query_parameter_is_422(client, method, path):
    response = client.request(method, concrete(path), params={"limt": "5"}, json={} if method == "POST" else None)
    assert response.status_code == 422
    errors = HTTPValidationError.model_validate(response.json()).detail
    assert [e.loc for e in errors] == [["query", "limt"]]  # the unknown parameter is reported, nothing else


def test_declared_query_parameter_is_accepted(client):
    assert client.get("/queue", params={"limit": "5"}).status_code == 200


def test_query_parameter_declared_in_a_dependency_is_accepted(engine, db_path):
    # A route may get its query parameters through a dependency; those count as declared too.
    app = create_app(Settings(db_path=db_path, feeds_path=db_path, api_token=TOKEN))

    def paging(page: int = 1) -> int:
        return page

    @app.get("/test-only/paged")
    def paged(page: Annotated[int, Depends(paging)]) -> dict:
        return {"page": page}

    client = TestClient(app, headers=AUTH)
    assert client.get("/test-only/paged", params={"page": "2"}).json() == {"page": 2}
    assert client.get("/test-only/paged", params={"pag": "2"}).status_code == 422


def test_several_unknown_query_parameters_are_all_reported(client):
    response = client.get("/queue", params={"limt": "5", "x": "1", "limit": "5"})
    assert [e["loc"] for e in response.json()["detail"]] == [["query", "limt"], ["query", "x"]]


def test_health_ignores_unknown_query_parameters(client):
    assert client.get("/health", params={"probe": "1"}).status_code == 200


def test_token_is_checked_before_query_parameters(engine, db_path):
    client = TestClient(create_app(Settings(db_path=db_path, feeds_path=db_path, api_token=TOKEN)))
    assert client.get("/queue", params={"limt": "5"}).status_code == 401
