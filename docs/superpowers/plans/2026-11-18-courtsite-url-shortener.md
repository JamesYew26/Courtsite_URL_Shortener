# Courtsite URL Shortener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete, well-tested URL shortener web service prototype in Python/FastAPI with live URL reachability validation, SSRF protection, 302 redirect mechanics, and BI visit tracking for physical QR code campaigns.

**Architecture:** Modular FastAPI application backed by Python's built-in `sqlite3` behind a clean `URLRepository` interface. Incoming `POST /shorten_url/` requests validate URL syntax, resolve DNS to block private/loopback IPs (SSRF protection), verify live HTTP reachability (`HEAD` with fallback to streaming `GET`), and issue unique 7-character Base62 keys. Visiting `GET /shorten_url/<key>` logs privacy-preserving visitor analytics (`SHA-256(ip + user_agent)`, referrer, location/campaign metadata) and returns a non-cached `302 Found` redirect.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, `httpx`, `sqlite3`, `uvicorn`, `pytest`, `pytest-asyncio`

**Spec:** `docs/superpowers/specs/2026-11-18-courtsite-url-shortener-design.md`

## Global Constraints

- Project root: `/Users/jamesyew/.gemini/antigravity/scratch/courtsite-url-shortener`
- Language & Stack: Python 3.11+, FastAPI, Pydantic v2, `httpx`, `sqlite3`, `pytest`
- `POST /shorten_url/` (and `/shorten_url`) accepts JSON payload with `"url"` (required) and optional `"location"` and `"campaign"` strings; returns `201 Created` with `key`, `short_url`, `original_url`, `location`, `campaign`, `created_at`.
- `POST /shorten_url/` must validate that the original URL is syntactically valid (`http`/`https`), not an SSRF target (loopback/private/link-local IPs blocked), and accessible via HTTP (`HEAD` with 5s timeout, falling back to streaming `GET` on `405`/`403`). Invalid/inaccessible URLs return `400 Bad Request` (or `422` for missing `url` field).
- `GET /shorten_url/<key>` returns `302 Found` with `Location: <original_url>` and `Cache-Control: no-store, no-cache, must-revalidate`, or `404 Not Found` if `<key>` does not exist.
- Unique short keys are 7-character cryptographically random Base62 (`0-9a-zA-Z`) generated per request (no deduplication across requests, allowing separate QR codes per location).
- Automated tests in `tests/` must be deterministic and never depend on live external internet connectivity (use custom `httpx.AsyncBaseTransport` / dependency injection in tests).

## Review Focus

1. **Servers rejecting `HEAD` with `405 Method Not Allowed` or `403 Forbidden`**: Validator must fall back to a streaming `GET` request and succeed if `GET` returns `2xx`–`3xx`.
2. **SSRF via localhost or private network IPs (`http://127.0.0.1:8000/admin`, `http://192.168.1.1`)**: Validator must resolve hostnames and reject loopback, private, and link-local IP ranges before making any HTTP request.
3. **Browser caching of redirects bypassing BI tracking**: `GET /shorten_url/<key>` must return `302 Found` with `Cache-Control: no-store, no-cache, must-revalidate` so repeat scans from the same phone are counted.
4. **Trailing-slash sensitivity on `POST /shorten_url/` vs `POST /shorten_url`**: Both must work seamlessly without failing or losing request bodies.
5. **Privacy & accuracy of unique visit counts**: Multiple visits from the same IP + User-Agent increment `total_visits` on every hit but increment `unique_visits` only once; different User-Agents or IPs count as distinct unique visitors.

---

