from __future__ import annotations

import argparse
import logging

from wikipeople.clients import MediaWikiClient
from wikipeople.runtime import Runtime, build_runtime, configure_logging

LOGGER = logging.getLogger(__name__)
COMPLETE = "__COMPLETE__"


def backfill_wiki(runtime: Runtime, mediawiki: MediaWikiClient, wiki: str, batches: int) -> int:
    state_key = f"backfill:{wiki}:cursor"
    cursor = runtime.repository.get_state(state_key)
    if cursor == COMPLETE:
        LOGGER.info("%s: backfill already complete", wiki)
        return 0

    queued = 0
    for _batch in range(max(1, batches)):
        pages, next_cursor = mediawiki.all_pages_batch(wiki, cursor or None)
        for page in pages:
            if runtime.repository.enqueue_if_stale(
                wiki=wiki,
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
    LOGGER.info("%s: queued %s backfill pages; next cursor=%s", wiki, queued, cursor or COMPLETE)
    return queued


def main() -> None:
    parser = argparse.ArgumentParser(description="Gradually enqueue all main-namespace pages")
    parser.add_argument("--wiki", default=None, help="Override BACKFILL_WIKIS for this run")
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    configure_logging()

    runtime = build_runtime()
    runtime.database.create_schema()
    # Backfill is never inferred from discovered wikis: crawling every article of a
    # large Wikipedia is a capacity commitment, so it stays an explicit opt-in.
    wikis = [args.wiki] if args.wiki else list(runtime.settings.backfill_wikis)
    wikis = [wiki for wiki in wikis if runtime.resolver.is_capable(wiki)]
    if not wikis:
        LOGGER.info("no wiki opted into backfill; set BACKFILL_WIKIS to enable it")
        return

    if args.restart:
        for wiki in wikis:
            runtime.repository.set_state(f"backfill:{wiki}:cursor", "")

    mediawiki = MediaWikiClient(
        runtime.settings.user_agent, runtime.settings.request_timeout_seconds, runtime.resolver
    )
    try:
        total = sum(backfill_wiki(runtime, mediawiki, wiki, args.batches) for wiki in wikis)
    finally:
        mediawiki.close()
    LOGGER.info("queued %s backfill pages across %s wikis", total, len(wikis))


if __name__ == "__main__":
    main()
