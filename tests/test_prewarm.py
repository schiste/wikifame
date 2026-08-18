from dataclasses import replace
from datetime import date
from pathlib import Path

from wikipeople.config import Settings
from wikipeople.prewarm import collect_recent_top_titles, resolve_target_wikis
from wikipeople.runtime import Runtime, build_runtime


class FakeAnalytics:
    def __init__(self, results: dict[date, list[str] | None]) -> None:
        self.results = results

    def top_pages(self, _wiki: str, day: date) -> list[str] | None:
        return self.results.get(day)


def test_collects_last_available_days_instead_of_failing_on_publication_lag() -> None:
    analytics = FakeAnalytics(
        {
            date(2026, 8, 15): None,
            date(2026, 8, 14): None,
            date(2026, 8, 13): ["France", "Paris"],
            date(2026, 8, 12): ["France", "Europe"],
        }
    )

    titles, loaded_days = collect_recent_top_titles(
        analytics,  # type: ignore[arg-type]
        "frwiki",
        days=2,
        today=date(2026, 8, 16),
    )

    assert titles == {"France", "Paris", "Europe"}
    assert loaded_days == [date(2026, 8, 13), date(2026, 8, 12)]


def _runtime(tmp_path: Path) -> Runtime:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'prewarm.db'}",
    )
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    return runtime


def test_a_wiki_discovered_by_a_worker_is_prewarmed_from_then_on(tmp_path: Path) -> None:
    """The first real result for a wiki enrols it into daily popular-page warming."""
    runtime = _runtime(tmp_path)

    assert resolve_target_wikis(runtime, None) == []

    assert runtime.repository.register_active_wiki("dewiki") is True
    assert resolve_target_wikis(runtime, None) == ["dewiki"]

    # Re-registering is idempotent and must not duplicate the wiki.
    assert runtime.repository.register_active_wiki("dewiki") is False
    assert resolve_target_wikis(runtime, None) == ["dewiki"]


def test_pinned_and_discovered_wikis_are_merged(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime = replace(runtime, settings=replace(runtime.settings, prewarm_wikis=("frwiki",)))
    runtime.repository.register_active_wiki("dewiki")

    assert resolve_target_wikis(runtime, None) == ["dewiki", "frwiki"]


def test_an_uncovered_wiki_is_never_prewarmed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime = replace(runtime, settings=replace(runtime.settings, prewarm_wikis=("commonswiki",)))

    assert resolve_target_wikis(runtime, None) == []


def test_explicit_wiki_argument_overrides_discovery(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.repository.register_active_wiki("dewiki")

    assert resolve_target_wikis(runtime, "frwiki") == ["frwiki"]
