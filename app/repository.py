from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
import threading
from typing import Any, Iterator, Optional

from app.schemas import LocationStatsItem, URLStatsResponse, VisitRecord


class KeyCollisionError(Exception):
    """Raised when attempting to create a short URL with an existing key."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_referrer(referrer: Optional[str]) -> str:
    if referrer is None:
        return "direct"
    stripped = referrer.strip()
    return stripped if stripped else "direct"


class URLRepository:
    """SQLite persistence repository for short URLs and BI visit analytics."""

    def __init__(self, db_path: str = "shortener.db") -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._memory_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._memory_conn = sqlite3.connect(
                ":memory:", check_same_thread=False, timeout=10.0
            )
            self._memory_conn.row_factory = sqlite3.Row
            self._memory_conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            with self._lock:
                with self._memory_conn:
                    yield self._memory_conn
            return

        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create database tables and indexes if they do not exist."""
        with self._connect() as conn:
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS short_urls (
                    key TEXT PRIMARY KEY,
                    original_url TEXT NOT NULL,
                    location TEXT NULL,
                    campaign TEXT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url_key TEXT NOT NULL REFERENCES short_urls(key),
                    visitor_hash TEXT NOT NULL,
                    referrer TEXT NULL,
                    user_agent TEXT NULL,
                    visited_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_visits_url_key
                ON visits(url_key)
                """
            )

    def create_short_url(
        self,
        key: str,
        original_url: str,
        location: Optional[str] = None,
        campaign: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a new short URL mapping, raising KeyCollisionError on duplicate key."""
        created_at = _utc_now_iso()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO short_urls (key, original_url, location, campaign, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, original_url, location, campaign, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise KeyCollisionError(f"Short key '{key}' already exists") from exc

        return {
            "key": key,
            "original_url": original_url,
            "location": location,
            "campaign": campaign,
            "created_at": created_at,
        }

    def get_short_url(self, key: str) -> Optional[dict[str, Any]]:
        """Retrieve a short URL mapping by key, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT key, original_url, location, campaign, created_at
                FROM short_urls
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def record_visit(
        self,
        key: str,
        visitor_hash: str,
        referrer: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        """Record a redirect visit for BI analytics."""
        normalized_referrer = _normalize_referrer(referrer)
        visited_at = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO visits (url_key, visitor_hash, referrer, user_agent, visited_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, visitor_hash, normalized_referrer, user_agent, visited_at),
            )

    def get_url_stats(self, key: str) -> Optional[URLStatsResponse]:
        """Return aggregated visit statistics for a short URL, or None if not found."""
        with self._connect() as conn:
            url_row = conn.execute(
                """
                SELECT key, original_url, location, campaign, created_at
                FROM short_urls
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
            if url_row is None:
                return None

            counts_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_visits,
                    COUNT(DISTINCT visitor_hash) AS unique_visits
                FROM visits
                WHERE url_key = ?
                """,
                (key,),
            ).fetchone()

            referrer_rows = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(referrer), ''), 'direct') AS normalized_referrer,
                    COUNT(*) AS visit_count
                FROM visits
                WHERE url_key = ?
                GROUP BY normalized_referrer
                ORDER BY visit_count DESC, normalized_referrer ASC
                """,
                (key,),
            ).fetchall()

            recent_rows = conn.execute(
                """
                SELECT
                    visited_at,
                    COALESCE(NULLIF(TRIM(referrer), ''), 'direct') AS referrer,
                    user_agent
                FROM visits
                WHERE url_key = ?
                ORDER BY id DESC
                LIMIT 50
                """,
                (key,),
            ).fetchall()

        referrers = {
            row["normalized_referrer"]: int(row["visit_count"]) for row in referrer_rows
        }
        recent_visits = [
            VisitRecord(
                visited_at=row["visited_at"],
                referrer=row["referrer"],
                user_agent=row["user_agent"],
            )
            for row in recent_rows
        ]

        return URLStatsResponse(
            key=url_row["key"],
            original_url=url_row["original_url"],
            location=url_row["location"],
            campaign=url_row["campaign"],
            created_at=url_row["created_at"],
            total_visits=int(counts_row["total_visits"]),
            unique_visits=int(counts_row["unique_visits"]),
            referrers=referrers,
            recent_visits=recent_visits,
        )

    def get_location_leaderboard(self) -> list[LocationStatsItem]:
        """Return aggregated link and visit metrics grouped by location and campaign."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(s.location, 'Unspecified') AS location,
                    s.campaign AS campaign,
                    COUNT(DISTINCT s.key) AS total_links,
                    COUNT(v.id) AS total_visits,
                    COUNT(DISTINCT v.visitor_hash) AS unique_visits
                FROM short_urls s
                LEFT JOIN visits v ON v.url_key = s.key
                GROUP BY COALESCE(s.location, 'Unspecified'), s.campaign
                ORDER BY total_visits DESC, total_links DESC
                """
            ).fetchall()

        return [
            LocationStatsItem(
                location=row["location"],
                campaign=row["campaign"],
                total_links=int(row["total_links"]),
                total_visits=int(row["total_visits"]),
                unique_visits=int(row["unique_visits"]),
            )
            for row in rows
        ]
