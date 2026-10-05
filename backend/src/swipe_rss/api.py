"""HTTP API (contract: api/openapi.yaml). Run: uvicorn swipe_rss.api:create_app --factory

Secure by default (PROJECT_PLAN.md §3 API conventions):
- The bearer token is checked by ASGI middleware before routing and before any body
  byte is read, for every path except the explicit PUBLIC_PATHS allowlist. (A FastAPI
  dependency would run too late: FastAPI parses the JSON body before dependencies.)
- Request bodies over MAX_BODY_BYTES are refused with 413 before they reach FastAPI.
- Every route rejects query parameters it doesn't declare (422), except /health; an
  app-level dependency, so new routes are strict automatically.
FastAPI's own /docs and /openapi.json are off: the hand-written spec is the single source of truth.

Routes are registered directly on the app: since FastAPI 0.14x, `include_router`
no longer lists the included routes in `app.routes`, which tests need to enumerate.
"""

import hmac
import json
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime, Field, TypeAdapter, ValidationError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from swipe_rss.api_models import (
    Card,
    FeedListResponse,
    Health,
    QueueResponse,
    SavedContent,
    SavedListResponse,
    SwipeBatch,
    SwipeBatchResult,
)
from swipe_rss.config import Settings, get_settings
from swipe_rss.db import make_engine
from swipe_rss.feed_health import feed_health
from swipe_rss.feeds import FeedsFileError, load_feeds
from swipe_rss.logs import configure_logging
from swipe_rss.queue import select_queue
from swipe_rss.saved import list_saved, saved_content
from swipe_rss.swipes import record_swipes
from swipe_rss.timestamps import TIMESTAMP_END, TIMESTAMP_MIN

log = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 32
PUBLIC_PATHS = frozenset({"/health"})
LENIENT_QUERY_PATHS = frozenset({"/health"})  # a liveness probe must not fail over an extra parameter
MAX_BODY_BYTES = 10 * 1024 * 1024  # largest legitimate batch: 500 swipes × ~18 KB ≈ 9 MB
LOG_BODY_MAX_CHARS = 10_000  # a rejected batch is resent swipe by swipe, and those are logged whole


# Path parameters are not covered by the generated models (they describe bodies), so their
# rules are read from the generated Card instead of being copied: they can't drift from the spec.
def _constraint(field: str, name: str):
    return next(getattr(m, name) for m in Card.model_fields[field].metadata if getattr(m, name, None) is not None)


FeedIdPath = Annotated[
    str, Path(pattern=_constraint("feed_id", "pattern"), max_length=_constraint("feed_id", "max_length"))
]
ItemKeyPath = Annotated[str, Path(pattern=_constraint("item_key", "pattern"))]


async def _send_error(send: Send, status_code: int, detail: str, headers: list[tuple[bytes, bytes]] = ()) -> None:
    body = json.dumps({"detail": detail}).encode()  # the spec's Error schema
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *headers],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class BearerTokenMiddleware:
    """401 for every path except PUBLIC_PATHS unless `Authorization: Bearer <token>` matches."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = token.encode()

    def _authorized(self, scope: Scope) -> bool:
        scheme, _, credentials = (_header(scope, b"authorization") or "").partition(" ")
        # Constant-time comparison: response timing must not reveal how much of the token matched.
        return scheme.lower() == "bearer" and hmac.compare_digest(credentials.encode(), self._expected)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in PUBLIC_PATHS and not self._authorized(scope):
            await _send_error(send, 401, "Missing or invalid bearer token", [(b"www-authenticate", b"Bearer")])
            return
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """413 for request bodies over `limit` bytes, checked on Content-Length and while streaming
    (chunked bodies have no Content-Length). Bodies are small, so the body is read here and
    handed on in one piece."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await _send_error(send, 413, f"Request body larger than {self.limit} bytes")
            return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # client went away
                return
            chunks.append(message.get("body", b""))
            size += len(chunks[-1])
            if size > self.limit:
                await _send_error(send, 413, f"Request body larger than {self.limit} bytes")
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()  # afterwards: disconnect notifications

        await self.app(scope, replay, send)


def _declared_query_params(dependant) -> set[str]:
    # Walks the route's dependency tree with public attributes only (FastAPI's own flattening
    # helpers are internal and changed names between versions). Query-parameter *models* are not
    # expanded; the API doesn't use them.
    names, stack = set(), [dependant]
    while stack:
        current = stack.pop()
        names.update(param.alias for param in current.query_params)
        stack.extend(current.dependencies)
    return names


def _reject_unknown_query_params(request: Request) -> None:
    # A typo like ?limt=5 must not be silently ignored (it would return the default). Same 422
    # format as Pydantic's extra="forbid" on request bodies.
    route = request.scope.get("route")
    if route is None or request.url.path in LENIENT_QUERY_PATHS:
        return
    declared = _declared_query_params(route.dependant)
    unknown = [name for name in dict.fromkeys(request.query_params.keys()) if name not in declared]
    if unknown:
        raise RequestValidationError(
            [
                {"type": "extra_forbidden", "loc": ("query", name), "msg": "Extra inputs are not permitted",
                 "input": request.query_params[name]}
                for name in unknown
            ]
        )  # fmt: skip


# The spec's timestamp range (prose in the spec: JSON Schema can't express it, so the generated
# models don't carry it). Checked by Pydantic, so errors keep its format.
_RANGED_TIMESTAMP = TypeAdapter(Annotated[AwareDatetime, Field(ge=TIMESTAMP_MIN, lt=TIMESTAMP_END)])


