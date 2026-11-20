# Courtsite URL Shortener — Technical Interview & Architecture Study Guide

This document summarizes the key engineering decisions, HTTP mechanics, security guardrails, and architectural tradeoffs in this codebase.

---

## 1. HTTP Redirect Mechanics (`GET /shorten_url/<key>`)

### Why `302 Found` instead of `301 Moved Permanently`?
- **`301 Moved Permanently`** tells the browser (and intermediate CDNs) to cache the redirect indefinitely. If a user scans a QR code a second time, their phone browser will navigate straight to the destination URL from local cache **without calling our server**, causing us to lose BI visit tracking data.
- **`302 Found`** (paired with `Cache-Control: no-store, no-cache, must-revalidate`) forces the browser to hit our server on **every scan**, ensuring 100% accurate visit analytics.
- **Bonus operational benefit:** If marketing prints 5,000 physical QR posters for a mall and later needs to change the destination URL, a `302` redirect lets us update the target URL in the database without users' phones being stuck on a cached `301` destination.

### How does data flow during `GET /shorten_url/<key>`?
- **No JSON body is sent or returned during a redirect.**
- The user's browser sends an HTTP `GET` request.
- Our server extracts telemetry from **HTTP request headers and query parameters**:
  - Client IP (`X-Forwarded-For` header or socket peer IP)
  - Device/Browser info (`User-Agent` header)
  - Traffic source (`?ref=` query parameter if present, falling back to the HTTP `Referer` header, or `"direct"` if neither is present)
- Our server logs the visit in SQLite and responds with status `302 Found` and header `Location: <original_url>`.

---

## 2. Unique Short Keys Per Request (`POST /shorten_url/`)

### Why generate a new key on every `POST` request instead of deduplicating identical URLs?
- **Physical QR Code Attribution:** Courtsite's marketing team places QR codes at different physical locations (e.g., *Suria KLCC* vs. *Mid Valley Megamall*) that often point to the **exact same destination URL** (`https://www.courtsite.my`).
- When someone scans a physical QR code with their smartphone camera, **native camera apps do not send an HTTP `Referer` header** indicating which mall they are standing in.
- By generating a **unique short key per request** (and attaching optional `location` and `campaign` metadata), marketing can create one short URL per physical mall poster and use `GET /analytics/locations` to see which shopping malls drive the most traffic.

---

## 3. URL Validation, Reachability & SSRF Protection (`app/validator.py`)

### Why use HTTP `HEAD` first with a fallback to streaming `GET`?
1. **Efficiency of `HEAD`:** An HTTP `HEAD` request asks the target server for only the HTTP status code and headers **without downloading the response body**. This makes reachability validation fast and avoids downloading megabytes of HTML or media.
2. **Why the fallback to streaming `GET` is needed:** Many modern servers, CDNs, and WAFs reject `HEAD` requests with `405 Method Not Allowed` or `403 Forbidden`, even though the URL works fine in a browser.
3. **How our fallback works:** Only when `HEAD` returns `405` or `403`, `URLValidator` opens a streaming `GET` request (`client.stream("GET", url)`), inspects the response status code (`< 400`), and closes the stream immediately without downloading the body.

### What is SSRF (Server-Side Request Forgery) and how do we prevent it?
- **What it is:** Because our backend server makes a live HTTP request to whatever URL a user submits in `POST /shorten_url/`, an attacker could submit internal addresses like `http://127.0.0.1:8000/admin`, private LAN IPs (`192.168.1.1`, `10.0.0.1`), or cloud instance metadata endpoints (`http://169.254.169.254/latest/meta-data/`) to probe internal infrastructure.
- **Our 4-layer defense in `app/validator.py` and `app/routes.py`:**
  1. **Self-Referential Loop Guard:** Rejects URLs pointing back to our own `/shorten_url` endpoints.
  2. **Hostname Blocklist:** Rejects `localhost`, `.localhost`, `.local`, and `.internal`.
  3. **DNS Resolution & IP Range Inspection:** Resolves the hostname via `socket.getaddrinfo` and checks every resolved IP with Python's `ipaddress` module (`_is_restricted_ip`), blocking loopback, private, link-local, multicast, reserved, unspecified, CGNAT (`not ip_obj.is_global`), and IPv4-mapped IPv6 (`::ffff:127.0.0.1`) addresses.
  4. **Per-Redirect Hop Guard:** Uses an `httpx` request event hook (`_guard_redirect_request`) to validate **every redirect hop** so a public URL cannot `302` redirect our validator into an internal IP.

