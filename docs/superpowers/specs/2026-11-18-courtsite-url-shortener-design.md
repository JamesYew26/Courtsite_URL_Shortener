# Courtsite URL Shortener — Design Specification

## 1. Intent & Background
Courtsite's marketing team shares social media posts and homepage URLs, including via QR codes placed at physical locations (e.g., shopping malls). They want a URL shortener web service that acts as a proxy to the original URL and enables Business Intelligence (BI) around traffic sources and physical locations.

This prototype demonstrates clean requirement interpretation, proactive edge-case and failure-mode handling, extensibility for changing requirements, and clear engineering communication within a pragmatic 3–5 hour scope.

## 2. Goals, Non-Goals, and Anti-Goals
### Goals
- **Core Shortening (`POST /shorten_url/`)**: Accept `{"url": "<original_url>"}` (plus optional BI metadata), validate syntax and live accessibility, block SSRF targets, generate a unique short key per request, and return a clear JSON envelope.
- **Core Redirection (`GET /shorten_url/<key>`)**: Redirect valid keys to their original URL using `302 Found` and return `404 Not Found` for nonexistent keys.
- **Stretch Goal — BI & Visit Tracking**: Track every redirect event (timestamp, unique visitor hash, HTTP Referer, User-Agent, and optional query params like `?ref=`) and expose `GET /shorten_url/<key>/stats` for business intelligence.
- **Automated Testing**: Comprehensive `pytest` suite covering happy paths, invalid/unreachable URLs, SSRF protection, 404s, and visit tracking accuracy without relying on flaky external internet calls during tests.
- **Supporting Documentation**: Thorough `README.md` covering setup, run instructions, API design decisions, and the engineering evaluation (challenges, assumptions, future improvements, time spent).

### Non-Goals
- User authentication, authorization, or multi-tenant account management (explicitly excluded by brief).
- External database servers (MySQL/PostgreSQL/Redis) required at runtime — the service must run out-of-the-box with zero external infrastructure.

### Anti-Goals
- Over-engineered enterprise ORM/migration boilerplate that obscures the core logic.
- Using `301 Moved Permanently` redirects, which cause browsers to cache redirects indefinitely and bypass BI visit tracking on subsequent scans.

## 3. Constraints
- **Project Location**: `/Users/jamesyew/.gemini/antigravity/scratch/courtsite-url-shortener`
- **Language & Stack**: Python 3.11+, FastAPI, Pydantic v2, `httpx` (async HTTP client for URL reachability validation), `sqlite3` (built-in standard library persistence), `pytest` + `pytest-asyncio` / `respx` or custom transport mocking for deterministic tests.

## 4. Approaches Considered
1. **Approach 1: Clean Modular FastAPI + SQLite Persistence via Repository Pattern (Selected)**
   - *Tradeoffs*: Uses Python's built-in `sqlite3` behind a clean repository interface (`URLRepository`). Zero external dependencies, survives server restarts during reviewer testing, supports clean SQL aggregations for unique visits and referrer breakdowns, and swaps easily to `:memory:` or temporary DB files in tests.
2. **Approach 2: Pure In-Memory Python Dict**
   - *Tradeoffs*: Marginally less code, but loses state on server restart/reload and requires manual Python aggregation for BI stats.
3. **Approach 3: FastAPI + SQLAlchemy + Alembic**
   - *Tradeoffs*: Production-style ORM, but adds unnecessary boilerplate for a 3–5 hour take-home prototype.

## 5. Detailed Technical Design

### 5.1 Directory Structure
```text
courtsite-url-shortener/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app factory, lifespan, exception handlers
│   ├── config.py          # Settings (DB path, timeouts, key length, base URL)
│   ├── schemas.py         # Pydantic request/response models
│   ├── repository.py      # SQLite repository for URLs and visit analytics
│   ├── validator.py       # URL syntax, SSRF check, and live HTTP reachability
│   ├── keygen.py          # Cryptographically random Base62 key generator
│   └── routes.py          # API endpoints (/shorten_url/, /shorten_url/{key}, /shorten_url/{key}/stats)
├── tests/
│   ├── __init__.py
│   ├── conftest.py        # Test fixtures (isolated DB, mocked URL validator/transport)
│   ├── test_shorten.py    # POST /shorten_url/ unit & integration tests
│   ├── test_redirect.py   # GET /shorten_url/<key> redirect & 404 tests
│   ├── test_validator.py  # URL syntax, SSRF, and reachability edge-case tests
│   └── test_analytics.py  # BI visit tracking & unique visitor aggregation tests
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-11-18-courtsite-url-shortener-design.md
├── requirements.txt
├── pyproject.toml
├── .gitignore
└── README.md              # Run guide, API reference, and Engineering Evaluation
```

### 5.2 Data Model (SQLite)
Two tables initialized automatically on startup:
1. `short_urls`:
   - `key TEXT PRIMARY KEY` (7-character Base62 string, e.g. `aB3x9Kp`)
   - `original_url TEXT NOT NULL`
   - `location TEXT NULL` (optional physical location tag, e.g. `"Mid Valley Megamall - North Court"`)
   - `campaign TEXT NULL` (optional marketing campaign tag, e.g. `" CNY_2026_Promo"`)
   - `created_at TEXT NOT NULL` (ISO-8601 UTC timestamp)
