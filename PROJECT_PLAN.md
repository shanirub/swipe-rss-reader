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
- **API auth: static bearer token** on top of Tailscale (defense in depth against local processes on the server and misconfiguration such as Funnel or a `0.0.0.0` publish). Server: token in `.env`, constant-time comparison. App: token in gitignored `local.properties` → `BuildConfig`, added by an OkHttp interceptor. Back it up alongside the keystore; rotation requires an app rebuild. Declared as a security scheme in the OpenAPI contract.
- **Auth is secure by default** (implemented 2026-10-03): the token check is an app-level FastAPI dependency, so every route requires the token however it is registered; only paths in an explicit `PUBLIC_PATHS` allowlist (`/health`) skip it. The API **fails closed**: it refuses to start if `SWIPE_RSS_API_TOKEN` is unset or shorter than 32 characters. 401 responses carry `WWW-Authenticate: Bearer`.
- **Machine name:** keep `my-first-server` (tailnet name is already randomized: `porcupine-celsius.ts.net`). The server is shared with other hobby services (e.g., Vikunja), so the name is intentionally not RSS-specific.
- **Multiple services on the host:** one **HTTPS port per service** via Serve (`:8443` → RSS API; a future Vikunja gets another port, e.g. `:9443`), not sub-paths. `:443` is unavailable to Serve while nginx needs `0.0.0.0:443`. Verified in stage 0: Serve accepts arbitrary HTTPS ports (443, 8443, 9443 tested), all with the same MagicDNS certificate. Each service in its own Compose project with its own Docker network and volumes; never mount the Docker socket into a container.
- **Existing public service (accepted exception):** nginx on public `:80`/`:443` fronts the owner's MCP server (`mcp-server.service`, `127.0.0.1:8000`, domain `mcp.ministryofpa.ws`) for a claude.ai connector. Unrelated to this project; kept installed, currently disabled while idle (re-enable command in findings.md). nginx binds `0.0.0.0:443`, which includes the Tailscale IP. Verified in stage 0 that Serve on `:443` blocks nginx from starting (bind conflict), so the RSS API uses Serve `:8443`.
- **Tailscale ACLs: default allow-all, kept deliberately** (decided in stage 0). The owner controls which devices join the tailnet. Accepted consequence: the extraction SSRF guard is the only barrier between the server and other tailnet devices, so it must refuse the Tailscale ranges explicitly, both IPv4 `100.64.0.0/10` and IPv6 `fd7a:115c:a1e0::/48`. Revisit if a less trusted device joins.

### Repository

- **Monorepo** containing backend, Android app, and the API contract.
- The **OpenAPI spec** file in the repo is the single source of truth for the backend ↔ app interface.
- Spec: **`api/openapi.yaml`** (OpenAPI 3.1), **hand-written first**; code follows it. Backend drift is caught by **contract tests** in pytest: (1) the app's set of (path, method) pairs equals the spec's; (2) must-fail and status-code tests per endpoint. Request/response *shapes* come from models generated from the spec (below), so per-response shape validation (e.g., `openapi-core`) is largely redundant; the full test strategy is settled in the testing-coverage discussion before contract tests are written. No exact comparison with FastAPI's generated spec (brittle). FastAPI's own `/openapi.json` and `/docs` are disabled or serve the YAML, so no second divergent spec is exposed. Behavior the spec can't express (idempotency, queue ordering, swipe flag, auth) is covered by ordinary tests derived from this plan.
- **API (wire) models are generated from the spec** with `datamodel-code-generator` (dev-only dependency, version pinned in `uv.lock`, formatter set explicitly) into the committed file `backend/src/swipe_rss/api_models.py` (header: generated, do not edit). Generator options live in `[tool.datamodel-codegen]` in `backend/pyproject.toml`; regenerate with `cd backend && uv run datamodel-codegen`. A **freshness test** (`tests/test_api_models.py`) runs the generator's own `--check` and fails if the committed file is stale. Custom validation lives in subclasses or handlers, never in the generated file. SQLAlchemy models stay separate (storage shape may differ from wire shape). Generation guarantees the *models* match the spec; it does not replace tests for routes, status codes, auth, error handling, wiring of models to routes, or invalid input (see the testing-coverage discussion).
- **API conventions** (decided 2026-10-03):
  - `GET /health` is the only unauthenticated endpoint: fixed `{"status": "ok"}`, no DB access, never extended with versions or stats.
  - List responses are wrapped in an object (`{"items": [...]}`, `{"feeds": [...]}`) so fields can be added without breaking clients.
  - `GET /saved/{feed_id}/{item_key}/content` returns `200` for any saved entry; the client branches on `extraction_status`, `text` is null until `done`; `404` only if not saved.
  - Fields are **required but nullable**: every field is always present, `null` when there is no value. A forgotten field is a `422`, not a silently missing value.
  - **New request fields must be optional** (and old ones never tightened), so older app versions and their queued swipes keep working.

