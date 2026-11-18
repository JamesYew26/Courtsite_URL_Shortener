# Courtsite URL Shortener

A FastAPI + SQLite URL shortener service built for Courtsite's marketing and business intelligence (BI) workflows. Beyond shortening and redirecting URLs, it validates live URL reachability with SSRF protections and captures privacy-preserving visit telemetry so marketing teams can measure which physical locations and referral channels drive traffic.

---

## 1. Overview & Architecture

The service follows a clean **FastAPI + Repository** architecture with explicit dependency injection configured in [`app/main.py`](app/main.py):

- **API Layer (`app/routes.py`)**: Defines the shortening, redirect, BI analytics, and health endpoints, plus self-referential loop detection and privacy-preserving visitor hashing.
- **Validation Layer (`app/validator.py`)**: Performs syntactic validation, DNS/IP SSRF filtering (including redirect-hop inspection), and live HTTP reachability checks (`HEAD` with streaming `GET` fallback).
- **Persistence Layer (`app/repository.py`)**: Encapsulates SQLite schema initialization and queries (`short_urls` and `visits` tables) behind `URLRepository`.
- **Key Generation & Schemas (`app/keygen.py`, `app/schemas.py`, `app/config.py`)**: Generates 7-character Base62 keys via `secrets.choice`, validates request/response payloads with Pydantic v2, and centralizes configuration defaults.

### Project Directory Layout

```text
courtsite-url-shortener/
├── app/
│   ├── __init__.py
│   ├── config.py        # Settings defaults (db path, key length, timeout, base URL)
│   ├── keygen.py        # Cryptographic Base62 key generator
│   ├── main.py          # FastAPI app factory (create_app) & lifespan initialization
│   ├── repository.py    # SQLite URLRepository for links, visits, and BI queries
│   ├── routes.py        # API endpoints (/shorten_url/, redirect, stats, analytics, health)
│   ├── schemas.py       # Pydantic request & response models
│   └── validator.py     # URL syntax, SSRF DNS/IP guard, and HTTP reachability validator
├── tests/
│   ├── conftest.py      # Shared fixtures (in-memory SQLite + MockTransport validator)
│   ├── test_api.py      # End-to-end API, redirect, loop protection, and BI tests
│   ├── test_keygen.py   # Base62 keygen, schema normalization, and settings tests
│   ├── test_repository.py # SQLite persistence and BI aggregation tests
│   └── test_validator.py  # Syntax, SSRF, redirect-SSRF, and HEAD/GET fallback tests
├── pyproject.toml       # Project metadata and pytest configuration
└── requirements.txt     # Runtime and test dependencies
```

---

## 2. Running the Service

### What is needed to run the service?

- **Python 3.9+** (tested on Python 3.9.6)
- **`pip`** and `venv` (standard library)
- Dependencies listed in `requirements.txt`:
  - `fastapi>=0.110.0`
  - `uvicorn[standard]>=0.29.0`
  - `httpx>=0.27.0`
  - `pydantic>=2.6.0`
  - `pytest>=8.0.0`, `pytest-asyncio>=0.23.0` (for automated tests)

No external database server is required — the prototype persists data locally to SQLite (`shortener.db` by default, initialized automatically on startup).

### How can we run the service?

1. **Create and activate a virtual environment, then install dependencies:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Start the development server:**

   ```bash
   uvicorn app.main:app --reload
   ```

   The service will be available at `http://localhost:8000` (interactive OpenAPI/Swagger docs at `http://localhost:8000/docs`, and health check at `GET /health`).

3. **Run the automated test suite:**

   ```bash
   .venv/bin/pytest -v
   ```

   All tests run completely offline and deterministically using an in-memory SQLite database (`:memory:`) and `httpx.MockTransport`.

---

## 3. API Reference & Technical Design Decisions

### Endpoints Summary

