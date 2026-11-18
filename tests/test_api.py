from __future__ import annotations

import pytest

from app import routes
from app.main import create_app
from app.repository import URLRepository
from app.validator import URLValidator


def test_shorten_valid_url_creates_unique_keys_per_request(client):
    r1 = client.post(
        "/shorten_url/",
        json={"url": "https://www.courtsite.my/book", "location": "KLCC", "campaign": "qr1"},
    )
    r2 = client.post(
        "/shorten_url/",
        json={"url": "https://www.courtsite.my/book", "location": "Mid Valley", "campaign": "qr2"},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    data1, data2 = r1.json(), r2.json()
    assert data1["key"] != data2["key"]
    assert data1["short_url"].endswith(f"/shorten_url/{data1['key']}")
    assert data1["original_url"] == "https://www.courtsite.my/book"
    assert data1["location"] == "KLCC"
    assert data1["campaign"] == "qr1"


def test_shorten_without_trailing_slash_returns_201_directly(client):
    res = client.post(
        "/shorten_url",
        json={"url": "https://www.courtsite.my/courts"},
        follow_redirects=False,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["original_url"] == "https://www.courtsite.my/courts"
    assert body["short_url"].endswith(f"/shorten_url/{body['key']}")


def test_shorten_rejects_unreachable_and_invalid_urls(client):
    r_bad = client.post("/shorten_url/", json={"url": "not-a-valid-url"})
    assert r_bad.status_code == 400
    r_dead = client.post("/shorten_url/", json={"url": "https://dead.example.com/404"})
    assert r_dead.status_code == 400
    r_ssrf = client.post("/shorten_url/", json={"url": "http://127.0.0.1:8000/admin"})
    assert r_ssrf.status_code == 400


def test_shorten_rejects_self_referential_shortener_urls(client):
    r_testserver = client.post(
        "/shorten_url/",
        json={"url": "http://testserver/shorten_url/abc1234"},
    )
    assert r_testserver.status_code == 400
    assert "shortener service" in r_testserver.json()["detail"].lower()

    r_base = client.post(
        "/shorten_url/",
        json={"url": "http://localhost:8000/shorten_url/abc1234"},
    )
    assert r_base.status_code == 400
    assert "shortener service" in r_base.json()["detail"].lower()


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
    client.get(
        f"/shorten_url/{key}",
        headers={"User-Agent": "PhoneA", "Referer": "https://instagram.com"},
        follow_redirects=False,
    )
    client.get(
        f"/shorten_url/{key}",
        headers={"User-Agent": "PhoneA", "Referer": "https://instagram.com"},
        follow_redirects=False,
    )
    client.get(
        f"/shorten_url/{key}?ref=qr_poster_lobby",
        headers={"User-Agent": "PhoneB"},
        follow_redirects=False,
    )

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


def test_stats_404_and_health_endpoint(client):
    missing_stats = client.get("/shorten_url/doesnotexist/stats")
    assert missing_stats.status_code == 404

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_forwarded_ip_determines_visitor_hash(client):
    created = client.post(
        "/shorten_url/",
        json={"url": "https://www.courtsite.my/venue"},
    ).json()
    key = created["key"]

    client.get(
        f"/shorten_url/{key}",
        headers={"User-Agent": "SameUA", "X-Forwarded-For": "203.0.113.10, 10.0.0.1"},
        follow_redirects=False,
    )
    client.get(
        f"/shorten_url/{key}",
        headers={"User-Agent": "SameUA", "X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
        follow_redirects=False,
    )
    client.get(
        f"/shorten_url/{key}",
        headers={"User-Agent": "SameUA", "X-Forwarded-For": "203.0.113.99"},
        follow_redirects=False,
    )

    stats = client.get(f"/shorten_url/{key}/stats").json()
    assert stats["total_visits"] == 3
    assert stats["unique_visits"] == 2


def test_key_collision_retries_before_succeeding(
    repo: URLRepository,
    validator: URLValidator,
    monkeypatch: pytest.MonkeyPatch,
):
    repo.create_short_url("dupkey1", "https://www.courtsite.my/existing")
    keys = iter(["dupkey1", "dupkey1", "fresh99"])
    monkeypatch.setattr(routes, "generate_key", lambda length=7: next(keys))

    app = create_app(repo=repo, validator=validator)
    from fastapi.testclient import TestClient

    with TestClient(app) as local_client:
        res = local_client.post("/shorten_url/", json={"url": "https://www.courtsite.my/new"})
    assert res.status_code == 201
    assert res.json()["key"] == "fresh99"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://[::1/",
        "https://..com/",
        "https://example.com/foo\nbar",
        "http://100.64.0.1/",
    ],
)
def test_shorten_returns_400_for_malformed_idna_invalid_url_and_cgnat(client, bad_url: str):
    res = client.post("/shorten_url/", json={"url": bad_url})
    assert res.status_code == 400
    assert res.json()["detail"]


def test_shorten_missing_url_returns_422(client):
    res = client.post("/shorten_url/", json={})
    assert res.status_code == 422


def test_self_referential_path_boundary_and_whitespace_ref_fallback(client):
    r_exact = client.post(
        "/shorten_url/",
        json={"url": "http://testserver/shorten_url"},
    )
    assert r_exact.status_code == 400

    r_similar = client.post(
        "/shorten_url/",
        json={"url": "http://testserver/shorten_url_guide"},
    )
    # testserver is not blocked by MockTransport/fake_getaddrinfo unless self-referential
    assert r_similar.status_code == 201

    created = client.post("/shorten_url/", json={"url": "https://www.courtsite.my/ref"}).json()
    key = created["key"]
    client.get(
        f"/shorten_url/{key}?ref=%20%20%20",
        headers={"Referer": "https://partner.example.com"},
        follow_redirects=False,
    )
    stats = client.get(f"/shorten_url/{key}/stats").json()
    assert stats["referrers"] == {"https://partner.example.com": 1}
