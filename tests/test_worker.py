from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from wikifame.clients import EditorCount, PageMetadata
from wikifame.config import Settings
from wikifame.models import utcnow
from wikifame.policy import ResolvedUser
from wikifame.runtime import build_runtime
from wikifame.worker import Worker


class FakeMediaWiki:
    def get_page(self, _wiki: str, page_id: int) -> PageMetadata:
        return PageMetadata(page_id, 200, "France", 0)

    def resolve_users(self, _wiki: str, user_ids: list[int]) -> dict[int, ResolvedUser]:
        names = {1: "Alice", 2: "Bob", 3: "Charlie"}
        return {user_id: ResolvedUser(user_id, names[user_id], frozenset()) for user_id in user_ids}

    def get_editor_count(self, _wiki: str, _title: str) -> EditorCount:
        return EditorCount(47, False)

    def get_bot_contributor_count(self, _wiki: str, _page_id: int) -> int:
        return 2


class FakeWikiWho:
    def fetch_revision(self, _wiki: str, _revision_id: int) -> list[dict[str, str]]:
        return (
            [{"str": "alpha", "editor": "1"}] * 60
            + [{"str": "beta", "editor": "2"}] * 30
            + [{"str": "gamma", "editor": "3"}] * 10
        )


class NewerRevisionMediaWiki(FakeMediaWiki):
    def get_page(self, _wiki: str, page_id: int) -> PageMetadata:
        return PageMetadata(page_id, 201, "France", 0)


def test_worker_builds_compact_result(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        minimum_tokens=5,
    )
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    runtime.repository.enqueue("frwiki", 100, 200, settings.algorithm_version, 100)
    worker = Worker(runtime, worker_id="test")
    worker.mediawiki = FakeMediaWiki()  # type: ignore[assignment]
    worker.wikiwho = FakeWikiWho()  # type: ignore[assignment]

    assert worker.run_once() is True
    result = runtime.repository.get_result("frwiki", 100, 200, settings.algorithm_version)

    assert result is not None
    assert result.distinct_contributors == 45
    assert result.countable_tokens == 100
    assert [item["username"] for item in result.contributors] == [
        "Alice",
        "Bob",
        "Charlie",
    ]
    assert runtime.repository.stats() == {"ready": 1}


def test_worker_requeues_current_revision_instead_of_computing_stale_one(
    tmp_path: Path,
) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'stale.db'}",
    )
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    runtime.repository.enqueue("frwiki", 100, 200, settings.algorithm_version, 50)
    worker = Worker(runtime, worker_id="test")
    worker.mediawiki = NewerRevisionMediaWiki()  # type: ignore[assignment]
    worker.wikiwho = FakeWikiWho()  # type: ignore[assignment]

    assert worker.run_once() is True
    old = runtime.repository.get_work("frwiki", 100, 200, settings.algorithm_version)
    current = runtime.repository.get_work("frwiki", 100, 201, settings.algorithm_version)
    assert old is not None and old.state == "superseded"
    assert current is not None and current.state == "pending"
    assert current.priority == 50


def test_worker_refreshes_expired_result_for_unchanged_revision(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'refresh.db'}",
        minimum_tokens=5,
    )
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    old_computed_at = utcnow() - timedelta(days=91)
    runtime.repository.save_result(
        {
            "wiki": "frwiki",
            "page_id": 100,
            "revision_id": 200,
            "algorithm_version": settings.algorithm_version,
            "title": "France",
            "metric": "old-metric",
            "contributors": [],
            "distinct_contributors": 1,
            "count_limited": False,
            "countable_tokens": 1,
            "wikiwho_revision_id": 200,
            "computed_at": old_computed_at,
        }
    )
    assert runtime.repository.enqueue_if_stale(
        "frwiki",
        100,
        200,
        settings.algorithm_version,
        priority=100,
        freshness_seconds=settings.page_freshness_seconds,
    )
    worker = Worker(runtime, worker_id="test")
    worker.mediawiki = FakeMediaWiki()  # type: ignore[assignment]
    worker.wikiwho = FakeWikiWho()  # type: ignore[assignment]

    assert worker.run_once() is True
    refreshed = runtime.repository.get_result("frwiki", 100, 200, settings.algorithm_version)

    assert refreshed is not None
    assert refreshed.computed_at > old_computed_at
    assert refreshed.metric == "wikiwho-surviving-alphanumeric-tokens"
    assert runtime.repository.get_work("frwiki", 100, 200, settings.algorithm_version) is None
