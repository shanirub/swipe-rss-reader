"""HTTP API (contract: api/openapi.yaml). Run: uvicorn swipe_rss.api:create_app --factory

Secure by default: the token check is an app-level dependency, so every route
requires the bearer token, however it is registered, except the explicit
PUBLIC_PATHS allowlist (PROJECT_PLAN.md §3 API conventions). FastAPI's own /docs
and /openapi.json are off: the hand-written spec is the single source of truth.

Routes are registered directly on the app: since FastAPI 0.14x, `include_router`
no longer lists the included routes in `app.routes`, which tests need to enumerate.
"""

import hmac
import json
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from swipe_rss.api_models import Health, QueueResponse, SwipeBatch, SwipeBatchResult
from swipe_rss.config import Settings, get_settings
from swipe_rss.db import make_engine
from swipe_rss.queue import select_queue
from swipe_rss.swipes import record_swipes

log = logging.getLogger(__name__)

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

    engine = make_engine(settings.db_path)

    @app.exception_handler(RequestValidationError)
    async def log_rejected_swipes(request: Request, exc: RequestValidationError):
        # A rejected swipe batch becomes a dead letter on the phone; log it so it is noticed
        # here too (PROJECT_PLAN.md §3 Swipe recording). Own data, single user: the body is logged.
        if request.url.path == "/swipes":
            body = exc.body if isinstance(exc.body, dict) else {}
            swipes = body.get("swipes") if isinstance(body.get("swipes"), list) else []
            ids = [s.get("swipe_id") for s in swipes if isinstance(s, dict)]
            log.warning(
                "rejected swipe batch: swipe_ids=%s errors=%s body=%s",
                ids, json.dumps(exc.errors(), default=str), json.dumps(exc.body, default=str),
            )  # fmt: skip
        return await request_validation_exception_handler(request, exc)

    # Endpoints are plain `def`: FastAPI runs them in its thread pool (sync SQLAlchemy, PROJECT_PLAN.md §3).
    # Each request is one explicit transaction, committed before the response is sent.

    @app.get("/health", response_model=Health)
    def get_health() -> Health:
        return Health(status="ok")

    @app.get("/queue", response_model=QueueResponse)
    def get_queue(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> QueueResponse:
        with Session(engine) as session, session.begin():
            return QueueResponse(items=select_queue(session, limit))

    @app.post("/swipes", response_model=SwipeBatchResult)
    def post_swipes(batch: SwipeBatch) -> SwipeBatchResult:
        with Session(engine) as session, session.begin():
            result = record_swipes(session, batch.swipes)
        return SwipeBatchResult(stored=result.stored, duplicates=result.duplicates)

    return app