---

## 4. Key Generation & Collision Handling (`app/keygen.py` & `app/routes.py`)

### Terminology Check: Random Token Generation vs. Encryption vs. Hashing
- **Random Token Generation (`secrets.choice` in `app/keygen.py`):** Picks 7 random characters from the Base62 alphabet (`0-9`, `a-z`, `A-Z`) using the operating system's cryptographically secure random number generator (CSPRNG). It is **not** encryption (there is no ciphertext or decryption key).
- **One-Way Cryptographic Hashing (`hashlib.sha256` in `app/routes.py`):** Deterministically maps an input (`client_ip:user_agent`) to a fixed-length digest. It is **one-way (irreversible)**, unlike encryption which is two-way.

### Why 7-character Base62 instead of Auto-Incrementing IDs or UUIDs?
- **Keyspace:** $62^7 = 3,521,614,606,208$ (~3.52 trillion combinations).
- **Vs. Auto-Incrementing IDs (`1, 2, 3...`):** Sequential IDs allow anyone to guess `/shorten_url/1`, `/shorten_url/2`, scrape all links, and trigger fake visits that corrupt BI analytics.
- **Vs. 36-character UUIDs:** Long URLs produce dense, complex QR codes that are harder for phone cameras to scan from a distance on physical posters.

### How are Key Collisions handled?
- In SQLite, `short_urls.key` is the `PRIMARY KEY` (enforcing uniqueness at the database level).
- If `generate_key()` ever produces a key that already exists, SQLite raises `sqlite3.IntegrityError`, which `URLRepository.create_short_url()` catches and raises as `KeyCollisionError`.
- In `app/routes.py`, `shorten_url()` loops up to `MAX_KEY_COLLISION_RETRIES = 5` times, generating a fresh Base62 key on collision.

---

## 5. Privacy-Preserving Unique Visitor Tracking (`app/routes.py` & `app/repository.py`)

### Why `SHA-256(client_ip + user_agent)[:16]`?
- **Privacy (GDPR / PDPA Compliance):** Raw IP addresses are Personally Identifiable Information (PII). By storing a one-way `SHA-256` hash instead of the raw IP, even if the database is inspected or leaked, raw customer IPs are never exposed.
- **Why not Cookies?** Mobile browsers and QR scanner webviews frequently block or clear cookies on redirects. Computing the hash server-side from IP + `User-Agent` requires zero cookies or client-side JavaScript.
- **Known Limitations (Great to mention proactively in an interview!):**
  - *Under-counting (False Merge):* Two different people connected to the same shopping mall Wi-Fi (same public NAT IP) using the exact same phone model and OS version (same `User-Agent`) will share the same `visitor_hash`.
  - *Over-counting (False Split):* One person who scans a QR code while on Mall Wi-Fi and scans it again later on 5G mobile data will have two different IP addresses and count as two unique visitors.

---

## 6. How to Scale This Prototype to Millions of Requests in Production

If asked *"How would you evolve this prototype for massive production scale?"*:
1. **Decouple Visit Analytics from the Redirect Path (Async Event Queue):**
   - Right now, `GET /shorten_url/<key>` performs a synchronous SQLite `INSERT INTO visits` before returning the `302` redirect.
   - In production, the redirect endpoint should only read the destination URL and push the visit event asynchronously to a background queue (e.g., **Redis Streams / Kafka / RabbitMQ / Celery** or FastAPI `BackgroundTasks`) so redirect latency stays under `5ms`.
2. **Read-Through Cache (`Redis`) + Managed RDBMS (`PostgreSQL`):**
   - URL shorteners are heavily read-skewed (e.g., 100:1 or 1000:1 reads to writes).
   - Cache hot `key -> original_url` mappings in **Redis** with a TTL, using **PostgreSQL** as the durable primary datastore.
3. **Rate Limiting & Abuse Prevention:**
   - Add IP-based rate limiting (e.g., token bucket via Redis) on `POST /shorten_url/` to prevent bots from spamming outbound reachability checks.
