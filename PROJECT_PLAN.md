# Swipe RSS Reader — Project Plan

> Working title; rename freely.

## Note to Claude Code

This plan was produced in a design discussion and has **not** been validated by implementation. You are explicitly allowed and encouraged to **ask questions or push back** if anything here looks wrong, risky, inconsistent, or sub-optimal — treat yourself as a second reviewer of the design, not just an implementer. When something is ambiguous or listed as open, **ask rather than assume**. When proposing alternatives, explain the reasoning and tradeoffs.

---

## 1. Description

A personal, single-user RSS reader made of two parts:

- **Backend** (Python, Docker) on a remote Hetzner server: fetches feeds on a schedule, stores items, serves an API, and later ranks items by predicted interest.
- **Android app**: presents headlines as swipeable cards (Tinder-style). Each card gets one of three actions: *never*, *save for later*, or *read now*.

All networking happens over **Tailscale** (WireGuard-based private mesh VPN). Nothing is exposed to the public internet.

## 2. Goals

- Fast triage of many headlines via swipe gestures.
- A read-later list for items worth reading.
- Every swipe logged as a training label, so a model can later learn personal interests and rank the queue.
- Visibility into feed health (detect broken or dead feeds).
- Minimal operational overhead: one user, one server, reproducible deployment from the repo.

### Non-goals (for now)

- Multi-user support.
- Feed management from the app (feeds are edited in a file on the server).
- Notifications.
- Public internet exposure or app-store distribution.

---

## 3. Decisions

### Infrastructure & networking

- **Host:** Hetzner remote server, running **Docker Compose**.
- **Network:** Tailscale on all machines (server, phone, dev machine). Public SSH is closed; all access is via the tailnet. Hetzner web console is the out-of-band fallback if Tailscale fails.
- **Tailscale runs on the host** (not as a sidecar container).
- **API exposure:** via **Tailscale Serve** (reverse proxy built into `tailscaled`, tailnet-only). The API container publishes on **loopback only** (`127.0.0.1:8001`; `8000` is taken by the existing MCP server), never on all interfaces or directly on the Tailscale interface. Serve terminates HTTPS and forwards to it: `tailscale serve --bg --https=8443 localhost:8001` (`--bg` persists across reboots). Port 8443, not 443: Serve binds its port on the Tailscale IP in the kernel, which prevents nginx (MCP server) from binding `0.0.0.0:443`.
  - Phone reaches the API at `https://my-first-server.porcupine-celsius.ts.net:8443`.
  - Loopback binding avoids a boot-order race with `tailscale0` and makes Docker's iptables bypass of host firewalls irrelevant for the API.
