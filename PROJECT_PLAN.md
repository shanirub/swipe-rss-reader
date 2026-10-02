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
- **API exposure:** via **Tailscale Serve** (reverse proxy built into `tailscaled`, tailnet-only). The API container publishes on **loopback only** (`127.0.0.1:8000`), never on all interfaces or directly on the Tailscale interface. Serve terminates HTTPS and forwards to it: `tailscale serve --bg --https=443 localhost:8000` (`--bg` persists across reboots).
  - Phone reaches the API at `https://<machine>.<tailnet>.ts.net`.
  - Loopback binding avoids a boot-order race with `tailscale0` and makes Docker's iptables bypass of host firewalls irrelevant for the API.
- **HTTPS:** MagicDNS certificates provisioned automatically by Serve (replaces the need for Caddy, a public domain, or Let's Encrypt). Requires MagicDNS and HTTPS enabled in the Tailscale admin console.
  - **Caveat:** certificates are recorded in public Certificate Transparency logs, so the machine name and tailnet name become publicly visible (not reachable). Use a non-sensitive machine name; consider a randomized tailnet name.
- Serve adds identity headers (e.g., `Tailscale-User-Login`) to proxied requests. Not used for auth: they can be spoofed if the API is ever reached without going through Serve.
- **API auth: static bearer token** on top of Tailscale (defense in depth against local processes on the server and misconfiguration such as Funnel or a `0.0.0.0` publish). Server: token in `.env`, constant-time comparison. App: token in gitignored `local.properties` → `BuildConfig`, added by an OkHttp interceptor. Back it up alongside the keystore; rotation requires an app rebuild. Declared as a security scheme in the OpenAPI contract.
- **Machine name:** keep `my-first-server` (tailnet name is already randomized: `porcupine-celsius.ts.net`). The server is shared with other hobby services (e.g., Vikunja), so the name is intentionally not RSS-specific.
- **Multiple services on the host:** one **HTTPS port per service** via Serve (e.g., `:443` → RSS API, `:8443` → Vikunja), not sub-paths. Verify which ports Serve accepts in stage 0. Each service in its own Compose project with its own Docker network and volumes; never mount the Docker socket into a container.
- **Tailscale ACLs:** restrict which tailnet devices may reach `my-first-server` and on which ports (e.g., phone + main desktop only), so a compromised device elsewhere on the tailnet can't reach every service. Also block `my-first-server` from initiating connections to other tailnet devices (second layer behind the extraction SSRF guard).

### Repository

- **Monorepo** containing backend, Android app, and the API contract.
- The **OpenAPI spec** file in the repo is the single source of truth for the backend ↔ app interface.

### Workflow

- Hosted on **GitHub**. Development (backend and Android) happens on the desktop; the server only runs committed code.
- Server pulls with a **read-only deploy key** scoped to this repo (not a personal SSH key or token).
- Deploy: `git pull && docker compose up -d --build`. Dependencies are installed inside the image build, pinned by `uv.lock`. Later option: build images in CI, push to GHCR, and have the server pull images only.
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

- **Dedicated scheduler container** (e.g., supercronic) runs the fetcher. This keeps scheduling inside Compose and isolates fetch failures from the API.
- The same container runs the **extraction job** every minute (see Content extraction) and, from stage 3, the pruning job.

### Feeds

- Defined in **`feeds.toml`, committed to the repo**. Bind-mounted from the server's checkout into the fetcher container and re-read on every fetch run, so `git pull` applies edits without rebuild or restart.
- **TOML**, read with stdlib `tomllib`, validated with Pydantic.
- Each feed has a **required, stable `id` slug** (never changes), plus `url` and optional `name`. The `id` (not the URL) keys `feed_status` and is the feed identifier stored in the swipe log, so a feed's URL can change without orphaning its history.
- `[defaults]` table for global settings; per-feed overrides (e.g., `retention_hours`) live on the feed entry later.
- **`max_item_age_hours`** (in `[defaults]`, currently 24): the fetcher skips entries published longer ago than this; entries without a date use fetch time. Not tombstoned. Introduced to keep the initial backlog out; revisit with retention (stage 3).
- **Invalid file → the fetch run aborts and logs a clear error.** A typo must never be interpreted as "all feeds removed".
- Initial feed list: converted from the owner's existing Markdown list (to be shared later).

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
| `swipe_id` | Client-generated UUID; idempotency for the offline queue. |
| `action` | never / save / read now. |
| `feed_id` | Stable feed slug; likely the strongest single feature. |
| `headline` | Permanent copy. |
| `summary` | Permanent copy, **plain text** (HTML is stripped at ingest, so items are already plain text when served). |
| `link` | Normalized; enables re-fetching full text later (embeddings) and link domain as a feature. |
| `published_at` | Article age at swipe time. |
| `tags`, `author` | JSON, nullable; cheap features when the feed provides them. |
| `swiped_at` | Client time; the real event time (may precede receipt by days when offline). |
| `received_at` | Server time; audit. |
| `time_to_swipe_ms` | Nullable; how long the card was on screen before the swipe (implicit interest signal). |

- **Extracted full text is deliberately not a training feature.** Only positives (save / read now) get extracted, so training on it would leak the label. Train on fields available uniformly for all labels.

### Swipe recording

- **Idempotency:** the phone generates a `swipe_id` (UUIDv4) at swipe time and stores it with the swipe in the Room queue. Server: `swipe_id` is the primary key; inserts use `ON CONFLICT(swipe_id) DO NOTHING`; the response is success whether new or duplicate, so retries are always safe.
- **Endpoint:** a single batch `POST /swipes` accepting 1..N swipes (live swiping sends one, offline sync sends the backlog). Partial failures are resolved by resending the whole batch.
- **Append-only log:** no per-item uniqueness. For training, the **latest swipe per item wins**. Undo (stage 8) becomes another event, not a schema change.
- **Card snapshot:** each swipe carries the item fields the phone received from the queue (headline, summary, link, `feed_id`, `published_at`, tags, author); the server stores them as given (Pydantic-validated, length-limited). This makes swipes independent of item pruning (offline for any duration) and makes the label pair with exactly what was displayed. If the item still exists, the server also removes it from the queue.

### Content extraction

- **Read now:** no extraction. The app opens the original page directly in **Custom Tabs** (online by definition; page exists now). An in-app reader for read-now items is a possible later addition.
- **Save for later:** extracted **shortly after save**, so saved items survive pages disappearing, changing, or going behind a paywall within the two-week window.
  - The swipe is recorded immediately; extraction never runs inside the request.
  - **DB as queue:** `saved` rows carry `extraction_status` (pending / done / failed), `attempts`, `last_error`. A scheduler-container job runs **every minute** and processes pending rows.
  - **Retries:** up to 3 attempts with increasing backoff, then `failed`. The app shows "couldn't extract — open original" and falls back to Custom Tabs.
  - Known limitation: trafilatura can't reliably detect paywalls; a paywalled page may "succeed" with teaser text only.
- **SSRF guard** (extraction fetches URLs from third-party feed content and client snapshots): allow only `http`/`https`; resolve DNS and refuse loopback, private, link-local, and Tailscale CGNAT (`100.64.0.0/10`) addresses, re-checking after every redirect; enforce a timeout and a max response size.

### Retention

- Unswiped items: **2 days** (may later be tuned per feed).
- Saved items: **2 weeks**.
- **Tombstones:** keep seen dedup keys (see Deduplication) for ~90 days (longer than any feed's window) so pruned items are not re-inserted as new. Written at first sight, expired by the pruning job.
- **Swipe log is kept permanently** and stores the item's headline and summary text itself, not only a reference to an item that will be pruned.

### Queue ordering (pre-ML)

- **Round-robin across feeds, oldest-first within each feed.** This gives fair exposure, and items are seen before they expire. It will be replaced by ML ranking later.

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

### Notifications

- None for now.

---

## 4. Data model (high level)

- **feed_status:** per-feed fetch state, conditional-GET headers, last successful fetch, last new item, last error.
- **items:** fetched articles (subject to retention).
- **swipes:** permanent swipe log; see "Swipe log fields" in §3.
- **saved:** read-later entries with extracted content and extraction state (`extraction_status`, `attempts`, `last_error`).
- **tombstones:** seen `(feed_id, key)` dedup keys with first-seen timestamps (~90 days), written at insert time.

Feed definitions themselves live in the feeds file, not the database.

---

## 5. Stages

Each stage should be usable and testable before the next. Stages 0–4 are backend-only and testable with `curl` over the tailnet.

0. **Server foundation:** Docker, Tailscale Serve, Tailscale ACLs, firewall verification (no public ports), automatic security updates, read-only deploy key.
Stages are **vertical slices**: each stage adds the tables it needs via a new Alembic migration, next to the code that uses them.

1. **Ingest:** SQLite settings, Alembic baseline, `feed_status` / `items` / `tombstones` tables. Fetcher: feeds file → fetch (conditional GET) → parse → deduplicate (incl. tombstones) → store. Run by the scheduler container. Testable by running a fetch and inspecting the DB with `sqlite3`.
2. **API:** OpenAPI contract first, then `swipes` / `saved` tables (new migration) and endpoints for: swipe queue, recording swipes, saved list, extracted content, per-feed status.
3. **Retention:** pruning job (unswiped items, saved items, tombstone expiry). Comes after the API because its rules depend on swipe and save state.
4. **Deployment & backups:** full Compose setup, encrypted rclone backup to Google Drive.
5. **Android MVP:** swipe cards against the API, Room cache, offline swipe queue with WorkManager sync, feed-status screen.
6. **Read-later view:** saved list, extracted-content reader, Custom Tabs fallback.
7. **Ranking:** TF-IDF + logistic regression on the swipe log, queue ordering by predicted interest, exploration slice.
8. **Iterate:** embeddings, per-feed retention tuning, undo, UX polish.

---

## 6. Open decisions

All decisions needed before stages 0–2 are resolved. Remaining items can wait until the stage noted.

- What happens to existing items when a feed is removed from `feeds.toml`. *(after stage 1; default: let them expire)*
- Behavior of a saved item after it is read (remove vs. move to a read history). *(stage 6; a nullable `read_at` column keeps both options open cheaply)*
- Backup method and policy: snapshot via `sqlite3 .backup` or `VACUUM INTO` before upload (never copy the live WAL database file); frequency and retention. *(stage 4)*
- Whether "read now" is weighted as a stronger positive than "save". *(stage 7; actions are logged distinctly, so this is a training-time choice)*
- ML retraining cadence (e.g., nightly vs. after N new swipes). *(stage 7)*
- Per-feed retention overrides (values and when). *(stage 8; `feeds.toml` already has room for them)*