### Task 1: Project Scaffolding, Configuration, Schemas, and Key Generator

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/schemas.py`
- Create: `app/keygen.py`
- Create: `tests/__init__.py`
- Create: `tests/test_keygen.py`

**Interfaces:**
- Produces:
  - `app.config.Settings` with `database_path: str = "shortener.db"`, `key_length: int = 7`, `validation_timeout: float = 5.0`, `base_url: str = "http://localhost:8000"`
  - `app.keygen.generate_key(length: int = 7) -> str` (cryptographically random Base62 `0-9a-zA-Z`)
  - `app.schemas.ShortenRequest(url: str, location: str | None = None, campaign: str | None = None)`
  - `app.schemas.ShortenResponse(key: str, short_url: str, original_url: str, location: str | None, campaign: str | None, created_at: str)`
  - `app.schemas.VisitRecord(visited_at: str, referrer: str, user_agent: str | None)`
  - `app.schemas.URLStatsResponse(key: str, original_url: str, location: str | None, campaign: str | None, created_at: str, total_visits: int, unique_visits: int, referrers: dict[str, int], recent_visits: list[VisitRecord])`
  - `app.schemas.LocationStatsItem(location: str, campaign: str | None, total_links: int, total_visits: int, unique_visits: int)`

- [ ] **Step 1: Set up virtual environment, `requirements.txt`, `pyproject.toml`, and `.gitignore`**

Create `requirements.txt` with `fastapi>=0.110.0`, `uvicorn[standard]>=0.29.0`, `httpx>=0.27.0`, `pydantic>=2.6.0`, `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`. Set up `.venv` and install dependencies.

- [ ] **Step 2: Write failing tests in `tests/test_keygen.py`**

```python
import string
from app.keygen import generate_key
from app.schemas import ShortenRequest

BASE62 = set(string.digits + string.ascii_lowercase + string.ascii_uppercase)

def test_generate_key_default_length_and_charset():
    key = generate_key()
    assert len(key) == 7
    assert set(key).issubset(BASE62)

def test_generate_key_uniqueness():
    keys = {generate_key() for _ in range(500)}
    assert len(keys) == 500

def test_shorten_request_strips_whitespace():
    req = ShortenRequest(url="  https://courtsite.my  ", location=" KLCC ", campaign=" launch ")
    assert req.url == "https://courtsite.my"
    assert req.location == "KLCC"
    assert req.campaign == "launch"
```

- [ ] **Step 3: Run `pytest tests/test_keygen.py -v` to verify it fails**

Run: `.venv/bin/pytest tests/test_keygen.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement `app/config.py`, `app/keygen.py`, and `app/schemas.py`**

Implement `generate_key(length: int = 7) -> str` using `secrets.choice` over `string.digits + string.ascii_lowercase + string.ascii_uppercase`. In `ShortenRequest`, strip whitespace on `url`, `location`, and `campaign` (converting empty optional strings to `None`).

- [ ] **Step 5: Run `pytest tests/test_keygen.py -v` to verify it passes**

Run: `.venv/bin/pytest tests/test_keygen.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pyproject.toml .gitignore app/ tests/test_keygen.py
git commit -m "feat: add project config, schemas, and Base62 key generator"
```

---

### Task 2: SQLite Repository for URL Mappings and BI Analytics

**Files:**
- Create: `app/repository.py`
- Create: `tests/test_repository.py`

**Interfaces:**
- Consumes: `app.schemas.VisitRecord`, `app.schemas.URLStatsResponse`, `app.schemas.LocationStatsItem`
- Produces:
  - `class URLRepository`:
    - `__init__(self, db_path: str = "shortener.db") -> None`
    - `init_db(self) -> None`
    - `create_short_url(self, key: str, original_url: str, location: str | None = None, campaign: str | None = None) -> dict` (raises `KeyCollisionError` on duplicate `key`)
    - `get_short_url(self, key: str) -> dict | None`
    - `record_visit(self, key: str, visitor_hash: str, referrer: str | None, user_agent: str | None) -> None`
    - `get_url_stats(self, key: str) -> URLStatsResponse | None`
    - `get_location_leaderboard(self) -> list[LocationStatsItem]`
  - `class KeyCollisionError(Exception)`

- [ ] **Step 1: Write failing tests in `tests/test_repository.py`**