| Method | Path | Success Status | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/shorten_url/` (and `/shorten_url`) | `201 Created` | Validate URL reachability and create a new short key |
| `GET` | `/shorten_url/{key}` | `302 Found` | Redirect to original URL and record BI visit telemetry |
| `GET` | `/shorten_url/{key}/stats` | `200 OK` | Per-link BI metrics (total/unique visits, referrers, recent visits) |
| `GET` | `/analytics/locations` | `200 OK` | Aggregated BI leaderboard grouped by `location` and `campaign` |
| `GET` | `/health` | `200 OK` | Service health check (`{"status": "ok"}`) |

---

### `POST /shorten_url/` — Shorten a URL

Both `/shorten_url/` and `/shorten_url` are registered to return `201 Created` directly without an intermediate `307` trailing-slash redirect.

#### Request Body

- `url` (`string`, required): Target `http://` or `https://` URL (leading/trailing whitespace is trimmed).
- `location` (`string | null`, optional): Physical placement label for BI tracking (e.g., `"Sunway Pyramid - Main Concourse"`). Blank strings normalize to `null`.
- `campaign` (`string | null`, optional): Marketing campaign identifier (e.g., `"Pickleball Launch Q4"`). Blank strings normalize to `null`.

#### Example Request

```bash
curl -X POST http://localhost:8000/shorten_url/ \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://courtsite.my/venues/sunway-pyramid",
    "location": "Sunway Pyramid - Main Concourse",
    "campaign": "Pickleball Launch Q4"
  }'
```

#### Chosen Response Envelope (`201 Created`)

```json
{
  "key": "aB3xK9q",
  "short_url": "http://localhost:8000/shorten_url/aB3xK9q",
  "original_url": "https://courtsite.my/venues/sunway-pyramid",
  "location": "Sunway Pyramid - Main Concourse",
  "campaign": "Pickleball Launch Q4",
  "created_at": "2026-11-18T11:45:00.123456+00:00"
}
```

#### Error Responses (`400 Bad Request`)

Returned when the URL fails syntax, self-referential loop checks, SSRF checks, or live HTTP reachability checks:

```json
{
  "detail": "The provided URL is not accessible (HTTP 404)"
}
```

#### Design Decision: Response Shape & Unique Key Per Request

1. **JSON Envelope vs. Bare String:** Returning a structured JSON object containing both the bare `key` and the ready-to-use `short_url` (plus echoed BI metadata and `created_at`) makes integration effortless for both API consumers and automated QR code generators without forcing clients to concatenate URLs themselves.
2. **Unique 7-Character Base62 Key Per Request:** Each `POST` generates a fresh random 7-character key from `0-9a-zA-Z` ($62^7 \approx 3.52\text{ trillion}$ combinations) using `secrets.choice` (with up to 5 collision retries).
   - **Compact for QR codes:** Short 7-character keys keep QR code module density low so printed posters scan reliably at a distance.
   - **Non-enumerable:** Cryptographic randomness prevents sequential scraping of shortened marketing links.
   - **Independent location tracking:** Rather than deduplicating identical destination URLs to the same key, generating a distinct key per request lets the marketing team print different trackable QR codes for the *exact same* Courtsite landing page across multiple shopping malls (e.g., Sunway Pyramid vs. Mid Valley) and compare their performance.

---

### URL Validation & Accessibility Check Pipeline

Before storing any URL, `POST /shorten_url/` runs a multi-stage validation pipeline (`app/routes.py` + `app/validator.py`):

1. **Self-Referential Loop Guard:** Rejects URLs whose path starts with `/shorten_url` on this service's own host/port (`400 Bad Request`), preventing infinite redirect loops.
2. **Syntactic Validation:** Trims whitespace and requires an `http` or `https` scheme, valid port, and non-empty `netloc` and `hostname`.
3. **SSRF DNS & IP Guard + Redirect Hook:**
   - Blocks `localhost` and `.localhost`, `.local`, `.internal` hostnames.
   - Resolves hostnames via `socket.getaddrinfo` (offloaded via `asyncio.to_thread`) and rejects any literal or resolved IP in loopback, private, link-local, multicast, reserved, or unspecified ranges (including IPv4-mapped IPv6 addresses).
   - Attaches an `httpx` `event_hooks={"request": [...]}` guard so **every redirect hop** and the final URL are re-checked before any outbound request leaves the server, defeating redirect-based SSRF (e.g., an external URL redirecting to `http://169.254.169.254/latest/meta-data/`).
