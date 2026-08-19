from collections import Counter
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from wikipeople.clients import EditorCount, EditorHistory, PageMetadata
from wikipeople.config import Settings
from wikipeople.errors import PermanentDataError
from wikipeople.models import utcnow
from wikipeople.policy import ResolvedUser
from wikipeople.runtime import build_runtime
from wikipeople.worker import Worker

# 4 to 7 exist so the fallback can be asked to drop them: the same accounts the token
# path would never name must be unnameable through the edit-count path too. 6 and 7 are
# the two bots no local flag identifies (ADR-0006) — one known to CentralAuth, one known
# to nothing but its name.
ACCOUNTS = {
    1: ResolvedUser(1, "Alice", frozenset()),
    2: ResolvedUser(2, "Bob", frozenset()),
    3: ResolvedUser(3, "Charlie", frozenset()),
    4: ResolvedUser(4, "Botrix", frozenset({"bot"})),
    5: ResolvedUser(5, "~2026-1", frozenset()),
    6: ResolvedUser(6, "Loveless", frozenset({"user", "autoconfirmed"})),
    7: ResolvedUser(7, "Gallicbot", frozenset({"user"})),
}

GLOBAL_GROUPS = {"Loveless": frozenset({"local-bot"})}


class FakeMediaWiki:
    def get_page(self, _wiki: str, page_id: int) -> PageMetadata:
        return PageMetadata(page_id, 200, "France", 0)

    def resolve_users(self, _wiki: str, user_ids: list[int]) -> dict[int, ResolvedUser]:
        return {user_id: ACCOUNTS[user_id] for user_id in user_ids}

    def global_groups(self, _wiki: str, username: str) -> frozenset[str]:
        return GLOBAL_GROUPS.get(username, frozenset())

    def get_editor_count(self, _wiki: str, _title: str) -> EditorCount:
        return EditorCount(47, False)

    def get_bot_contributor_count(self, _wiki: str, _page_id: int) -> int:
        return 2

    def get_editor_history(self, _wiki: str, _page_id: int, _max_revisions: int) -> EditorHistory:
        # A bot and a temporary account outrank two of the three nameable humans.
        return EditorHistory(
            counts=Counter({1: 30, 4: 25, 5: 20, 2: 10, 3: 5}),
            revisions=100,
            complete=True,
        )


class FakeWikiWho:
    def fetch_revision(self, _wiki: str, _revision_id: int) -> list[dict[str, str]]:
        return (
            [{"str": "alpha", "editor": "1"}] * 60
            + [{"str": "beta", "editor": "2"}] * 30
            + [{"str": "gamma", "editor": "3"}] * 10
        )


class RefusingWikiWho:
    """WikiWho answering 400: a fact about the page, not an outage."""

    def fetch_revision(self, _wiki: str, revision_id: int) -> list[dict[str, str]]:
        raise PermanentDataError(f"WikiWho ne sert pas la révision {revision_id} (HTTP 400)")


class ThinWikiWho:
    """WikiWho answers, but the page is too short for anyone to clear the thresholds."""

    def fetch_revision(self, _wiki: str, _revision_id: int) -> list[dict[str, str]]:
        return [{"str": "alpha", "editor": "1"}] * 3


class LongHistoryMediaWiki(FakeMediaWiki):
    def get_editor_history(self, _wiki: str, _page_id: int, _max_revisions: int) -> EditorHistory:
        return EditorHistory(counts=Counter({1: 400}), revisions=5000, complete=False)


class BotOnlyHistoryMediaWiki(FakeMediaWiki):
    def get_editor_history(self, _wiki: str, _page_id: int, _max_revisions: int) -> EditorHistory:
        return EditorHistory(counts=Counter({4: 12, 5: 3}), revisions=15, complete=True)


class UnflaggedBotHistoryMediaWiki(FakeMediaWiki):
    def get_editor_history(self, _wiki: str, _page_id: int, _max_revisions: int) -> EditorHistory:
        # The two bots fr.wikipedia flags as ordinary accounts outrank the one human.
        return EditorHistory(counts=Counter({6: 40, 7: 35, 1: 5}), revisions=80, complete=True)


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


def build_worker(tmp_path: Path, name: str, mediawiki: object, wikiwho: object) -> Worker:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / name}",
    )
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    runtime.repository.enqueue("frwiki", 100, 200, settings.algorithm_version, 100)
    worker = Worker(runtime, worker_id="test")
    worker.mediawiki = mediawiki  # type: ignore[assignment]
    worker.wikiwho = wikiwho  # type: ignore[assignment]
    return worker


def stored(worker: Worker) -> object:
    return worker.repository.get_result("frwiki", 100, 200, worker.settings.algorithm_version)


