from __future__ import annotations

import argparse
import logging

from wikipeople.runtime import build_runtime

LOGGER = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune obsolete WikiPeople cache rows")
    parser.add_argument("--queue-days", type=int, default=30)
    parser.add_argument("--old-revision-days", type=int, default=30)
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    runtime = build_runtime()
    runtime.database.create_schema()
    removed = runtime.repository.cleanup(args.queue_days, args.old_revision_days)
    LOGGER.info("removed queue=%s results=%s", removed["queue"], removed["results"])


if __name__ == "__main__":
    main()
