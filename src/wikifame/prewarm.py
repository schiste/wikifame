from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta

from wikifame.clients import AnalyticsClient, MediaWikiClient
from wikifame.errors import RetryableUpstreamError
from wikifame.runtime import build_runtime

LOGGER = logging.getLogger(__name__)


def previous_days(days: int, today: date | None = None) -> list[date]:
    reference_day = today or datetime.now(UTC).date()
    return [reference_day - timedelta(days=offset) for offset in range(1, days + 1)]


def collect_recent_top_titles(
    analytics: AnalyticsClient,
    wiki: str,
    days: int,
    today: date | None = None,
) -> tuple[set[str], list[date]]:
    target_days = max(1, days)
    lookback_days = max(14, target_days * 3)
    titles: set[str] = set()
    loaded_days: list[date] = []

    for day in previous_days(lookback_days, today):
        articles = analytics.top_pages(wiki, day)
        if articles is None:
            LOGGER.info("pageviews not published yet for %s", day)
            continue
        titles.update(articles)
        loaded_days.append(day)
        if len(loaded_days) == target_days:
            break

    if not loaded_days:
        raise RetryableUpstreamError("Aucun classement Pageviews récent disponible")
    return titles, loaded_days


def main() -> None:
    parser = argparse.ArgumentParser(description="Prewarm popular Wikipedia articles")
    parser.add_argument("--wiki", default="frwiki")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    runtime = build_runtime()
    runtime.database.create_schema()
    analytics = AnalyticsClient(
        runtime.settings.user_agent, runtime.settings.request_timeout_seconds
    )
    mediawiki = MediaWikiClient(
        runtime.settings.user_agent, runtime.settings.request_timeout_seconds
    )
    try:
        titles, loaded_days = collect_recent_top_titles(analytics, args.wiki, max(1, args.days))
        pages = mediawiki.resolve_titles(args.wiki, sorted(titles))
        queued = 0
        for page in pages:
            if page.namespace != 0:
                continue
            if runtime.repository.enqueue_if_stale(
                wiki=args.wiki,
                page_id=page.page_id,
                revision_id=page.revision_id,
                algorithm_version=runtime.settings.algorithm_version,
                priority=50,
                freshness_seconds=runtime.settings.page_freshness_seconds,
            ):
                queued += 1
        LOGGER.info(
            "queued %s popular pages from %s unique titles across %s available days",
            queued,
            len(titles),
            len(loaded_days),
        )
    finally:
        analytics.close()
        mediawiki.close()


if __name__ == "__main__":
    main()