def test_a_page_wikiwho_refuses_falls_back_to_edit_counts(tmp_path: Path) -> None:
    """HTTP 400 means the page will never be tokenised, so the job stops waiting for it.

    Before the ladder this exhausted eight attempts and served 503 forever. The page now
    gets the weaker answer rather than none, and says so through its metric.
    """
    worker = build_worker(tmp_path, "refused.db", FakeMediaWiki(), RefusingWikiWho())

    assert worker.run_once() is True
    result = stored(worker)

    assert result is not None
    assert result.metric == "mediawiki-revision-count"
    assert [item["username"] for item in result.contributors] == ["Alice", "Bob", "Charlie"]
    # No tokens were ever counted, and the stored result must not imply otherwise.
    assert result.countable_tokens == 0


def test_the_fallback_drops_the_same_accounts_the_token_path_would(tmp_path: Path) -> None:
    """A bot and a temporary account top this history, and neither may be named.

    The rule is not reimplemented for edit counts: both paths call
    should_highlight_contributor, so the two rankings can never disagree about who is
    eligible — only about who is first.
    """
    worker = build_worker(tmp_path, "excluded.db", FakeMediaWiki(), RefusingWikiWho())

    assert worker.run_once() is True
    result = stored(worker)

    assert result is not None
    names = [item["username"] for item in result.contributors]
    assert "Botrix" not in names
    assert "~2026-1" not in names
    # Alice made 30 of the 100 revisions; the share is of edits, not of surviving text.
    assert result.contributors[0] == {
        "user_id": 1,
        "username": "Alice",
        "edit_count": 30,
        "share": 0.3,
    }


def test_a_page_too_short_to_rank_by_tokens_falls_back_too(tmp_path: Path) -> None:
    """WikiWho answered; nobody cleared 20 tokens. To a reader that is the same nothing."""
    worker = build_worker(tmp_path, "thin.db", FakeMediaWiki(), ThinWikiWho())

    assert worker.run_once() is True
    result = stored(worker)

    assert result is not None
    assert result.metric == "mediawiki-revision-count"
    assert [item["username"] for item in result.contributors] == ["Alice", "Bob", "Charlie"]
    # WikiWho did answer here, so the tokens it counted are still recorded.
    assert result.countable_tokens == 3


def test_a_history_past_the_budget_is_left_unranked_rather_than_ranked_partially(
    tmp_path: Path,
) -> None:
    """Counting the newest 5000 revisions does not tell you who edited most, ever.

    Naming the winner of a window under a sentence that says "most edited" would be a
    guess. The aggregate count is still there, so the reader loses nothing true.
    """
    worker = build_worker(tmp_path, "long.db", LongHistoryMediaWiki(), RefusingWikiWho())

    assert worker.run_once() is True
    result = stored(worker)

    assert result is not None
    assert result.contributors == []
    assert result.distinct_contributors == 45


def test_a_page_only_bots_ever_touched_yields_a_count_and_no_names(tmp_path: Path) -> None:
    """The bottom rung: a stored result with no names beats a dead job and a 503."""
    worker = build_worker(tmp_path, "botonly.db", BotOnlyHistoryMediaWiki(), RefusingWikiWho())

    assert worker.run_once() is True
    result = stored(worker)

    assert result is not None
    assert result.contributors == []
    assert result.distinct_contributors == 45
    assert worker.repository.stats() == {"ready": 1}


def test_bots_the_wiki_does_not_flag_are_still_not_named(tmp_path: Path) -> None:
    """The two holes ADR-0006 closes, in the rung where they showed up in production.

    `Loveless` is a bot only CentralAuth knows about; `Gallicbot` is flagged nowhere and
    is caught by its name alone. Both outrank the one human here, and neither is named.
    """
    worker = build_worker(
        tmp_path, "unflagged.db", UnflaggedBotHistoryMediaWiki(), RefusingWikiWho()
    )

    assert worker.run_once() is True
    result = stored(worker)

    assert result is not None
    assert [item["username"] for item in result.contributors] == ["Alice"]


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


class RedirectMediaWiki(FakeMediaWiki):
    def get_page(self, _wiki: str, page_id: int) -> PageMetadata:
        return PageMetadata(page_id, 200, '"Heroes"', 0, is_redirect=True)


def test_a_redirect_is_refused_rather_than_attributed(tmp_path: Path) -> None:
    """Keeping redirects out of the backfill source does not keep them out of the queue.

    The endpoint enqueues whatever page id a reader opens and cannot tell a redirect
    from an article, because it may not call MediaWiki. So a 46-byte redirect reached
    the worker and got a cached attribution row with six tokens. The worker has the
    page in hand by then, which makes it the one place the refusal can happen.
    """
    worker = build_worker(tmp_path, "redirect.db", RedirectMediaWiki(), FakeWikiWho())

    assert worker.run_once() is True
    assert stored(worker) is None
    work = worker.repository.get_work("frwiki", 100, 200, worker.settings.algorithm_version)
    assert work is not None and work.state == "superseded"