- **HTTPS:** MagicDNS certificates provisioned automatically by Serve (replaces the need for Caddy, a public domain, or Let's Encrypt). Requires MagicDNS and HTTPS enabled in the Tailscale admin console.
  - **Caveat:** certificates are recorded in public Certificate Transparency logs, so the machine name and tailnet name become publicly visible (not reachable). Use a non-sensitive machine name; consider a randomized tailnet name.
- Serve adds identity headers (e.g., `Tailscale-User-Login`) to proxied requests. Not used for auth: they can be spoofed if the API is ever reached without going through Serve.
- **API auth: static bearer token** on top of Tailscale (defense in depth against local processes on the server and misconfiguration such as Funnel or a `0.0.0.0` publish). Server: token in `.env`, constant-time comparison. App: token and server address in the git-ignored `android/secrets.properties` (template `secrets.properties.example`; decided 2026-10-11, user: a separate file rather than `local.properties`, which Android Studio generates) → `BuildConfig`, added by an OkHttp interceptor. The build fails if either is missing or invalid (token < 32 characters, address not `https://`), like the server without its token; CI writes dummy values. The token is readable in plain text from the APK, so the APK only goes from the desktop to the phone (never uploaded, not even from CI). Back it up alongside the keystore; rotation requires an app rebuild. Declared as a security scheme in the OpenAPI contract.
- **Auth is secure by default** (implemented 2026-10-03): the token check runs for every request however a route is registered (ASGI middleware before routing since 2026-10-05, see API conventions); only paths in an explicit `PUBLIC_PATHS` allowlist (`/health`) skip it. The API **fails closed**: it refuses to start if `SWIPE_RSS_API_TOKEN` is unset or shorter than 32 characters. 401 responses carry `WWW-Authenticate: Bearer`.
- **Machine name:** keep `my-first-server` (tailnet name is already randomized: `porcupine-celsius.ts.net`). The server is shared with other hobby services (e.g., Vikunja), so the name is intentionally not RSS-specific.
- **Multiple services on the host:** one **HTTPS port per service** via Serve (`:8443` → RSS API; a future Vikunja gets another port, e.g. `:9443`), not sub-paths. `:443` is unavailable to Serve while nginx needs `0.0.0.0:443`. Verified in stage 0: Serve accepts arbitrary HTTPS ports (443, 8443, 9443 tested), all with the same MagicDNS certificate. Each service in its own Compose project with its own Docker network and volumes; never mount the Docker socket into a container.
- **Existing public service (accepted exception):** nginx on public `:80`/`:443` fronts the owner's MCP server (`mcp-server.service`, `127.0.0.1:8000`, domain `mcp.ministryofpa.ws`) for a claude.ai connector. Unrelated to this project; kept installed, currently disabled while idle (re-enable command in findings.md). nginx binds `0.0.0.0:443`, which includes the Tailscale IP. Verified in stage 0 that Serve on `:443` blocks nginx from starting (bind conflict), so the RSS API uses Serve `:8443`.
- **Tailscale ACLs: default allow-all, kept deliberately** (decided in stage 0). The owner controls which devices join the tailnet. Accepted consequence: the extraction SSRF guard is the only barrier between the server and other tailnet devices, so it must refuse the Tailscale ranges explicitly, both IPv4 `100.64.0.0/10` and IPv6 `fd7a:115c:a1e0::/48`. Revisit if a less trusted device joins.

### Repository

- **Monorepo** containing backend, Android app, and the API contract.
- The **OpenAPI spec** file in the repo is the single source of truth for the backend ↔ app interface.
- Spec: **`api/openapi.yaml`** (OpenAPI 3.1), **hand-written first**; code follows it. Backend drift is caught by **contract tests** in pytest: (1) the app's set of (path, method) pairs equals the spec's; (2) must-fail and status-code tests per endpoint; (3) **contract coverage** (implemented 2026-10-04): the test client records every (method, spec path, status) it receives, and after a full green run the recorded set must equal the spec's documented responses (nothing documented untested, nothing undocumented returned). Request/response *shapes* come from models generated from the spec (below), so per-response shape validation (e.g., `openapi-core`) is largely redundant; the full test strategy is in `backend/tests/README.md`. No exact comparison with FastAPI's generated spec (brittle). FastAPI's own `/openapi.json` and `/docs` are disabled or serve the YAML, so no second divergent spec is exposed. Behavior the spec can't express (idempotency, queue ordering, swipe flag, auth) is covered by ordinary tests derived from this plan.
- **API (wire) models are generated from the spec** with `datamodel-code-generator` (dev-only dependency, version pinned in `uv.lock`, formatter set explicitly) into the committed file `backend/src/swipe_rss/api_models.py` (header: generated, do not edit). Generator options live in `[tool.datamodel-codegen]` in `backend/pyproject.toml`; regenerate with `cd backend && uv run datamodel-codegen`. A **freshness test** (`tests/test_api_models.py`) runs the generator's own `--check` and fails if the committed file is stale. Custom validation lives in subclasses or handlers, never in the generated file. SQLAlchemy models stay separate (storage shape may differ from wire shape). Generation guarantees the *models* match the spec; it does not replace tests for routes, status codes, auth, error handling, wiring of models to routes, or invalid input (see the testing-coverage discussion).
- **API conventions** (decided 2026-10-03):
  - `GET /health` is the only unauthenticated endpoint: fixed `{"status": "ok"}`, no DB access, never extended with versions or stats.
  - List responses are wrapped in an object (`{"items": [...]}`, `{"feeds": [...]}`) so fields can be added without breaking clients.
  - `GET /feeds` returns `503` with the reason when `feeds.toml` is invalid (the fetcher stops then too); the file is re-read on every request.
  - Path parameters (`feed_id`, `item_key`) are validated with the patterns read from the generated `Card` model, so they follow the spec without being copied.
  - `GET /saved/{feed_id}/{item_key}/content` returns `200` for any saved entry; the client branches on `extraction_status`, `text` is null until `done`; `404` only if not saved.
  - Fields are **required but nullable**: every field is always present, `null` when there is no value. A forgotten field is a `422`, not a silently missing value.
  - **New request fields must be optional** (and old ones never tightened), so older app versions and their queued swipes keep working.
  - **Requests the spec doesn't describe fail** (decided 2026-10-04, stated in the spec's `info.description` and tested): with a valid token, unknown path → `404`; method not listed for a path → `405` with an `Allow` header; **unknown query parameter → `422`** except on `/health` (a typo like `?limt=5` must not silently return the default). The query check is an app-level dependency, so new routes are strict automatically. Introduced before any client existed, a one-time exception to "never tightened" (swipe bodies were unaffected).
  - **Token first, then size, then parsing** (decided 2026-10-05): the token check is ASGI middleware before routing (a FastAPI dependency ran too late: FastAPI parses JSON bodies before dependencies, so an unauthenticated broken body got `422` and was read and logged). Without a valid token every path except `/health` → `401`, unknown paths and wrong methods included, and no body byte is read. Then bodies over **10 MB → `413`** (largest legitimate batch ≈ 9 MB), checked on `Content-Length` and while streaming.
  - **Swipe bodies are validated against the spec's JSON types** (strict JSON mode, decided 2026-10-05 after Schemathesis findings): a number is not a timestamp and `"180"` is not an integer (FastAPI's default lax mode converted both). Integral floats like `180.0` count as integers, as in JSON Schema. Integer fields have maxima (`app_version` ≤ 2147483647, Android's 32-bit `versionCode`; `time_to_swipe_ms` ≤ 2⁶³−1, SQLite's INTEGER), so no value can crash the insert. **Request timestamps lie in 1970-01-01T00:00:00Z ≤ t < 3000-01-01T00:00:00Z** (decided 2026-10-05): outside it, a valid RFC 3339 value can leave Python's datetime range when converted to UTC (`0001-01-01T00:00:00+00:01` → year 0 → 500). JSON Schema can't express date ranges (a `pattern` breaks the generated models), so the spec states it in the field descriptions and `swipe_rss/timestamps.py` enforces it: Pydantic `ge`/`lt` in the API (422), and the fetcher stores a feed date outside it as null, so every served card stays swipeable.

### Workflow

- Hosted on **GitHub**. Development (backend and Android) happens on the desktop; the server only runs committed code.
- The repo is **public**, so the server clones and pulls **anonymously over HTTPS** (no deploy key, no credentials on the server). If the repo ever goes private, switch to a read-only deploy key.
- Deploy: `git pull && docker compose up -d --build`. Claude may run exactly this plus read-only checks over Tailscale SSH, after the owner approves each deploy; all other server changes are done by the owner. Dependencies are installed inside the image build, pinned by `uv.lock`. The image does not inherit the checkout's file modes (`COPY --chmod=a+rX`), since containers run as a non-root user and a checkout under a strict umask would otherwise break them. Later option: build images in CI, push to GHCR, and have the server pull images only.
- **Secrets never in git:** `.env` (commit `.env.example`), rclone/restic config and passphrase, Android signing keystore, `android/secrets.properties` (API token), `local.properties` (SDK path).
- The Android app is built on the desktop (and in CI); the server ignores `android/`.