```python
import pytest
from app.repository import URLRepository, KeyCollisionError

def test_create_and_get_short_url(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()
    record = repo.create_short_url("abc1234", "https://courtsite.my", location="KLCC", campaign="q1")
    assert record["key"] == "abc1234"
    assert record["original_url"] == "https://courtsite.my"
    fetched = repo.get_short_url("abc1234")
    assert fetched == record
    assert repo.get_short_url("missing") is None

def test_key_collision_raises(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()
    repo.create_short_url("dup1234", "https://courtsite.my")
    with pytest.raises(KeyCollisionError):
        repo.create_short_url("dup1234", "https://courtsite.my/other")

def test_visit_tracking_and_unique_counts(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()
    repo.create_short_url("trk1234", "https://courtsite.my", location="Mid Valley", campaign="promo")
    repo.record_visit("trk1234", visitor_hash="hash_a", referrer=None, user_agent="iPhone")
    repo.record_visit("trk1234", visitor_hash="hash_a", referrer="https://instagram.com", user_agent="iPhone")
    repo.record_visit("trk1234", visitor_hash="hash_b", referrer="https://instagram.com", user_agent="Android")

    stats = repo.get_url_stats("trk1234")
    assert stats is not None
    assert stats.total_visits == 3
    assert stats.unique_visits == 2
    assert stats.referrers == {"direct": 1, "https://instagram.com": 2}
    assert len(stats.recent_visits) == 3

    leaderboard = repo.get_location_leaderboard()
    assert len(leaderboard) == 1
    assert leaderboard[0].location == "Mid Valley"
    assert leaderboard[0].total_visits == 3
    assert leaderboard[0].unique_visits == 2
```

- [ ] **Step 2: Run `pytest tests/test_repository.py -v` to verify it fails**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.repository'`

- [ ] **Step 3: Implement `URLRepository` in `app/repository.py`**

Use `sqlite3` with context-managed connections. Normalize `None` or empty `referrer` to `"direct"` when recording or aggregating visits. Group location leaderboard by `(COALESCE(location, 'Unspecified'), campaign)` ordered by `total_visits DESC`.

- [ ] **Step 4: Run `pytest tests/test_repository.py -v` to verify it passes**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/repository.py tests/test_repository.py
git commit -m "feat: add SQLite URLRepository with BI visit and location analytics"
```

---

### Task 3: URL Syntax, SSRF Guard, and Live Reachability Validator

**Files:**
- Create: `app/validator.py`
- Create: `tests/test_validator.py`

**Interfaces:**
- Produces:
  - `class URLValidationError(Exception)` (with `.message: str`)
  - `class URLValidator`:
    - `__init__(self, timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None, allow_private_ips: bool = False) -> None`
    - `async def validate(self, url: str) -> str` (returns normalized URL string if valid and accessible; raises `URLValidationError` with descriptive reason otherwise)

- [ ] **Step 1: Write failing tests in `tests/test_validator.py`**

```python
import httpx
import pytest
from app.validator import URLValidator, URLValidationError

@pytest.mark.asyncio
async def test_rejects_invalid_syntax_and_schemes():
    validator = URLValidator()
    for bad_url in ["not-a-url", "ftp://files.example.com", "javascript:alert(1)", "http://", ""]:
        with pytest.raises(URLValidationError):
            await validator.validate(bad_url)

@pytest.mark.asyncio
async def test_rejects_ssrf_localhost_and_private_ips():
    validator = URLValidator()
    for internal_url in [
        "http://localhost:8000/shorten_url/",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/router",
        "http://10.0.0.1/internal",
    ]:
        with pytest.raises(URLValidationError, match="private|local|internal|restricted"):
            await validator.validate(internal_url)

@pytest.mark.asyncio
async def test_head_success_and_get_fallback_on_405():
    calls = []
    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.host == "head-ok.example.com":
            return httpx.Response(200)
        if request.url.host == "no-head.example.com":
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(200)
        if request.url.host == "dead.example.com":
            return httpx.Response(404)
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    validator = URLValidator(transport=transport, allow_private_ips=True)

    assert await validator.validate("https://head-ok.example.com/page") == "https://head-ok.example.com/page"
    assert await validator.validate("https://no-head.example.com/post") == "https://no-head.example.com/post"
    assert ("HEAD", "https://no-head.example.com/post") in calls
    assert ("GET", "https://no-head.example.com/post") in calls

    with pytest.raises(URLValidationError, match="404"):
        await validator.validate("https://dead.example.com/missing")

    with pytest.raises(URLValidationError, match="not accessible|Connection"):
        await validator.validate("https://unreachable.example.com")
```

- [ ] **Step 2: Run `pytest tests/test_validator.py -v` to verify it fails**

Run: `.venv/bin/pytest tests/test_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.validator'`

- [ ] **Step 3: Implement `URLValidator` in `app/validator.py`**