### Workflow

- Hosted on **GitHub**. Development (backend and Android) happens on the desktop; the server only runs committed code.
- The repo is **public**, so the server clones and pulls **anonymously over HTTPS** (no deploy key, no credentials on the server). If the repo ever goes private, switch to a read-only deploy key.
- Deploy: `git pull && docker compose up -d --build`. Claude may run exactly this plus read-only checks over Tailscale SSH, after the owner approves each deploy; all other server changes are done by the owner. Dependencies are installed inside the image build, pinned by `uv.lock`. Later option: build images in CI, push to GHCR, and have the server pull images only.
- **Secrets never in git:** `.env` (commit `.env.example`), rclone/restic config and passphrase, Android signing keystore, `local.properties` (holds the API token).
- The Android app is built on the desktop; the server ignores `android/`.

### Backend

- **Python** with **FastAPI** (async web framework, auto-generated OpenAPI docs), run by **Uvicorn** (ASGI server).
- **Pydantic** for request/response validation.
- **httpx** (async HTTP client) + **feedparser** (tolerant RSS/Atom parser). Fetching uses **conditional GET** (`ETag` / `If-Modified-Since`).
- **SQLAlchemy 2.x** (ORM, **sync**) + **Alembic** (schema migrations; batch mode for SQLite). API endpoints that touch the DB are plain `def` (FastAPI runs them in its thread pool); aiosqlite would only wrap the sync driver in a thread, so async gains nothing with SQLite.
- **trafilatura** for article text extraction.
- Tooling: **uv** (package manager with lockfile), **pytest**, **ruff**.

### Database

- **SQLite**, single user. Settings: **WAL mode** (concurrent reads during writes), **busy_timeout** (writers wait rather than fail), **STRICT tables** (enforced column types).
- Shared between the fetcher and API containers via a **named Docker volume** on the same host. Never on a network filesystem.
- **All transactions start with `BEGIN IMMEDIATE`.** Deferred transactions that upgrade from read to write fail immediately with `SQLITE_BUSY_SNAPSHOT` in WAL mode if another process wrote in between, and `busy_timeout` cannot help. IMMEDIATE takes the write lock up front, so `busy_timeout` applies. Implemented via SQLAlchemy's documented pysqlite recipe: `isolation_level = None` on connect, emit `BEGIN IMMEDIATE` in a `begin` event listener. Applied to all transactions (not only writes) for simplicity; contention cost is negligible at this scale.
- **Never hold a transaction across network I/O.** Fetcher: fetch and parse each feed outside any transaction, then write that feed's results in one short transaction. Same for background extraction.
- Future embeddings: brute-force cosine similarity in Python is expected to be sufficient at this scale. Benchmark before adding a vector extension.

### Scheduling

- **Dedicated scheduler container** (supercronic) runs the fetcher **every 15 minutes** (`backend/crontab`). A one-shot **`migrate`** Compose service runs `alembic upgrade head` first; other services start only after it completes, so migrations never race. This keeps scheduling inside Compose and isolates fetch failures from the API.
- The same container runs the **extraction job** every minute (see Content extraction) and, from stage 3, the pruning job.

### Feeds

- Defined in **`config/feeds.toml`, committed to the repo**. The `config/` **directory** is bind-mounted read-only from the server's checkout into the containers and the file is re-read on every fetch run, so `git pull` applies edits without rebuild or restart. (A single-file bind mount would go stale: `git pull` replaces the file with a new inode.)
- **TOML**, read with stdlib `tomllib`, validated with Pydantic.
- Each feed has a **required, stable `id` slug** (never changes), plus `url` and optional `name`. The `id` (not the URL) keys `feed_status` and is the feed identifier stored in the swipe log, so a feed's URL can change without orphaning its history.
- `[defaults]` table for global settings; per-feed overrides (e.g., `retention_hours`) live on the feed entry later.
- **`max_item_age_hours`** (in `[defaults]`, currently 24): the fetcher skips entries published longer ago than this; entries without a date use fetch time. Not tombstoned. Introduced to keep the initial backlog out; revisit with retention (stage 3).
- **Invalid file → the fetch run aborts and logs a clear error.** A typo must never be interpreted as "all feeds removed".
- Initial feed list: converted from the owner's Markdown list (`tech_privacy_rss_feeds.md`).

