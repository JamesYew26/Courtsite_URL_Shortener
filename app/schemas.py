from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator


class ShortenRequest(BaseModel):
    """Payload for creating a shortened URL."""

    url: str
    location: Optional[str] = None
    campaign: Optional[str] = None

    @field_validator("url", mode="before")
    @classmethod
    def strip_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("location", "campaign", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class ShortenResponse(BaseModel):
    """Response returned when a short URL is created."""

    key: str
    short_url: str
    original_url: str
    location: Optional[str] = None
    campaign: Optional[str] = None
    created_at: str


class VisitRecord(BaseModel):
    """Individual redirect visit record for BI reporting."""

    visited_at: str
    referrer: str
    user_agent: Optional[str] = None


class URLStatsResponse(BaseModel):
    """Aggregated visit statistics for a single short URL."""

    key: str
    original_url: str
    location: Optional[str] = None
    campaign: Optional[str] = None
    created_at: str
    total_visits: int
    unique_visits: int
    referrers: Dict[str, int]
    recent_visits: List[VisitRecord]


class LocationStatsItem(BaseModel):
    """Aggregated campaign/location leaderboard metrics."""

    location: str
    campaign: Optional[str] = None
    total_links: int
    total_visits: int
    unique_visits: int
