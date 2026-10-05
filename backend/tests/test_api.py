from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from swipe_rss.api import MIN_TOKEN_LENGTH, PUBLIC_PATHS, BearerTokenMiddleware, create_app
from swipe_rss.api_models import Error, Health
from swipe_rss.config import Settings

TOKEN = "t" * MIN_TOKEN_LENGTH


def settings(token: str | None = TOKEN) -> Settings:
    return Settings(db_path=Path("unused.db"), feeds_path=Path("unused.toml"), api_token=token)


@pytest.fixture
def client():
    return TestClient(create_app(settings()))


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    Health.model_validate(response.json())


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_fastapi_generated_docs_are_off(client, path):
    # The hand-written api/openapi.yaml is the only spec; FastAPI must not serve a second one.
    # With the token: without it, every path answers 401 before routing.
    assert client.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


@pytest.mark.parametrize("token", [None, "", "short"])
def test_app_refuses_to_start_without_a_strong_token(token):
    with pytest.raises(RuntimeError, match="SWIPE_RSS_API_TOKEN"):
        create_app(settings(token))


def test_only_health_is_public():
    assert {"/health"} == PUBLIC_PATHS  # making a route public is a deliberate, reviewed change


def test_every_route_except_the_public_ones_requires_the_token():
    app = create_app(settings())
    client = TestClient(app)
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/health" in paths  # the enumeration must see real routes, or this test checks nothing
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_PATHS:
            continue
        for method in route.methods:
            response = client.request(method, route.path.replace("{", "x").replace("}", ""))
            assert response.status_code == 401, (method, route.path)


def test_routes_added_later_are_protected_too():
    # The middleware must also cover routers included after create_app.
    app = create_app(settings())
    router = APIRouter()

    @router.get("/later")
    def later() -> dict:
        return {}

    app.include_router(router)
    client = TestClient(app)
    assert client.get("/later").status_code == 401
    assert client.get("/later", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


@pytest.fixture
def guarded():
    # A minimal app behind the same middleware, to exercise the token check itself.
    app = FastAPI()

    @app.get("/guarded")
    def guarded_route() -> dict:
        return {"ok": True}

    app.add_middleware(BearerTokenMiddleware, token=TOKEN)
    return TestClient(app)


def test_right_token_passes(guarded):
    for scheme in ("Bearer", "bearer"):  # the scheme is case-insensitive (RFC 7235)
        assert guarded.get("/guarded", headers={"Authorization": f"{scheme} {TOKEN}"}).json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no header
        {"Authorization": f"Bearer {TOKEN}x"},  # wrong token
        {"Authorization": f"Bearer {TOKEN[:-1]}"},  # prefix of the right token
        {"Authorization": f"Basic {TOKEN}"},  # wrong scheme
        {"Authorization": TOKEN},  # no scheme
    ],
)
def test_bad_credentials_get_401(guarded, headers):
    response = guarded.get("/guarded", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    Error.model_validate(response.json())  # body shape from the spec


def test_correct_token_passes(guarded):
    assert guarded.get("/guarded", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