### Deduplication

- Scope is **per feed**: identity is `(feed_id, key)`. The same article in two feeds appears twice; revisit only if this proves annoying (global dedup on normalized link would only catch identical URLs and muddies the per-feed ML signal).
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
| Read now | positive (possibly stronger) | swipe up / tap |

- All three actions are **logged distinctly**, even if initially treated as equal positives.
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
- **Endpoint:** a single batch `POST /swipes` accepting 1..N swipes (live swiping sends one, offline sync sends the backlog). Transport failures (no response, timeout) are resolved by resending the whole batch; validation failures follow "Invalid swipes" below.
- **Invalid swipes (decided 2026-10-03):** the server validates the batch as a whole (`422` rejects all of it). On `422`, the phone resends that batch one swipe at a time; a swipe that fails alone moves to a local **dead-letter** table (kept, not resent automatically), so one bad swipe never blocks the queue. Alternatives considered: per-swipe results in a `200` (loose request schema), one swipe per request (many requests).
- **Dead letters are visible and recoverable:**
  - *Stage 2:* the server logs every `422` on `POST /swipes` with the `swipe_id`s, the validation errors and the request body (own data, single user), so rejections show up in `docker compose logs` even if the phone never reports them.
  - *Stage 5:* each dead letter is stored with the `app_version` that failed and its error. On the first start of a new app version, all dead letters are retried once (a release that fixes the cause may first repair stored swipes). A debug screen lists them with **retry** and **export as JSON**. Resending is always safe: dead letters keep their original `swipe_id`.
  - *Stage 8, only if dead letters actually occur:* a deliberately lenient `POST /dead-letters` (any JSON up to a size limit, stored as-is) plus a server command to repair and import them into `swipes`.
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

### Retention

- Unswiped items: **2 days** (may later be tuned per feed).
- Swiped items (`items.swiped_at` set): deleted by the pruning job; how soon is decided in stage 3 (default: next run, since the swipe log holds the card).
- Saved items: **2 weeks**.
- **Tombstones:** keep seen dedup keys (see Deduplication) for ~90 days (longer than any feed's window) so pruned items are not re-inserted as new. Written at first sight, expired by the pruning job.
- **Swipe log is kept permanently** and stores the item's headline and summary text itself, not only a reference to an item that will be pruned.

### Queue ordering (pre-ML)

- **Round-robin across feeds, oldest-first within each feed.** This gives fair exposure, and items are seen before they expire. It will be replaced by ML ranking later.
- **Queue endpoint semantics:** returns up to `limit` unswiped items. It is stateless: the server doesn't track what the phone already holds, so items fetched earlier but not yet swiped (or whose swipes haven't synced) are returned again; the phone deduplicates by `(feed_id, item_key)`.

### Observability

- Log every failed fetch.
- Per feed, store and expose: **last successful fetch** and **last new item**. The app displays both, to distinguish a broken fetcher from a source that went quiet.

### Backups

- **rclone** (optionally with **restic**) to **Google Drive**, **encrypted** before upload.

### ML ranking (later stage)

- Start with **scikit-learn**: `TfidfVectorizer` + `LogisticRegression`, model persisted with **joblib**. Runs server-side.
- Reserve a small **exploration** percentage of unranked items in the queue, to avoid filter bubbles.
- Later option: sentence embeddings (sentence-transformers; heavier, pulls in PyTorch, so check server resources).

### Android

- **Kotlin** + **Jetpack Compose**.
- **Retrofit + OkHttp** (HTTP), **kotlinx.serialization** (JSON).
- **Room** for the local cache and an offline queue of unsent swipes.
- **WorkManager** syncs queued swipes when the network returns.
- **Coil** for images (if feeds provide thumbnails).
- **Custom Tabs** for opening articles.
- Dependency injection (Hilt) skipped for the MVP.
- **Distribution:** sideloaded release APK signed with a personal key, installed via adb (wireless over Tailscale) or downloaded from the server. Increment `versionCode` each release. **Back up the keystore.** Losing it forces an uninstall to update.
- **minSdk:** set to the owner's phone Android version.
- **kotlinx.serialization must send nulls and defaults** (`encodeDefaults = true`, or no default values on API fields): by default it omits fields equal to their default, which would drop required-but-nullable fields and get swipes rejected with `422`. Verify the current default when writing the client.