4. **Live Reachability Check (`HEAD` with Streaming `GET` Fallback):**
   - Issues an async `HEAD` request (`follow_redirects=True`, `timeout=5.0`).
   - Many web servers and CDNs reject `HEAD` with `405 Method Not Allowed` or `403 Forbidden` even when the page works normally in browsers. When `HEAD` returns `405` or `403`, the validator automatically falls back to a streaming `GET` (`client.stream("GET", ...)`) to inspect the response status without downloading a large response body.
   - Any HTTP status $\ge 400$ or `httpx.HTTPError` (DNS failure, timeout, connection error) fails validation with `400 Bad Request`.

---

### `GET /shorten_url/<key>` — Redirect & Visit Tracking

Looks up `<key>` in SQLite (`404 Not Found` if missing), records a visit in `visits`, and redirects the client to `original_url`.

#### Example Request & Headers

```bash
curl -i "http://localhost:8000/shorten_url/aB3xK9q?ref=qr_poster_entrance"
```

```http
HTTP/1.1 302 Found
location: https://courtsite.my/venues/sunway-pyramid
cache-control: no-store, no-cache, must-revalidate
```

#### Design Decision: Why `302 Found` + `Cache-Control: no-store` Instead of `301 Moved Permanently`

- **Accurate Analytics:** Browsers and mobile OS caches treat `301 Moved Permanently` as permanent. After a user's first scan or click, subsequent visits bypass the shortener server completely and jump straight to the destination URL — silently undercounting repeat visits and breaking BI telemetry.
- **Operational Flexibility:** Using **`302 Found`** alongside `Cache-Control: no-store, no-cache, must-revalidate` forces every scan/click to hit `GET /shorten_url/{key}` so visit counts, timestamps, and referrer breakdowns stay accurate, while also allowing a printed mall QR code's destination URL to be updated later without reprinting posters.

#### Visit Telemetry Captured

Each redirect writes a row to `visits`:
- `visitor_hash`: Truncated `SHA-256("{client_ip}:{user_agent}")[:16]` (uses the first IP in `X-Forwarded-For` when behind a reverse proxy, falling back to `request.client.host`).
- `referrer`: Uses `?ref=` query parameter if provided, otherwise falls back to the HTTP `Referer` header, normalizing missing/blank values to `"direct"`.
- `user_agent`: Raw `User-Agent` header for device/scanner context.
- `visited_at`: UTC ISO-8601 timestamp.

---

### Stretch Goal BI Endpoints

#### 1. `GET /shorten_url/<key>/stats` — *How did someone get to the post or URL?*

Returns total visits, unique visitors, a sorted referrer channel breakdown, and the 50 most recent visits for a given short key (`404 Not Found` if unknown).

```bash
curl http://localhost:8000/shorten_url/aB3xK9q/stats
```

```json
{
  "key": "aB3xK9q",
  "original_url": "https://courtsite.my/venues/sunway-pyramid",
  "location": "Sunway Pyramid - Main Concourse",
  "campaign": "Pickleball Launch Q4",
  "created_at": "2026-11-18T11:45:00.123456+00:00",
  "total_visits": 42,
  "unique_visits": 31,
  "referrers": {
    "qr_poster_entrance": 25,
    "https://instagram.com/courtsite": 10,
    "direct": 7
  },
  "recent_visits": [
    {
      "visited_at": "2026-11-18T11:50:12.654321+00:00",
      "referrer": "qr_poster_entrance",
      "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)..."
    }
  ]
}
```

#### 2. `GET /analytics/locations` — *What physical locations are driving traffic?*

Aggregates all shortened links and their visits grouped by `location` (defaulting `null` to `"Unspecified"`) and `campaign`, ordered by `total_visits DESC, total_links DESC`.

```bash
curl http://localhost:8000/analytics/locations
```

```json
[
  {
    "location": "Sunway Pyramid - Main Concourse",
    "campaign": "Pickleball Launch Q4",
    "total_links": 2,
    "total_visits": 87,
    "unique_visits": 64
  },
  {
    "location": "Mid Valley Megamall - North Court",
    "campaign": "Pickleball Launch Q4",
    "total_links": 1,
    "total_visits": 34,
    "unique_visits": 28
  }
]
```

