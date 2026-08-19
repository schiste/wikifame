from dataclasses import replace
from pathlib import Path

from wikipeople.backfill import COMPLETE, backfill_wiki, length_cursor_key, title_cursor_key
from wikipeople.clients import PageMetadata
from wikipeople.config import Settings
from wikipeople.replica import LengthCursor, ReplicaPage
from wikipeople.runtime import Runtime, build_runtime


class FakeReplica:
    def __init__(self, batches: list[tuple[list[ReplicaPage], LengthCursor | None]]) -> None:
        self.batches = batches
        self.calls: list[LengthCursor | None] = []

    def available(self) -> bool:
        return True

    def pages_by_descending_length(
        self, _wiki: str, cursor: LengthCursor | None, _limit: int
    ) -> tuple[list[ReplicaPage], LengthCursor | None]:
        self.calls.append(cursor)
        return self.batches[len(self.calls) - 1]


class AbsentReplica:
    def available(self) -> bool:
        return False


class FakeMediaWiki:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def all_pages_batch(
        self, _wiki: str, cursor: str | None
    ) -> tuple[list[PageMetadata], str | None]:
        self.calls.append(cursor)
        return [PageMetadata(7, 700, "Aardvark", 0)], None


def build(tmp_path: Path, name: str) -> Runtime:
    settings = replace(Settings.from_env(), database_url=f"sqlite:///{tmp_path / name}")
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    return runtime


def test_backfill_walks_down_from_the_heaviest_page(tmp_path: Path) -> None:
    runtime = build(tmp_path, "length.db")
    replica = FakeReplica(
        [
            ([ReplicaPage(1, 11, 90000), ReplicaPage(2, 12, 80000)], LengthCursor(80000, 2)),
            ([ReplicaPage(3, 13, 70000)], None),
        ]
    )

    queued = backfill_wiki(runtime, FakeMediaWiki(), replica, "frwiki", batches=2)  # type: ignore[arg-type]

    assert queued == 3
    assert replica.calls == [None, LengthCursor(80000, 2)]
    assert runtime.repository.get_state(length_cursor_key("frwiki")) == COMPLETE
    for page_id, revision_id in ((1, 11), (2, 12), (3, 13)):
        work = runtime.repository.get_work(
            "frwiki", page_id, revision_id, runtime.settings.algorithm_version
        )
        assert work is not None and work.state == "pending"


def test_the_length_walk_resumes_from_its_own_cursor(tmp_path: Path) -> None:
    runtime = build(tmp_path, "resume.db")
    runtime.repository.set_state(length_cursor_key("frwiki"), "80000:2")
    replica = FakeReplica([([ReplicaPage(3, 13, 70000)], None)])

    backfill_wiki(runtime, FakeMediaWiki(), replica, "frwiki", batches=1)  # type: ignore[arg-type]

    assert replica.calls == [LengthCursor(80000, 2)]


def test_without_a_replica_the_backfill_still_runs_on_the_action_api(tmp_path: Path) -> None:
    """The fallback keeps its own cursor rather than sharing the length one.

    A title and a `length:page_id` pair are not interchangeable, and a wiki can move
    between the two paths when a replica goes down. Sharing one key would have made
    each switch read the other's cursor as its own.
    """
    runtime = build(tmp_path, "fallback.db")
    runtime.repository.set_state(length_cursor_key("frwiki"), "80000:2")
    mediawiki = FakeMediaWiki()

    queued = backfill_wiki(runtime, mediawiki, AbsentReplica(), "frwiki", batches=1)  # type: ignore[arg-type]

    assert queued == 1
    assert mediawiki.calls == [None]
    assert runtime.repository.get_state(title_cursor_key("frwiki")) == COMPLETE
    assert runtime.repository.get_state(length_cursor_key("frwiki")) == "80000:2"