### Notifications

- None for now.

---

## 4. Data model (high level)

- **feed_status:** per-feed fetch state, conditional-GET headers, last successful fetch, last new item, last error.
- **items:** fetched articles (subject to retention); nullable `swiped_at` flags swiped items, which leave the queue but stay until pruned.
- **swipes:** permanent swipe log; see "Swipe log fields" in §3. Item identity `(feed_id, item_key)` (indexed). `action` and `saved.extraction_status` are limited by CHECK constraints.
- **saved:** read-later entries, one per `(feed_id, item_key)`, referencing the save swipe (`swipe_id`, foreign key); extracted content (`text`, `extracted_at`) and extraction state (`extraction_status`, `attempts`, `last_error`, `next_attempt_at`), nullable `read_at`.
- **tombstones:** seen `(feed_id, key)` dedup keys with first-seen timestamps (~90 days), written at insert time.

Feed definitions themselves live in the feeds file, not the database.

---

## 5. Stages

Each stage should be usable and testable before the next. Stages 0–4 are backend-only and testable with `curl` over the tailnet.

Stages are **vertical slices**: each stage adds the tables it needs via a new Alembic migration, next to the code that uses them.

0. **Server foundation:** Docker, Tailscale Serve, firewall verification (no public ports), automatic security updates, repo clone on the server.
1. **Ingest:** SQLite settings, Alembic baseline, `feed_status` / `items` / `tombstones` tables. Fetcher: feeds file → fetch (conditional GET) → parse → deduplicate (incl. tombstones) → store. Run by the scheduler container. Testable by running a fetch and inspecting the DB with `sqlite3`.
2. **API:** OpenAPI contract first, then `swipes` / `saved` tables (new migration) and endpoints for: swipe queue, recording swipes, saved list, extracted content, per-feed status. Includes the `api` Compose service (published on `127.0.0.1:8001`) so the stage is testable with `curl` over the tailnet; stage 4 completes the rest of the Compose setup.
3. **Retention:** pruning job (unswiped items, swiped items, saved items, tombstone expiry). Comes after the API because its rules depend on swipe and save state.
4. **Deployment & backups:** full Compose setup, encrypted rclone backup to Google Drive.
5. **Android MVP:** swipe cards against the API, Room cache, offline swipe queue with WorkManager sync (single-swipe fallback on `422`, dead-letter store with retry on app update, debug screen with retry/export), feed-status screen.
6. **Read-later view:** saved list, extracted-content reader, Custom Tabs fallback, permanent log of reads.
7. **Ranking:** TF-IDF + logistic regression on the swipe log, queue ordering by predicted interest, exploration slice.
8. **Iterate:** embeddings, per-feed retention tuning, undo, UX polish; lenient dead-letter upload if needed.

---

## 6. Open decisions

All decisions needed before stages 0–2 are resolved. Remaining items can wait until the stage noted.

- What happens to existing items when a feed is removed from `feeds.toml`. *(after stage 1; default: let them expire)*
- How soon swiped items are pruned. *(stage 3; default: next pruning run)*
- Behavior of a saved item after it is read (remove vs. move to a read history). *(stage 6; a nullable `read_at` column keeps both options open cheaply)*
  - Either way, **opening a saved item is logged as a permanent, append-only event** (it is a training signal: saved-but-never-read is a weaker positive), not only as `saved.read_at`, which disappears when `saved` rows are pruned after two weeks. *(stage 6)*
- Backup method and policy: snapshot via `sqlite3 .backup` or `VACUUM INTO` before upload (never copy the live WAL database file); frequency and retention. *(stage 4)*
- Whether "read now" is weighted as a stronger positive than "save". *(stage 7; actions are logged distinctly, so this is a training-time choice)*
- ML retraining cadence (e.g., nightly vs. after N new swipes). *(stage 7)*
- Per-feed retention overrides (values and when). *(stage 8; `feeds.toml` already has room for them)*