### Backend

- **Python** with **FastAPI** (async web framework, auto-generated OpenAPI docs), run by **Uvicorn** (ASGI server).
- **Pydantic** for request/response validation.
- **httpx** (async HTTP client) + **feedparser** (tolerant RSS/Atom parser). Fetching uses **conditional GET** (`ETag` / `If-Modified-Since`).
- **SQLAlchemy 2.x** (ORM, **sync**) + **Alembic** (schema migrations; batch mode for SQLite). API endpoints that touch the DB are plain `def` (FastAPI runs them in its thread pool); aiosqlite would only wrap the sync driver in a thread, so async gains nothing with SQLite.
- **trafilatura** for article text extraction.
- Tooling: **uv** (package manager with lockfile), **pytest**, **ruff**, **Schemathesis** (property-based API tests generated from the spec), **CI** on GitHub Actions for every push. Mutation testing checks the tests themselves: a curated list of mutants for the guard tests (`backend/scripts/mutants.py`) and **mutmut** for discovering untested code (`backend/scripts/run_mutmut.py`, an exploration tool, not a gate). See `backend/tests/README.md`.

### Database

- **SQLite**, single user. Settings: **WAL mode** (concurrent reads during writes), **busy_timeout** (writers wait rather than fail), **STRICT tables** (enforced column types).
- Shared between the fetcher and API containers via a **named Docker volume** on the same host. Never on a network filesystem.
- **All transactions start with `BEGIN IMMEDIATE`.** Deferred transactions that upgrade from read to write fail immediately with `SQLITE_BUSY_SNAPSHOT` in WAL mode if another process wrote in between, and `busy_timeout` cannot help. IMMEDIATE takes the write lock up front, so `busy_timeout` applies. Implemented via SQLAlchemy's documented pysqlite recipe: `isolation_level = None` on connect, emit `BEGIN IMMEDIATE` in a `begin` event listener. Applied to all transactions (not only writes) for simplicity; contention cost is negligible at this scale.
- **Never hold a transaction across network I/O.** Fetcher: fetch and parse each feed outside any transaction, then write that feed's results in one short transaction. Same for background extraction.
- Future embeddings: brute-force cosine similarity in Python is expected to be sufficient at this scale. Benchmark before adding a vector extension.

### Scheduling

- **Dedicated scheduler container** (supercronic) runs the fetcher **every 15 minutes** (`backend/crontab`). A one-shot **`migrate`** Compose service runs `alembic upgrade head` first; other services start only after it completes, so migrations never race. This keeps scheduling inside Compose and isolates fetch failures from the API. The **`api`** service (same image) runs uvicorn, published on `127.0.0.1:8001` only, with a Docker health check on `/health`. Only `api` reads `.env` (the token); `.env` is optional for Compose, so a missing token stops just the API (it fails closed) while fetching and extraction keep running.
- The same container runs the **extraction job** every minute (see Content extraction) and the **pruning job** hourly (see Retention).

### Feeds