1. Parse with `urllib.parse.urlparse`. Require scheme in `{"http", "https"}` and non-empty `hostname`.
2. Unless `allow_private_ips=True`, check if `hostname` is `"localhost"` or ends with `.local` / `.internal`, and resolve via `socket.getaddrinfo` (in `asyncio.to_thread`), checking every returned IP with `ipaddress.ip_address(ip)`: reject `is_loopback`, `is_private`, `is_link_local`, `is_multicast`, `is_reserved`, or `is_unspecified`.
3. Use `httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, transport=self.transport, headers={"User-Agent": "CourtsiteURLShortener/1.0 (+https://courtsite.my)"})`. Send `HEAD`; if status is `405` or `403`, send `client.stream("GET", url)` and inspect status code before closing. If final status `< 400`, return `url`; otherwise raise `URLValidationError(f"URL returned HTTP {status_code}")`. Catch `httpx.HTTPError` and raise `URLValidationError`.

- [ ] **Step 4: Run `pytest tests/test_validator.py -v` to verify it passes**

Run: `.venv/bin/pytest tests/test_validator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/validator.py tests/test_validator.py
git commit -m "feat: implement URL syntax, SSRF guard, and HTTP reachability validator"
```

---

### Task 4: FastAPI Endpoints (`POST /shorten_url/`, `GET /shorten_url/<key>`, BI Analytics) & Integration Tests

