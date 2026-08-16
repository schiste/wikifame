from datetime import date

from wikifame.prewarm import collect_recent_top_titles


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
