import string

import pytest

from app.config import Settings
from app.keygen import generate_key
from app.schemas import (
    LocationStatsItem,
    ShortenRequest,
    ShortenResponse,
    URLStatsResponse,
    VisitRecord,
)

BASE62 = set(string.digits + string.ascii_lowercase + string.ascii_uppercase)


def test_generate_key_default_length_and_charset():
    key = generate_key()
    assert len(key) == 7
    assert set(key).issubset(BASE62)


def test_generate_key_custom_length():
    key = generate_key(length=12)
    assert len(key) == 12
    assert set(key).issubset(BASE62)


def test_generate_key_invalid_length_raises():
    with pytest.raises(ValueError):
        generate_key(0)


def test_generate_key_uniqueness():
    keys = {generate_key() for _ in range(500)}
    assert len(keys) == 500


def test_shorten_request_strips_whitespace():
    req = ShortenRequest(url="  https://courtsite.my  ", location=" KLCC ", campaign=" launch ")
    assert req.url == "https://courtsite.my"
    assert req.location == "KLCC"
    assert req.campaign == "launch"


def test_shorten_request_converts_blank_optionals_to_none():
    req = ShortenRequest(url="https://courtsite.my", location="   ", campaign="")
    assert req.location is None
    assert req.campaign is None


def test_settings_defaults():
    settings = Settings()
    assert settings.database_path == "shortener.db"
    assert settings.key_length == 7
    assert settings.validation_timeout == 5.0
    assert settings.base_url == "http://localhost:8000"


def test_response_schemas_instantiate():
    short = ShortenResponse(
        key="abc1234",
        short_url="http://localhost:8000/shorten_url/abc1234",
        original_url="https://courtsite.my",
        location="KLCC",
        campaign=None,
        created_at="2026-11-18T11:00:00Z",
    )
    visit = VisitRecord(
        visited_at="2026-11-18T11:05:00Z",
        referrer="direct",
        user_agent="Mozilla/5.0",
    )
    stats = URLStatsResponse(
        key=short.key,
        original_url=short.original_url,
        location=short.location,
        campaign=short.campaign,
        created_at=short.created_at,
        total_visits=1,
        unique_visits=1,
        referrers={"direct": 1},
        recent_visits=[visit],
    )
    leaderboard_item = LocationStatsItem(
        location="KLCC",
        campaign=None,
        total_links=1,
        total_visits=1,
        unique_visits=1,
    )
    assert stats.recent_visits[0].referrer == "direct"
    assert leaderboard_item.total_links == 1