**Files:**
- Create: `app/routes.py`
- Create: `app/main.py`
- Create: `tests/conftest.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `URLRepository`, `URLValidator`, `generate_key`, schemas from `app.schemas`
- Produces:
  - `app.main.create_app(repo: URLRepository | None = None, validator: URLValidator | None = None) -> FastAPI`
  - `app.main.app` (default `FastAPI` instance for `uvicorn app.main:app`)
  - Endpoints:
    - `POST /shorten_url/` and `POST /shorten_url` -> `201 Created` (`ShortenResponse`) or `400 Bad Request` (`{"detail": ...}`)
    - `GET /shorten_url/{key}` -> `302 Found` with `Location` and `Cache-Control: no-store, no-cache, must-revalidate`, or `404 Not Found`
    - `GET /shorten_url/{key}/stats` -> `200 OK` (`URLStatsResponse`) or `404 Not Found`
    - `GET /analytics/locations` -> `200 OK` (`list[LocationStatsItem]`)
    - `GET /health` -> `200 OK` (`{"status": "ok"}`)

- [ ] **Step 1: Write failing integration tests in `tests/conftest.py` and `tests/test_api.py`**

```python
# tests/test_api.py
def test_shorten_valid_url_creates_unique_keys_per_request(client):
    r1 = client.post("/shorten_url/", json={"url": "https://www.courtsite.my/book", "location": "KLCC", "campaign": "qr1"})
    r2 = client.post("/shorten_url/", json={"url": "https://www.courtsite.my/book", "location": "Mid Valley", "campaign": "qr2"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    data1, data2 = r1.json(), r2.json()
    assert data1["key"] != data2["key"]
    assert data1["short_url"].endswith(f"/shorten_url/{data1['key']}")
    assert data1["original_url"] == "https://www.courtsite.my/book"

def test_shorten_rejects_unreachable_and_invalid_urls(client):
    r_bad = client.post("/shorten_url/", json={"url": "not-a-valid-url"})
    assert r_bad.status_code == 400
    r_dead = client.post("/shorten_url/", json={"url": "https://dead.example.com/404"})
    assert r_dead.status_code == 400
    r_ssrf = client.post("/shorten_url/", json={"url": "http://127.0.0.1:8000/admin"})
    assert r_ssrf.status_code == 400

def test_redirect_existing_key_and_404_on_missing(client):
    created = client.post("/shorten_url/", json={"url": "https://www.courtsite.my"}).json()
    key = created["key"]

    res = client.get(f"/shorten_url/{key}", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "https://www.courtsite.my"
    assert "no-store" in res.headers.get("cache-control", "")

    missing = client.get("/shorten_url/nonexistent", follow_redirects=False)
    assert missing.status_code == 404

def test_bi_visit_tracking_and_location_leaderboard(client):
    created = client.post(
        "/shorten_url/",
        json={"url": "https://www.courtsite.my/promo", "location": "1 Utama Mall", "campaign": "summer"},
    ).json()
    key = created["key"]

    # Same visitor twice, then a second visitor with custom ref query param
    client.get(f"/shorten_url/{key}", headers={"User-Agent": "PhoneA", "Referer": "https://instagram.com"}, follow_redirects=False)
    client.get(f"/shorten_url/{key}", headers={"User-Agent": "PhoneA", "Referer": "https://instagram.com"}, follow_redirects=False)
    client.get(f"/shorten_url/{key}?ref=qr_poster_lobby", headers={"User-Agent": "PhoneB"}, follow_redirects=False)

    stats = client.get(f"/shorten_url/{key}/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["total_visits"] == 3
    assert body["unique_visits"] == 2
    assert body["referrers"] == {"https://instagram.com": 2, "qr_poster_lobby": 1}

    locations = client.get("/analytics/locations")
    assert locations.status_code == 200
    assert locations.json()[0]["location"] == "1 Utama Mall"
    assert locations.json()[0]["total_visits"] == 3
```

- [ ] **Step 2: Run `pytest tests/test_api.py -v` to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Implement `app/routes.py`, `app/main.py`, and `tests/conftest.py`**

Implement `create_app` and route handlers. Compute `visitor_hash = hashlib.sha256(f"{client_ip}:{user_agent}".encode()).hexdigest()[:16]`. Support `?ref=` query param overriding the HTTP `Referer` header when recording visits so QR codes with `?ref=` tags work even when native camera apps strip HTTP `Referer` headers.

- [ ] **Step 4: Run full `pytest` suite to verify all tests pass**

Run: `.venv/bin/pytest -v`
Expected: PASS (all unit and integration tests green)

- [ ] **Step 5: Commit**

```bash
git add app/routes.py app/main.py tests/conftest.py tests/test_api.py
git commit -m "feat: implement FastAPI shorten, redirect, and BI analytics endpoints"
```

---

### Task 5: Comprehensive Documentation (`README.md`) & End-to-End Smoke Verification

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: All endpoints and modules from Tasks 1–4
- Produces: Complete `README.md` satisfying every Supporting Requirement and Stretch Goal explanation in the Courtsite brief.

- [ ] **Step 1: Write `README.md` covering all required sections**

Include:
1. **Overview & Architecture**: Brief summary of the FastAPI + SQLite Repository prototype.
2. **Prerequisites & Running the Service**: Step-by-step setup (`python3 -m venv .venv`, `pip install -r requirements.txt`, `uvicorn app.main:app --reload`) and running automated tests (`pytest -v`).
3. **API Reference & Design Decisions**:
   - `POST /shorten_url/` response envelope choice (`201 Created` with `key`, `short_url`, `original_url`, `location`, `campaign`, `created_at`) and why unique keys are generated per request (enables location-specific QR tracking for the same destination URL).
   - URL validation & accessibility pipeline (syntax check + SSRF DNS/IP guard + `HEAD` with `GET` fallback).
   - `GET /shorten_url/<key>` redirect status code choice (`302 Found` + `Cache-Control: no-store` vs `301 Moved Permanently`) and why `302` is essential for accurate visit analytics.
   - Stretch Goal BI endpoints (`GET /shorten_url/<key>/stats` and `GET /analytics/locations`).
4. **Engineering Evaluation (Required by Brief)**:
   - *What challenges did you run into?* (e.g., false-negative `405`/`403` responses when validating live URLs with `HEAD` only; SSRF risks when fetching user-supplied URLs; mobile QR scanners omitting HTTP `Referer` headers).
   - *What assumptions did you make?* (e.g., generating a distinct key per `POST` request so marketing can place different QR codes for the same destination URL at different malls; privacy-preserving visitor hashing via `SHA-256(IP + User-Agent)`).
   - *What would you improve with more time?* (e.g., async background worker queue for analytics ingestion, Redis cache layer in front of PostgreSQL, rate limiting, custom aliases, link expiration, QR image generation endpoint).
   - *Roughly how long did you spend?*

- [ ] **Step 2: Run full test suite and verify README commands**

Run: `.venv/bin/pytest -v`
Expected: All tests pass with 0 warnings/errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add run instructions, API design rationale, and engineering evaluation"
```