def _timestamp_range_errors(batch: SwipeBatch) -> list[dict]:
    errors = []
    for i, swipe in enumerate(batch.swipes):
        fields = {("swiped_at",): swipe.swiped_at, ("card", "fetched_at"): swipe.card.fetched_at,
                  ("card", "published_at"): swipe.card.published_at}  # fmt: skip
        for loc, value in fields.items():
            if value is None:
                continue
            try:
                _RANGED_TIMESTAMP.validate_python(value)
            except ValidationError as e:
                errors += [
                    {**error, "loc": ("body", "swipes", i, *loc), "input": value.isoformat()} for error in e.errors()
                ]
    return errors


def _text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _integral_float_as_int(text: str) -> int | float:
    number = float(text)
    return int(number) if number.is_integer() else number


async def _strict_swipe_batch(request: Request) -> SwipeBatch:
    """The swipe body, validated against the spec's JSON types (strict JSON mode).

    FastAPI's default parses the JSON first and validates the result in Pydantic's lax mode,
    which converts e.g. 0 into a timestamp or "5" into an integer. The spec (JSON Schema)
    allows neither. Errors keep FastAPI's 422 format (loc starts with "body").
    """
    body = await request.body()  # already buffered and size-checked by BodySizeLimitMiddleware
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not body:
        raise RequestValidationError([{"type": "missing", "loc": ("body",), "msg": "Field required", "input": None}])
    if media_type != "application/json":
        raise RequestValidationError(
            [{"type": "model_attributes_type", "loc": ("body",),
              "msg": "Input should be a valid dictionary or object to extract fields from", "input": _text(body)}],
            body=_text(body),
        )  # fmt: skip
    try:
        # JSON Schema counts 33.0 as an integer; strict mode doesn't. The swipe schema has no
        # fractional fields, so integral floats are normalized to ints first.
        parsed = json.loads(body, parse_float=_integral_float_as_int)
        source = json.dumps(parsed).encode()
    except ValueError:
        parsed, source = body.decode("utf-8", "replace"), body  # invalid JSON: reported below
    try:
        batch = SwipeBatch.model_validate_json(source, strict=True)
    except ValidationError as e:
        # Inputs as text: FastAPI's error response would crash (500) on bytes that aren't UTF-8.
        errors = [
            {**error, "loc": ("body", *error["loc"]), "input": _text(error["input"])} if "input" in error
            else {**error, "loc": ("body", *error["loc"])}
            for error in e.errors()
        ]  # fmt: skip
        raise RequestValidationError(errors, body=parsed) from None
    if errors := _timestamp_range_errors(batch):
        raise RequestValidationError(errors, body=parsed)
    return batch


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.api_token or len(settings.api_token) < MIN_TOKEN_LENGTH:
        # Fail closed: a missing or weak token must never mean an unauthenticated API.
        raise RuntimeError(f"SWIPE_RSS_API_TOKEN must be set to at least {MIN_TOKEN_LENGTH} characters")
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Swipe RSS API",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(_reject_unknown_query_params)],
    )
    # The middleware added last runs first: token check, then size limit, then routing.
    app.add_middleware(BodySizeLimitMiddleware, limit=MAX_BODY_BYTES)
    app.add_middleware(BearerTokenMiddleware, token=settings.api_token)

    engine = make_engine(settings.db_path)

    @app.exception_handler(RequestValidationError)
    async def log_rejected_swipes(request: Request, exc: RequestValidationError):
        # A rejected swipe batch becomes a dead letter on the phone; log it so it is noticed
        # here too (PROJECT_PLAN.md §3 Swipe recording). Own data, single user: the body is logged,
        # only for authenticated requests (the middleware runs first) and capped in size.
        if request.url.path == "/swipes":
            body = exc.body if isinstance(exc.body, dict) else {}
            swipes = body.get("swipes") if isinstance(body.get("swipes"), list) else []
            ids = [s.get("swipe_id") for s in swipes if isinstance(s, dict)]
            body_text = json.dumps(exc.body, default=str)
            if len(body_text) > LOG_BODY_MAX_CHARS:
                body_text = f"{body_text[:LOG_BODY_MAX_CHARS]}… ({len(body_text)} chars, truncated)"
            # Errors without their "input": Pydantic repeats the offending input in every error,
            # which would multiply the body's size in the log line.
            errors = [{key: e[key] for key in ("type", "loc", "msg") if key in e} for e in exc.errors()]
            log.warning(
                "rejected swipe batch: swipe_ids=%s errors=%s body=%s",
                ids, json.dumps(errors, default=str), body_text,
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
    def post_swipes(batch: Annotated[SwipeBatch, Depends(_strict_swipe_batch)]) -> SwipeBatchResult:
        with Session(engine) as session, session.begin():
            result = record_swipes(session, batch.swipes)
        return SwipeBatchResult(stored=result.stored, duplicates=result.duplicates)

    @app.get("/saved", response_model=SavedListResponse)
    def get_saved() -> SavedListResponse:
        with Session(engine) as session, session.begin():
            return SavedListResponse(items=list_saved(session))

    @app.get("/saved/{feed_id}/{item_key}/content", response_model=SavedContent)
    def get_saved_content(feed_id: FeedIdPath, item_key: ItemKeyPath) -> SavedContent:
        with Session(engine) as session, session.begin():
            content = saved_content(session, feed_id, item_key)
        if content is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not saved")
        return content

    @app.get("/feeds", response_model=FeedListResponse)
    def get_feeds() -> FeedListResponse:
        try:
            feeds_file = load_feeds(settings.feeds_path)  # re-read per request, like the fetcher
        except FeedsFileError as e:
            log.error("feeds.toml is invalid: %s", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"feeds.toml is invalid: {e}"
            ) from e
        with Session(engine) as session, session.begin():
            return FeedListResponse(feeds=feed_health(session, feeds_file))

    return app
