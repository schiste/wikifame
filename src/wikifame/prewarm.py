from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta

from wikifame.clients import AnalyticsClient, MediaWikiClient
from wikifame.runtime import build_runtime

LOGGER = logging.getLogger(__name__)


def previous_days(days: int) -> list[date]:
    today = datetime.now(UTC).date()
    return [today - timedelta(days=offset) for offset in range(1, days + 1)]


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
        titles: set[str] = set()
        for day in previous_days(max(1, args.days)):
            titles.update(analytics.top_pages(args.wiki, day))
        pages = mediawiki.resolve_titles(args.wiki, sorted(titles))
        queued = 0
        for page in pages:
            if page.namespace != 0:
                continue
            runtime.repository.enqueue(
                args.wiki,
                page.page_id,
                page.revision_id,
                runtime.settings.algorithm_version,
                priority=50,
            )
            queued += 1
        LOGGER.info("queued %s popular pages from %s unique titles", queued, len(titles))
    finally:
        analytics.close()
        mediawiki.close()


if __name__ == "__main__":
    main()