2. `visits`:
   - `id INTEGER PRIMARY KEY AUTOINCREMENT`
   - `url_key TEXT NOT NULL REFERENCES short_urls(key)`
   - `visitor_hash TEXT NOT NULL` (SHA-256 hash of client IP + User-Agent for privacy-preserving unique visit counting)
   - `referrer TEXT NULL` (HTTP `Referer` header or `?ref=` query param)
   - `user_agent TEXT NULL` (HTTP `User-Agent` header)
   - `visited_at TEXT NOT NULL` (ISO-8601 UTC timestamp)

### 5.3 Endpoint Specifications

#### 1. `POST /shorten_url/` (and alias `POST /shorten_url` for trailing-slash tolerance)
- **Request Body**:
  ```json
  {
    "url": "https://www.courtsite.my/locations",
    "location": "Suria KLCC - Level 3 QR Standee",
    "campaign": "pickleball-launch"
  }
  ```
  *Note*: Only `url` is required. `location` and `campaign` are optional fields supporting the marketing team's physical QR tracking use case.
- **Validation Pipeline**:
  1. **Syntax check**: Must parse as a valid URL with scheme `http` or `https` and a valid hostname. Returns `422 Unprocessable Entity` (or `400 Bad Request` with clear detail) if invalid.
  2. **SSRF & Loop Guard**: Resolve hostname via DNS (`socket.getaddrinfo`) and reject loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`), and self-referential URLs pointing to the shortener service itself.
  3. **Live Reachability Check**: Send an async `HEAD` request via `httpx.AsyncClient(follow_redirects=True, timeout=5.0)` with a standard browser-like `User-Agent`.
     - If the server returns `405 Method Not Allowed` or `403 Forbidden` on `HEAD`, automatically fall back to a streaming `GET` request (reading only headers, closing immediately so large files aren't downloaded).
     - Any status `< 400` (or `401`/`403` from anti-bot walls like Cloudflare when the TCP/TLS handshake and HTTP response succeeded? *Builder call*: treat `2xx`–`3xx` as accessible, and also allow configurable tolerance or clear error messages when a server returns `4xx`/`5xx` or times out). Specifically: `2xx` and `3xx` pass; `405` on HEAD retries with GET; connection errors, DNS failures, timeouts, and `404`/`5xx` fail validation with `400 Bad Request` (`{"detail": "The provided URL is not accessible: ..."}`).
- **Key Generation**:
  - Generates a New Unique Key per request (even for duplicate URLs) using cryptographically secure `secrets.choice` over Base62 (`0-9`, `a-z`, `A-Z`), length 7 (~3.52 trillion combinations). Retries on collision up to 5 times.
- **Response (`201 Created` or `200 OK` — we use `201 Created`)**:
  ```json
  {
    "key": "k9Xm2Pq",
    "short_url": "http://localhost:8000/shorten_url/k9Xm2Pq",
    "original_url": "https://www.courtsite.my/locations",
    "location": "Suria KLCC - Level 3 QR Standee",
    "campaign": "pickleball-launch",
    "created_at": "2026-11-18T10:20:00Z"
  }
  ```

#### 2. `GET /shorten_url/<key>`
- Looks up `<key>` in `short_urls`.
- If not found: returns `404 Not Found` with `{"detail": "Short URL key not found"}`.
- If found:
  - Records a visit in `visits` with `visitor_hash = sha256(f"{client_ip}:{user_agent}")`, `referrer` (from `?ref=` query param if present, otherwise `Referer` header), `user_agent`, and UTC timestamp.
  - Returns **`302 Found`** with `Location: <original_url>` and `Cache-Control: no-store, no-cache, must-revalidate` so every QR scan hits the proxy and gets counted.

#### 3. `GET /shorten_url/<key>/stats` (Stretch Goal: BI & Analytics)
- Looks up `<key>` in `short_urls`. Returns `404 Not Found` if missing.
- Returns `200 OK`:
  ```json
  {
    "key": "k9Xm2Pq",
    "original_url": "https://www.courtsite.my/locations",
    "location": "Suria KLCC - Level 3 QR Standee",
    "campaign": "pickleball-launch",
    "created_at": "2026-11-18T10:20:00Z",
    "total_visits": 42,
    "unique_visits": 31,
    "referrers": {
      "direct": 25,
      "https://instagram.com": 17
    },
    "recent_visits": [
      {
        "visited_at": "2026-11-18T10:25:12Z",
        "referrer": "direct",
        "user_agent": "Mozilla/5.0 ..."
      }
    ]
  }
  ```

#### 4. `GET /analytics/locations` (Stretch Goal: Marketing Location Leaderboard)
- Aggregates total and unique visits grouped by `location` and `campaign` across all shortened URLs so the marketing team can immediately answer *"What physical locations are driving traffic?"*.

## 6. What's Left to the Builder
- Exact FastAPI dependency injection structure for swapping the SQLite database path and HTTP reachability validator in `pytest`.
- Concise, well-formatted `README.md` covering setup, API usage (`curl` examples), design choices, and the required evaluation answers.