- Defined in **`config/feeds.toml`, committed to the repo**. The `config/` **directory** is bind-mounted read-only from the server's checkout into the containers and the file is re-read on every fetch run, so `git pull` applies edits without rebuild or restart. (A single-file bind mount would go stale: `git pull` replaces the file with a new inode.)
- **TOML**, read with stdlib `tomllib`, validated with Pydantic.
- Each feed has a **required, stable `id` slug** (never changes; at most 100 characters, the API's `FeedId` limit), plus `url` and optional `name`. The `id` (not the URL) keys `feed_status` and is the feed identifier stored in the swipe log, so a feed's URL can change without orphaning its history.
- `[defaults]` table for global settings; per-feed overrides (e.g., `retention_hours`) live on the feed entry later.
- **`max_item_age_hours`** (in `[defaults]`, currently 24): the fetcher skips entries published longer ago than this; entries without a date use fetch time. Not tombstoned. Introduced to keep the initial backlog out; **kept at 24 after the retention review** (2026-10-06, user). Known trade-off: after a fetch outage longer than 24 h, articles published during it are skipped for good.
- **Invalid file → the fetch run aborts and logs a clear error.** A typo must never be interpreted as "all feeds removed".
- Initial feed list: converted from the owner's Markdown list (`tech_privacy_rss_feeds.md`).

### Deduplication

- Scope is **per feed**: identity is `(feed_id, key)`. The same article in two feeds appears twice; revisit only if this proves annoying (global dedup on normalized link would only catch identical URLs and muddies the per-feed ML signal). First use on the phone (2026-10-13) showed such pairs from overlapping Wired and Ars Technica category feeds (the same article swiped twice); to be handled later (stage 8, user).
- `key` is the first available of:
  1. Entry GUID/ID (feedparser `entry.id`).
  2. Normalized link: lowercase scheme and host, drop `#fragment`, strip `utm_*` and similar tracking parameters.
  3. Hash of title + publish date.
- Stored as a SHA-256 hash with a prefix recording which rule produced it (`guid:`, `link:`, `hash:`), for debuggability.
- **Per-feed override** in `feeds.toml`: `dedup = "link"` forces link-based keys for feeds with unstable GUIDs.
- Keys are written to the tombstones table **at first sight** (insert time), not at prune time; dedup checks only that table. This avoids a window between pruning and tombstoning.
- **First version wins:** if a feed edits an entry but keeps its key, the update is ignored.

### Swipe semantics (training labels)

| Action | Label | Proposed gesture |
|---|---|---|
| Never | negative | swipe left |
| Save for later | positive | swipe right |
| Read now | positive (possibly stronger) | swipe up |

- All three actions are **logged distinctly**, even if initially treated as equal positives.
- **No tap for read now** (decided 2026-10-13, user): an accidental tap would record the strongest label and open a page. Read now needs an `http`/`https` link; on a card without one the up-swipe springs back.
- Items that **expire unswiped are unlabeled**, not negatives.

### Swipe log fields

Rule: **item properties are captured at swipe time** (items are pruned, so anything not copied is lost forever). **Ranking-context properties** (model score, model version, exploration flag) are added later as nullable columns when ranking exists; before ranking everything is round-robin, so nothing needs backfilling.

| Field | Purpose |
|---|---|
| `swipe_id` | Client-generated UUID identifying one swipe *event*; idempotency for the offline queue (a retried send of the same swipe is ignored). |
| `action` | never / save / read now. |
| `feed_id` | Stable feed slug; likely the strongest single feature. |
| `item_key` | Identifies the *article*: together with `feed_id` it is the item's dedup key (`items.dedup_key`). Groups multiple swipes on the same article (e.g., undo, then a new swipe) so "latest swipe per item wins". |
| `headline` | Permanent copy. |
| `summary` | Permanent copy, **plain text** (HTML is stripped at ingest, so items are already plain text when served). |
| `link` | Normalized; enables re-fetching full text later (embeddings) and link domain as a feature. |
| `published_at` | Article age at swipe time. |
| `tags`, `author` | `tags`: array of strings, possibly empty (stored as JSON); `author`: nullable. Cheap features when the feed provides them. |
| `swiped_at` | Client time; the real event time (may precede receipt by days when offline). |
| `received_at` | Server time; audit. |
| `time_to_swipe_ms` | Nullable; how long the card was on screen before the swipe (implicit interest signal). |
| `fetched_at` | When the item entered the queue (copied from the queue response). Article-age fallback when `published_at` is missing; also time-in-queue. |
| `tz_offset_minutes` | Phone's UTC offset at swipe time; `swiped_at` is stored in UTC, so this keeps local time of day (reading habits) recoverable. |
| `app_version` | App `versionCode`. Separates labels from before/after app changes (e.g., gesture or meaning of an action). |

- **Completeness check (2026-10-02):** everything else stage 7/8 needs is in the log or derivable (session effects from `swiped_at` ordering; link domain from `link`). Impressions are not logged: in a swipe UI nearly every displayed card ends in a swipe, and expired cards are unlabeled by design.
- **Extracted full text is deliberately not a training feature.** Only positives (save / read now) get extracted, so training on it would leak the label. Train on fields available uniformly for all labels.

### Swipe recording

- **Idempotency:** the phone generates a `swipe_id` (UUIDv4) at swipe time and stores it with the swipe in the Room queue. Server: `swipe_id` is the primary key; inserts use `ON CONFLICT(swipe_id) DO NOTHING`; the response is success whether new or duplicate, so retries are always safe.
- **Endpoint:** a single batch `POST /swipes` accepting 1..N swipes (the phone sends whatever is pending: one swipe, or a backlog after time offline). Transport failures (no response, timeout) are resolved by resending the whole batch; validation failures follow "Invalid swipes" below.
- **Invalid swipes (decided 2026-10-03):** the server validates the batch as a whole (`422` rejects all of it). On `422`, the phone resends that batch one swipe at a time; a swipe that fails alone becomes a local **dead letter** (kept, not resent automatically; since 2026-10-12 a state of the phone's `outbox` table, see §3 Android), so one bad swipe never blocks the queue. Alternatives considered: per-swipe results in a `200` (loose request schema), one swipe per request (many requests).
- **Dead letters are visible and recoverable:**
  - *Stage 2:* the server logs every `422` on `POST /swipes` with the `swipe_id`s, the validation errors and the request body (own data, single user; authenticated requests only; body capped at 10,000 characters, since the phone's single-swipe resends are logged whole), so rejections show up in `docker compose logs` even if the phone never reports them.
  - *Stage 5* (implemented 2026-10-13): each dead letter is stored with the `app_version` that failed and its error (`HTTP 422: ` plus the server's answer, up to 2,000 characters). On the first sync after a new app version starts, its older dead letters are retried once (a release that fixes the cause may first repair stored swipes). The swipe screen shows "N swipes couldn't be sent" with a **Retry** button (retries all of them). No debug screen and no export (decided 2026-10-13, user): the server log already has every rejected body with its errors. Resending is always safe: dead letters keep their original `swipe_id`.
  - *Stage 8, only if dead letters actually occur:* a deliberately lenient `POST /dead-letters` (any JSON up to a size limit, stored as-is) plus a server command to repair and import them into `swipes`.
- **Swipe recording details** (implemented 2026-10-04): one transaction per batch; a duplicate `swipe_id` is skipped and the rest of the batch still processed; a swipe for an item that is already gone is stored normally; `items.swiped_at` keeps the first swipe's time; a later non-save swipe does not remove an existing saved entry (undo is stage 8).
- **Never tighten request validation without considering swipes already queued on phones:** a stricter rule turns them into dead letters. To tighten a limit, lower the ingest cap first, the request limit later.
- **Append-only log:** no per-item uniqueness. For training, the **latest swipe per item wins**. Undo (stage 8) becomes another event, not a schema change.
- **Card snapshot:** each swipe carries the item fields the phone received from the queue (`feed_id`, `item_key`, headline, summary, link, `published_at`, `fetched_at`, tags, author); the server stores them as given (Pydantic-validated, length-limited). This makes swipes independent of item pruning (offline for any duration) and makes the label pair with exactly what was displayed. If the item still exists (looked up by `(feed_id, item_key)`), the server also removes it from the queue by setting `items.swiped_at` (a flag, not a delete); the queue serves only rows with `swiped_at IS NULL`, and the pruning job deletes swiped rows later. Flagging keeps stage 8 undo simple: clearing the flag returns the card to the queue, whereas a deleted item could never come back (its key is tombstoned).
- **Wire format nests the card** (decided 2026-10-03): a swipe is `{swipe_id, action, swiped_at, tz_offset_minutes, time_to_swipe_ms, app_version, card: {…}}`, where `card` is exactly the object received from `/queue`. One `Card` schema serves both endpoints, so its limits exist once. The `swipes` table stays flat; the server maps the fields when storing. Rejected: flat JSON (card fields defined twice, limits could drift), `allOf` composition (needs `unevaluatedProperties`, generator support unverified).
- **Card length limits are enforced at ingest, equal to the spec's limits** (decided 2026-10-03): headline 1000, summary 2000, author 500, tags ≤ 50 × 200, link 4096 chars. The fetcher truncates headline/author/tags (with `…`, like summary) and stores `null` for an oversized link (a truncated URL is broken). Every card the queue serves therefore passes `POST /swipes` validation; otherwise an over-long field would make a swipe permanently unrecordable. The dedup key is still computed from the full title and link, so an item's identity never depends on the card limits. Tests assert fetcher limits ≤ the generated models' limits and that an oversized entry yields a valid `Card`. Migration `0002` applies the same caps to rows stored before this rule. Alternatives considered: no field limits (rows unbounded), truncate on store (stored ≠ displayed), skip oversized items (silent loss), loose limits only (failure rarer, not impossible).
- **Item identity is `(feed_id, item_key)`**, where `item_key` is the item's dedup key, exposed in the queue response under that name. Chosen over `items.id`: the rowid can be reused after pruning, which would silently merge unrelated articles in the permanent swipe log; the dedup key is derived from the article and stays meaningful after the item is gone. Known limitation: if a feed's dedup rule changes (e.g., a `dedup = "link"` override is added, or the feed's GUIDs change), the same article gets a new key and counts as a different item.

### Content extraction

- **Read now:** no extraction. The app opens the original page directly in **Custom Tabs** (online by definition; page exists now). An in-app reader for read-now items is a possible later addition.
- **Save for later:** extracted **shortly after save**, so saved items survive pages disappearing, changing, or going behind a paywall within the two-week window.
  - The swipe is recorded immediately; extraction never runs inside the request.
  - **One `saved` row per article:** primary key `(feed_id, item_key)`; a repeated save of the same article (`ON CONFLICT DO NOTHING`) adds nothing. The row references the save swipe (`swipe_id` → `swipes`), and headline, link etc. come from that swipe's snapshot rather than being copied, so saving works even if the `items` row is already pruned (late offline sync).
  - **DB as queue:** `saved` rows carry `extraction_status` (pending / done / failed), `attempts`, `last_error`, `next_attempt_at` (schedules the backoff; `NULL` = due now). A scheduler-container job runs **every minute** and processes pending rows that are due.
  - **Retries:** up to 3 attempts with increasing backoff, then `failed`. The app shows "couldn't extract — open original" and falls back to Custom Tabs.
  - Known limitation: trafilatura can't reliably detect paywalls; a paywalled page may "succeed" with teaser text only.
- **SSRF guard** (extraction fetches URLs from third-party feed content and client snapshots): allow only `http`/`https`; resolve DNS and refuse loopback, private, link-local, and Tailscale (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) addresses, re-checking after every redirect; enforce a timeout and a max response size.
  - **Implementation** (2026-10-04, `safe_fetch.py`): the check runs where the socket is opened (a custom httpcore network backend): the hostname is resolved once, **every** resolved address must be public, and the socket connects to that checked address, so there is no second DNS lookup (no DNS-rebinding window); TLS still verifies against the hostname. "Public" = `is_global` plus explicit multicast/reserved/loopback/link-local/private/unspecified checks (Python counts multicast as global), IPv4-mapped IPv6 unwrapped, Tailscale/NAT64/6to4 listed explicitly. No proxies. Manual redirects (max 5). Limits: connect 10 s, read 15 s, 30 s overall, 5 MB, HTML only, `Accept-Encoding: identity`.
  - **Job** (`extraction.py`, `swipe-rss extract` every minute): claims up to 5 due `pending` rows in one transaction (attempt counted at claim time, 10-minute lease against overlapping runs), fetches and extracts outside any transaction, stores the result per row. Temporary failures retry after 5, then 30 minutes; the 3rd failure, or any permanent one (no link, blocked address, 4xx except 408/429, not HTML, no article text), sets `failed`. Extracted text capped at 500,000 characters.

### Retention

- Unswiped items: **2 days** (may later be tuned per feed), counted from **`fetched_at`** (decided 2026-10-06: the rule giving the longest time; `published_at` is earlier or missing).
- Swiped items (`items.swiped_at` set): **same rule as unswiped, 2 days from `fetched_at`** (decided 2026-10-06; rejected: delete at the next run). One rule for all items, and Stage 8's undo (clear the flag) keeps working until the item expires anyway; the cost is a little disk space.
- Saved items: **2 weeks**, counted from the save swipe's **`received_at`** (server clock; for a late offline sync it is later than the phone's `swiped_at`), read or unread alike (decided 2026-10-06).
- **Tombstones:** seen dedup keys (see Deduplication) are **kept forever** (decided 2026-10-06; the plan said ~90 days), so pruned items are never re-inserted as new. Written at first sight, never pruned. Cost: ~60 rows/day ≈ 3 MB/year; lookups are primary-key lookups. An expired tombstone would only have mattered for an entry still in the feed that also passes the age filter (undated, future-dated, or with `max_item_age_hours` lifted). Expiry can be added later if ever needed; a deleted tombstone can't be recovered.
- **Swipe log is kept permanently** and stores the item's headline and summary text itself, not only a reference to an item that will be pruned.
- Items of a feed removed from `feeds.toml` expire like all items (2 days after fetch); its tombstones and `feed_status` row stay (`GET /feeds` lists only feeds in the file). This settles the former open decision "what happens to items of a removed feed" with its default.
- **Pruning job** (decided 2026-10-06): `swipe-rss prune` in the scheduler container, **hourly** at minute 7 (away from the fetch runs); one transaction per run (steady state ≈ 3 rows per run); no `VACUUM` (SQLite reuses freed pages, so the file stops growing at steady state); one INFO line per run with the counts; `--dry-run` counts without deleting (for the first deploy); `now` passed in (server UTC), so the rules are testable with a fixed clock. Built test-first.
- **Swipes can never be deleted or changed**, three layers (decided 2026-10-06): a database trigger (migration `0004`) rejects every `DELETE` and `UPDATE` on `swipes`, whatever the code path (including a manual `sqlite3` session or a future job); a test that prunes a database of old swipes and checks they all survive; a curated mutant that adds a swipe delete to the job, which that test must kill. An append-only log needs neither operation; undo (stage 8) is a new event.

### Queue ordering (pre-ML)

- **Round-robin across feeds, oldest-first within each feed.** This gives fair exposure, and items are seen before they expire. It will be replaced by ML ranking later.
- **Round-robin details** (implemented 2026-10-04): each feed's items are ranked by age (`published_at`, or `fetched_at` when the feed gives no date); the queue takes every feed's rank 1, then rank 2, and so on; within a round, older items first. A row that is not a valid `Card` is skipped and logged instead of failing the whole queue.
- **Queue endpoint semantics:** returns up to `limit` unswiped items. It is stateless: the server doesn't track what the phone already holds, so items fetched earlier but not yet swiped (or whose swipes haven't synced) are returned again; the phone deduplicates by `(feed_id, item_key)`.

### Observability

- Log every failed fetch.
- **Logs** (decided 2026-10-05): level from `SWIPE_RSS_LOG_LEVEL` (default INFO) for the jobs and the API; Docker log rotation in Compose (json-file, 3 × 10 MB per container; the default never deletes); health checks are not written to the access log (they were 99.8% of its lines). Normal volume measured on the server: about 1.3 MB/day.
- Per feed, store and expose: **last successful fetch** and **last new item**. The app displays both, to distinguish a broken fetcher from a source that went quiet.

### Backups

Two layers (decided 2026-10-06):

- **Layer 1, on the server (stage 4):** `swipe-rss backup`, hourly at minute 37 in the scheduler container, writes a consistent snapshot of the database (`VACUUM INTO`; never a plain copy of the live WAL database) into **`/home/srub/swipe-rss-backups` on the host**: outside the repo and outside every Docker volume, so `docker compose down -v` or a volume prune (as in the deliberate reset of 2026-10-04) can't take it along. Rotation: every snapshot of the last 48 hours, plus the midnight snapshot of each day for 14 days. Covers bugs, bad migrations and volume loss; **not** server loss, server compromise or an account problem. Each snapshot is **checked by opening it as a separate, read-only database and querying it** (integrity check, migration version, row count of every table); the log line `backup succeeded: …` carries the counts; production is only read, by the backup itself. **Restore is manual** (README; decided 2026-10-06, user: keep it simple; losing up to an hour of swipes on a restore is acceptable for now).
- **Layer 2, off the server (pinned, last stage):** destination open; the owner is checking Hetzner's options (Cloud Backups, Storage Box). Considered so far: Google Drive via restic + rclone (needs an own Google OAuth client, published, since the shared rclone client is being retired in 2026 and test-mode grants expire weekly); the owner's desktop via restic `rest-server` in append-only mode over the tailnet (desktop must be on; a compromised server can't delete backups); email (rejected: whole-account SMTP credentials on the server, 25 MB limit, manual retention). Tool leaning: restic (snapshots, retention, `restic check`).

### ML ranking (later stage)

- Start with **scikit-learn**: `TfidfVectorizer` + `LogisticRegression`, model persisted with **joblib**. Runs server-side.
- Reserve a small **exploration** percentage of unranked items in the queue, to avoid filter bubbles.
- Later option: sentence embeddings (sentence-transformers; heavier, pulls in PyTorch, so check server resources).

### Android

- **Kotlin** + **Jetpack Compose**.
- **Retrofit + OkHttp** (HTTP), **kotlinx.serialization** (JSON).
- **Room** for the local cache and an offline queue of unsent swipes. Schema (decided 2026-10-12, user; `db/LocalStore.kt`): `cards` (the stack: `(feed_id, item_key)` key, `position`, `card_json`) and `outbox` (`swipe_id` key, `feed_id`, `item_key`, `swipe_json`, `dead_error`, `dead_app_version`). Cards and swipes are stored as the API's JSON plus only the columns queries need: a swipe is resent exactly as recorded, and API model changes need no Room migration (rejected: one column per field, which needs mapping code both ways and a migration per model change). Dead letters are a state of `outbox` (`dead_error` set), not a second table: moving and retrying are one `UPDATE` each, like the backend's `saved.extraction_status`. A swipe deletes its card and queues itself in one transaction. Cards from `/queue` already on the stack or in `outbox` (pending or dead) are skipped. On the first sync after app start, dead letters from older app versions get one more try; one that fails again carries the new version and waits for the next release. The schema is exported to `android/app/schemas/` (reviewable in git, the base for migrations). Version 1 changes freely until the first release APK; migrations start from there. Never `fallbackToDestructiveMigration()`: it would silently delete unsent swipes.
- **Swipe sync only while the app is in the foreground** (decided 2026-10-08): pending swipes are sent when the app comes to the foreground and a few seconds after the last swipe (debounced), from a coroutine tied to the screen's lifecycle. On failure (no network, Tailscale off, `5xx`) nothing is done: the swipes stay in Room until the next trigger. A request cancelled by leaving the app is harmless (swipes are deleted only after a `200`; `swipe_id` makes resends safe). Rejected: **WorkManager** background sync. It adds no durability (Room provides that) and only shortens the delay until the server sees swipes, which doesn't matter for one user (ranking uses accumulated history); it would cost a dependency, a Worker, constraint/backoff setup and its own tests. Trade-off: swipes from a session that ends offline reach the server at the next app start.
- **Swipe sync** (implemented 2026-10-13, `swipe/SwipeSync.kt`): the outbox is sent as stored, byte for byte (the swipe JSON is joined into a `{"swipes": [...]}` body, not decoded and encoded again), in batches of up to 500 (the spec's maximum). A batch leaves the outbox only after a `200`. On `422` that batch is resent one swipe at a time and a swipe rejected alone becomes a dead letter; any other failure (no connection, `5xx`, `401`) stops sending and keeps everything. Triggers: return to the foreground (sync, then refill, so the server already has the swipes when it is asked for cards) and 5 seconds after the last swipe (each swipe restarts the wait; also followed by a refill, for when the last refill found only cards whose swipes were still waiting, e.g. after many swipes piled up offline). Refill and sync never overlap (one `Mutex` in `SwipeRepository`): a `/queue` answer the server built before it got a swipe must not arrive after sync has removed that swipe from the outbox, or the swiped card would come back. The screen shows "N swipes not sent yet" only after a sync has failed (pending swipes are normal for the 5 seconds before sending).
- **Swipe screen** (implemented 2026-10-13, `swipe/`): `SwipeRepository` (plain Kotlin: refills the stack from `GET /queue?limit=50` when fewer than 10 cards are left, records swipes into `outbox`), `SwipeViewModel` (state for the screen, one refill at a time, refill errors as one line of text), `SwipeScreen` (Compose). The gesture is Compose's own drag handling, no swipe-card library: a card counts when dragged past a third of the screen's width (left, right) or height (up), or flicked faster than 1000 dp/s in the direction it was dragged; the decision is a pure function with JVM tests. `time_to_swipe_ms` runs on the monotonic clock from the moment a card becomes the top card and restarts when the app returns to the foreground (time in another app doesn't count). `swiped_at` is the phone's local time with its offset, to the millisecond; `tz_offset_minutes` comes from the same clock reading. The database and HTTP client are created once in an `Application` class (`SwipeRssApp`), by hand. Cards show `feed_id`; feed names come with the feed-status screen.
- **Coil** for images (if feeds provide thumbnails).
- **Custom Tabs** for opening articles (`androidx.browser`, used by read now since 2026-10-13).
- Dependency injection (Hilt) skipped for the MVP.
- **Distribution:** sideloaded release APK signed with a personal key, installed with adb from the desktop, over USB or wireless debugging on the home Wi-Fi (decided 2026-10-11, user: installing from anywhere over Tailscale or downloading from the server would add machinery for no benefit, since the app only changes right after coding at the desk; Tailscale is still how the app reaches the API). Increment `versionCode` each release. **Back up the keystore.** Losing it forces an uninstall to update.
- **minSdk:** set to the owner's phone Android version: **36** (Pixel 10a on Android 16, decided 2026-10-09).
- **applicationId and package:** **`io.github.shanirub.swiperss`** (decided 2026-10-09). Permanent: Android treats a different id as a different app, so changing it means uninstalling and losing local data (unsent swipes).
- **API models are hand-written** (decided 2026-10-09): `@Serializable` Kotlin data classes for the schemas the app uses, not generated. The spec is small (17 schemas, about 10 needed in stage 5) and changes rarely; a generator would add a build tool whose OpenAPI 3.1 nullable support is unverified. Drift is caught by an **Android conformance test** (JVM unit test, Key Question 11 h): JSON the app sends (`SwipeBatch`) is validated against the spec's schemas, and the app's classes must decode spec-valid responses (including `null` in every nullable field). Implemented 2026-10-12 as `SpecConformanceTest.kt` with the networknt JSON Schema validator (test-only), format checks on; which fields are tested as `null` is read from the spec, not from hand-written samples, so a field made nullable later is covered. The spec is a Gradle test input, so editing it re-runs the test. Details in `android/app/src/test/README.md`.
- **Android CI** (decided 2026-10-09): one GitHub Actions job builds the app and runs the JVM unit tests (no emulator), in its own workflow file (`.github/workflows/android.yml`) triggered only by changes to `android/**`, `api/openapi.yaml` (the conformance test reads it) or the workflow itself, so backend-only pushes don't wait for a Gradle build. Added 2026-10-11, right after the Gradle project setup. `main` has no branch protection, so a skipped workflow never blocks a merge.
- **Tests are JVM unit tests only** (decided 2026-10-11): no instrumented (on-device) tests or emulator, in CI or locally, so the instrumented-test setup was removed from the generated project. If a test later needs a device, the setup is a few lines to restore. Room does not (checked 2026-10-12): its tests use an in-memory database on the bundled SQLite driver (`sqlite-bundled-jvm`, test-only, its own SQLite for the desktop); the app keeps Android's SQLite. Fallback if a Room update breaks this: Robolectric.
- **Android Auto Backup is off** (`allowBackup="false"`, decided 2026-10-11): the phone holds only a cache (cards, unsynced swipes) and the server is the source of truth, so a cloud copy adds nothing and keeps reading data out of Google's backup. Cost: swipes not yet synced are lost on a phone switch.
- **kotlinx.serialization must send nulls and defaults**: by default it omits fields equal to their default, which would drop required-but-nullable fields and get swipes rejected with `422`. Done in the API client (2026-10-11): `encodeDefaults = true`, `explicitNulls = true`, no default values on API fields, and a test that every key is sent. Responses are read with `ignoreUnknownKeys = true`, so fields the server adds later don't break older app versions.
- **Mutation testing by hand only** (decided 2026-10-11, user): each new test is checked by a few deliberate breaks of the code it guards (like the backend's curated mutants, but without a script). No generated-mutant tool: PIT's Android Gradle plugin has no release with AGP 9 support yet and PIT mutates bytecode the Kotlin compiler generates (noise); Arcmutate, which filters that, is commercial; mutflow is too young (see §7). The app's code is thin, so a tool would cost more than it finds.

### Notifications

- None for now.

---

## 4. Data model (high level)

- **feed_status:** per-feed fetch state, conditional-GET headers, last successful fetch, last new item, last error.
- **items:** fetched articles (subject to retention); nullable `swiped_at` flags swiped items, which leave the queue but stay until pruned.
- **swipes:** permanent, append-only swipe log (DELETE and UPDATE rejected by triggers, migration `0004`); see "Swipe log fields" in §3. Item identity `(feed_id, item_key)` (indexed). `action` and `saved.extraction_status` are limited by CHECK constraints.
- **saved:** read-later entries, one per `(feed_id, item_key)`, referencing the save swipe (`swipe_id`, foreign key); extracted content (`text`, `extracted_at`) and extraction state (`extraction_status`, `attempts`, `last_error`, `next_attempt_at`), nullable `read_at`.
- **tombstones:** seen `(feed_id, key)` dedup keys with first-seen timestamps, written at insert time, kept forever.

Feed definitions themselves live in the feeds file, not the database.

---

## 5. Stages

Each stage should be usable and testable before the next. Stages 0–4 are backend-only and testable with `curl` over the tailnet.

Stages are **vertical slices**: each stage adds the tables it needs via a new Alembic migration, next to the code that uses them.

0. **Server foundation:** Docker, Tailscale Serve, firewall verification (no public ports), automatic security updates, repo clone on the server.
1. **Ingest:** SQLite settings, Alembic baseline, `feed_status` / `items` / `tombstones` tables. Fetcher: feeds file → fetch (conditional GET) → parse → deduplicate (incl. tombstones) → store. Run by the scheduler container. Testable by running a fetch and inspecting the DB with `sqlite3`.
2. **API:** OpenAPI contract first, then `swipes` / `saved` tables (new migration) and endpoints for: swipe queue, recording swipes, saved list, extracted content, per-feed status. Includes the `api` Compose service (published on `127.0.0.1:8001`) so the stage is testable with `curl` over the tailnet; stage 4 completes the rest of the Compose setup.
3. **Retention:** pruning job (unswiped and swiped items, saved items; tombstones are kept). Comes after the API because its rules depend on swipe and save state.
4. **Deployment & on-server backups:** full Compose setup, hourly snapshots outside the Docker volume (layer 1, see Backups).
5. **Android MVP:** swipe cards against the API, Room cache, offline swipe queue synced while the app is in the foreground (single-swipe fallback on `422`, dead-letter store with retry on app update and a Retry button), feed-status screen.
6. **Read-later view:** saved list, extracted-content reader, Custom Tabs fallback, permanent log of reads.
7. **Ranking:** TF-IDF + logistic regression on the swipe log, queue ordering by predicted interest, exploration slice.
8. **Iterate:** embeddings, per-feed retention tuning, undo, UX polish, the same article in several feeds (see Deduplication); lenient dead-letter upload if needed.
9. **Off-server backups** (pinned 2026-10-06, layer 2, see Backups): destination to be decided after the owner checks Hetzner's options.

---

## 6. Open decisions

All decisions needed before stages 0–2 are resolved. Remaining items can wait until the stage noted.

- Behavior of a saved item after it is read (remove vs. move to a read history). *(stage 6; a nullable `read_at` column keeps both options open cheaply)*
  - Either way, **opening a saved item is logged as a permanent, append-only event** (it is a training signal: saved-but-never-read is a weaker positive), not only as `saved.read_at`, which disappears when `saved` rows are pruned after two weeks. *(stage 6)*
- Off-server backup destination and tool (layer 2). *(stage 9; owner checking Hetzner's options)*
- Whether "read now" is weighted as a stronger positive than "save". *(stage 7; actions are logged distinctly, so this is a training-time choice)*
- ML retraining cadence (e.g., nightly vs. after N new swipes). *(stage 7)*
- Per-feed retention overrides (values and when). *(stage 8; `feeds.toml` already has room for them)*

---

## 7. Ideas for after the initial project

Not part of any stage; to look at once stages 0–9 are done.

- **Record the backend's git commit with each swipe** (`swipes.backend_commit`; moved here from stage 4 on 2026-10-06, user). Purpose: if a backend bug ever stores swipes wrongly, find exactly the affected rows (the phone side already has `app_version`). Cost: a migration, a `deploy.sh` passing the commit as a build arg, a changed deploy command. Until then, deploys are noted with date, time and commit in `progress.md`/`task_plan.md`; with each swipe's `received_at` that maps swipes to backend versions after the fact.
- **Try mutflow for generated mutants in the Android app** (2026-10-11, user). mutflow (github.com/anschnapp/mutflow, Apache-2.0) is a Kotlin compiler plugin: it builds every mutant into the code at compile time and switches them on one by one during the normal `test` run, so it mutates source, not bytecode, and avoids the Kotlin noise PIT has; it has a JUnit 4 runner, which Android unit tests use. Not tried now because it was very young (first release 2026-02, 1.0 in 2026-04, 38 stars) and each release works only with the Kotlin version it was built against. Check then: a release for the app's Kotlin version, any report of use in an Android project.
- **See how to minimize data loss in case a restore is needed** (2026-10-06, user). Today a restore is manual and loses everything written after the snapshot (at most an hour). Ideas discussed: a restore command, a safety copy of the current state before restoring, more frequent snapshots.