---

## 4. Engineering Evaluation

### What challenges did you run into?

1. **False-Negative `405`/`403` Responses on `HEAD` Validation:** Checking live URL accessibility with `HEAD` alone rejects valid public sites and CDNs that disallow `HEAD` requests (`405 Method Not Allowed` or `403 Forbidden`). Solving this required falling back to a streaming `GET` (`client.stream("GET", ...)`) when `HEAD` returns `405` or `403`, reading only the status code and final URL without buffering large payloads.
2. **SSRF & Redirect-Based SSRF Risks:** Fetching user-supplied URLs server-side exposes internal metadata endpoints (`169.254.169.254`) and localhost/private services. Even if the initial URL resolves to a public IP, an attacker can serve a `302` redirect to `127.0.0.1`. We mitigated this by validating both literal IPs and `socket.getaddrinfo` DNS results against restricted IP ranges, hooking every `httpx` redirect request via `event_hooks`, and re-validating the final destination host.
3. **Mobile QR Scanners Omitting HTTP `Referer` Headers:** Native iOS/Android camera QR scanners open links with no HTTP `Referer` header, making every physical scan look identical if tracked only by `Referer`. We addressed this in two ways: (a) assigning unique short keys per physical placement with `location`/`campaign` metadata, and (b) supporting a `?ref=` query parameter override on `GET /shorten_url/{key}` so posters or social links can pass explicit attribution tags.
4. **Deterministic Offline Testing:** Testing live HTTP reachability and SSRF redirect guards without real network calls can be flaky or slow. Injecting `httpx.MockTransport` into `URLValidator` and `:memory:` SQLite into `URLRepository` keeps the entire 29-test suite deterministic and running in ~0.16s offline.

### What assumptions did you make?

- **One Unique Key Per `POST` Request (No URL Deduplication):** Assumed marketing prefers separate trackable keys for the same destination URL when placed at different physical locations or campaigns, rather than returning a single shared key.
- **Privacy-Preserving Unique Visitor Approximation:** Assumed unique visitor counts should not store raw IP addresses (PII) or rely on tracking cookies (which are often stripped or blocked in cross-site redirects and in-app browsers). Hashing `SHA-256(client_ip:user_agent)[:16]` gives a reasonable privacy-preserving approximation for unique visits.
- **Validation at Creation Time Only:** Assumed URL reachability should be verified during `POST /shorten_url/` rather than on every `GET /shorten_url/{key}` redirect so end-user redirects stay fast and independent of upstream hiccups.
- **Single-Node SQLite Prototype:** Assumed SQLite (`shortener.db`) with synchronous writes is appropriate for a self-contained assessment prototype before scaling horizontally.

### What would you improve with more time?

- **Async Analytics Ingestion:** Offload `record_visit` writes to a background queue (e.g., Redis Streams / Kafka + worker consumers) so `GET /shorten_url/{key}` achieves sub-millisecond redirect latency without blocking on database writes.
- **Redis Cache + PostgreSQL Storage:** Put a Redis read-through cache in front of PostgreSQL (`key -> original_url`) for high-throughput read scaling and multi-worker deployments.
- **Rate Limiting & Abuse Controls:** Add per-IP rate limiting (e.g., token bucket via Redis) on `POST /shorten_url/` to prevent link spam and outbound validation abuse.
- **Custom Aliases, TTL & Link Management:** Support optional vanity aliases (e.g., `/shorten_url/sunway-open`), link expiration timestamps (`expires_at`), and authenticated endpoints to update or deactivate destination URLs.
- **Built-In QR Code Generator Endpoint:** Add `GET /shorten_url/{key}/qr` returning print-ready SVG/PNG QR codes directly for Courtsite's marketing team.

### Roughly how long did you spend?

Approximately **3.5 hours** end-to-end across requirements analysis, architecture & API design, TDD implementation of the core shortener + validation/SSRF guard + BI stretch goals, and documentation.
