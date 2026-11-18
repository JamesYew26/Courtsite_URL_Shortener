import sqlite3
import threading

import pytest

from app.repository import KeyCollisionError, URLRepository


def test_create_and_get_short_url(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()
    record = repo.create_short_url(
        "abc1234", "https://courtsite.my", location="KLCC", campaign="q1"
    )
    assert record["key"] == "abc1234"
    assert record["original_url"] == "https://courtsite.my"
    assert record["location"] == "KLCC"
    assert record["campaign"] == "q1"
    assert isinstance(record["created_at"], str) and record["created_at"]

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
    repo.create_short_url(
        "trk1234", "https://courtsite.my", location="Mid Valley", campaign="promo"
    )
    repo.record_visit("trk1234", visitor_hash="hash_a", referrer=None, user_agent="iPhone")
    repo.record_visit(
        "trk1234",
        visitor_hash="hash_a",
        referrer="https://instagram.com",
        user_agent="iPhone",
    )
    repo.record_visit(
        "trk1234",
        visitor_hash="hash_b",
        referrer="https://instagram.com",
        user_agent="Android",
    )

    stats = repo.get_url_stats("trk1234")
    assert stats is not None
    assert stats.key == "trk1234"
    assert stats.original_url == "https://courtsite.my"
    assert stats.location == "Mid Valley"
    assert stats.campaign == "promo"
    assert stats.total_visits == 3
    assert stats.unique_visits == 2
    assert stats.referrers == {"direct": 1, "https://instagram.com": 2}
    assert len(stats.recent_visits) == 3
    assert stats.recent_visits[0].user_agent == "Android"

    leaderboard = repo.get_location_leaderboard()
    assert len(leaderboard) == 1
    assert leaderboard[0].location == "Mid Valley"
    assert leaderboard[0].campaign == "promo"
    assert leaderboard[0].total_links == 1
    assert leaderboard[0].total_visits == 3
    assert leaderboard[0].unique_visits == 2


def test_in_memory_db_persists_across_calls():
    repo = URLRepository(":memory:")
    repo.init_db()
    repo.create_short_url("mem1234", "https://courtsite.my/mem")
    repo.record_visit("mem1234", visitor_hash="h1", referrer="  ", user_agent="Safari")

    fetched = repo.get_short_url("mem1234")
    assert fetched is not None
    assert fetched["original_url"] == "https://courtsite.my/mem"

    stats = repo.get_url_stats("mem1234")
    assert stats is not None
    assert stats.total_visits == 1
    assert stats.referrers == {"direct": 1}


def test_get_url_stats_missing_and_zero_visits(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()
    assert repo.get_url_stats("missing") is None

    repo.create_short_url("zero123", "https://courtsite.my/zero")
    stats = repo.get_url_stats("zero123")
    assert stats is not None
    assert stats.total_visits == 0
    assert stats.unique_visits == 0
    assert stats.referrers == {}
    assert stats.recent_visits == []


def test_recent_visits_capped_at_50(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()
    repo.create_short_url("cap1234", "https://courtsite.my/cap")
    for idx in range(60):
        repo.record_visit(
            "cap1234",
            visitor_hash=f"user_{idx % 10}",
            referrer="" if idx % 2 == 0 else "https://google.com",
            user_agent=f"Agent-{idx}",
        )

    stats = repo.get_url_stats("cap1234")
    assert stats is not None
    assert stats.total_visits == 60
    assert stats.unique_visits == 10
    assert len(stats.recent_visits) == 50
    assert stats.recent_visits[0].user_agent == "Agent-59"
    assert stats.recent_visits[-1].user_agent == "Agent-10"


def test_location_leaderboard_grouping_and_ordering(tmp_path):
    repo = URLRepository(str(tmp_path / "test.db"))
    repo.init_db()

    # Group 1: (Unspecified, None) -> 2 links, 0 visits
    repo.create_short_url("unsp001", "https://courtsite.my/1")
    repo.create_short_url("unsp002", "https://courtsite.my/2")

    # Group 2: (KLCC, q1) -> 1 link, 3 visits, 2 unique
    repo.create_short_url("klcc001", "https://courtsite.my/3", location="KLCC", campaign="q1")
    repo.record_visit("klcc001", "u1", None, "UA1")
    repo.record_visit("klcc001", "u1", "https://x.com", "UA1")
    repo.record_visit("klcc001", "u2", "https://x.com", "UA2")

    # Group 3: (Bangsar, None) -> 1 link, 1 visit, 1 unique
    repo.create_short_url("bgsr001", "https://courtsite.my/4", location="Bangsar", campaign=None)
    repo.record_visit("bgsr001", "u1", None, "UA1")

    leaderboard = repo.get_location_leaderboard()
    assert len(leaderboard) == 3

    assert leaderboard[0].location == "KLCC"
    assert leaderboard[0].campaign == "q1"
    assert leaderboard[0].total_links == 1
    assert leaderboard[0].total_visits == 3
    assert leaderboard[0].unique_visits == 2

    assert leaderboard[1].location == "Bangsar"
    assert leaderboard[1].campaign is None
    assert leaderboard[1].total_links == 1
    assert leaderboard[1].total_visits == 1
    assert leaderboard[1].unique_visits == 1

    assert leaderboard[2].location == "Unspecified"
    assert leaderboard[2].campaign is None
    assert leaderboard[2].total_links == 2
    assert leaderboard[2].total_visits == 0
    assert leaderboard[2].unique_visits == 0


def test_file_db_enables_wal_mode(tmp_path):
    db_file = tmp_path / "wal.db"
    repo = URLRepository(str(db_file))
    repo.init_db()
    with sqlite3.connect(str(db_file)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_memory_repo_concurrent_access():
    repo = URLRepository(":memory:")
    repo.init_db()
    repo.create_short_url("conc001", "https://courtsite.my/concurrent")
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            for idx in range(20):
                repo.record_visit(
                    "conc001",
                    visitor_hash=f"v_{worker_id}_{idx}",
                    referrer=None,
                    user_agent="ThreadTest",
                )
        except Exception as exc:  # pragma: no cover - test assertion catches failures
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    stats = repo.get_url_stats("conc001")
    assert stats is not None
    assert stats.total_visits == 80
