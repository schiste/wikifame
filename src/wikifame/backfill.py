from __future__ import annotations

import argparse
import logging

from wikifame.clients import MediaWikiClient
from wikifame.runtime import build_runtime

LOGGER = logging.getLogger(__name__)
COMPLETE = "__COMPLETE__"


def main() -> None:
    parser = argparse.ArgumentParser(description="Gradually enqueue all main-namespace pages")
    parser.add_argument("--wiki", default="frwiki")
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    runtime = build_runtime()
    runtime.database.create_schema()
    state_key = f"backfill:{args.wiki}:cursor"
    if args.restart:
        runtime.repository.set_state(state_key, "")
    cursor = runtime.repository.get_state(state_key)
    if cursor == COMPLETE:
        LOGGER.info("backfill already complete for %s", args.wiki)
        return

    mediawiki = MediaWikiClient(
        runtime.settings.user_agent, runtime.settings.request_timeout_seconds
    )
    queued = 0
    try:
        for _batch in range(max(1, args.batches)):
            pages, next_cursor = mediawiki.all_pages_batch(args.wiki, cursor or None)
            for page in pages:
                if runtime.repository.enqueue_if_stale(
                    wiki=args.wiki,
                    page_id=page.page_id,
                    revision_id=page.revision_id,
                    algorithm_version=runtime.settings.algorithm_version,
                    priority=10,
                    freshness_seconds=runtime.settings.page_freshness_seconds,
                ):
                    queued += 1
            cursor = next_cursor
            runtime.repository.set_state(state_key, cursor or COMPLETE)
            if cursor is None:
                break
    finally:
        mediawiki.close()
    LOGGER.info("queued %s backfill pages; next cursor=%s", queued, cursor or COMPLETE)


if __name__ == "__main__":
    main()
