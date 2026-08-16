from datetime import datetime, timedelta
from pathlib import Path

from wikifame.db import Database
from wikifame.models import utcnow
from wikifame.repository import Repository


def make_repository(tmp_path: Path) -> Repository:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.create_schema()
    return Repository(database)


def test_enqueue_deduplicates_and_boosts_priority(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    repository.enqueue("frwiki", 10, 20, "v1", priority=10)
    repository.enqueue("frwiki", 10, 20, "v1", priority=100)

    assert repository.stats()["pending"] == 1
    work = repository.get_work("frwiki", 10, 20, "v1")
    assert work is not None
    assert work.priority == 100


def test_new_revision_supersedes_old_pending_work(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    repository.enqueue("frwiki", 10, 20, "v1", priority=10)
    repository.enqueue("frwiki", 10, 21, "v1", priority=10)

    old = repository.get_work("frwiki", 10, 20, "v1")
    new = repository.get_work("frwiki", 10, 21, "v1")
    assert old is not None and old.state == "superseded"
    assert new is not None and new.state == "pending"


def test_stale_request_does_not_supersede_newer_pending_work(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    repository.enqueue("frwiki", 10, 21, "v1", priority=10)
    repository.enqueue("frwiki", 10, 20, "v1", priority=100)

    current = repository.get_work("frwiki", 10, 21, "v1")
    stale = repository.get_work("frwiki", 10, 20, "v1")
    assert current is not None and current.state == "pending"
    assert stale is None


def test_expired_lease_can_be_reclaimed(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    repository.enqueue("frwiki", 10, 20, "v1", priority=10)

    first = repository.claim("worker-1", lease_seconds=-1)
    second = repository.claim("worker-2", lease_seconds=60)

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert second.attempts == 2


def test_cleanup_keeps_latest_result_even_when_it_is_old(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    def save(page_id: int, revision_id: int, computed_at: datetime) -> None:
        repository.save_result(
            {
                "wiki": "frwiki",
                "page_id": page_id,
                "revision_id": revision_id,
                "algorithm_version": "v1",
                "title": f"Page {page_id}",
                "metric": "test",
                "contributors": [],
                "distinct_contributors": 1,
                "count_limited": False,
                "countable_tokens": 1,
                "wikiwho_revision_id": revision_id,
                "computed_at": computed_at,
            }
        )

    old = utcnow() - timedelta(days=60)
    save(10, 20, old)
    save(10, 21, utcnow())
    save(11, 30, old)

    removed = repository.cleanup(superseded_result_days=30)

    assert removed["results"] == 1
    assert repository.get_result("frwiki", 10, 20, "v1") is None
    assert repository.get_result("frwiki", 10, 21, "v1") is not None
    assert repository.get_result("frwiki", 11, 30, "v1") is not None
