"""HTTP API (contract: api/openapi.yaml). Run: uvicorn swipe_rss.api:create_app --factory

Secure by default: the token check is an app-level dependency, so every route
requires the bearer token, however it is registered, except the explicit
PUBLIC_PATHS allowlist (PROJECT_PLAN.md §3 API conventions). FastAPI's own /docs
and /openapi.json are off: the hand-written spec is the single source of truth.

Routes are registered directly on the app: since FastAPI 0.14x, `include_router`
no longer lists the included routes in `app.routes`, which tests need to enumerate.
"""

import hmac
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from swipe_rss.api_models import Health
from swipe_rss.config import Settings, get_settings

MIN_TOKEN_LENGTH = 32
PUBLIC_PATHS = frozenset({"/health"})

_bearer = HTTPBearer(auto_error=False)  # missing/malformed header -> None; we answer 401 ourselves


def _require_token(expected: str):
    expected_bytes = expected.encode()

    def check(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> None:
        if request.url.path in PUBLIC_PATHS:
            return
        # Constant-time comparison: response timing must not reveal how much of the token matched.
        if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected_bytes):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return check


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.api_token or len(settings.api_token) < MIN_TOKEN_LENGTH:
        # Fail closed: a missing or weak token must never mean an unauthenticated API.
        raise RuntimeError(f"SWIPE_RSS_API_TOKEN must be set to at least {MIN_TOKEN_LENGTH} characters")

    app = FastAPI(
        title="Swipe RSS API",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(_require_token(settings.api_token))],
    )

    @app.get("/health", response_model=Health)
    def get_health() -> Health:
        return Health(status="ok")

    return app
