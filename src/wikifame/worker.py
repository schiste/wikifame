from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import time
from typing import Any

from wikifame.attribution import candidate_user_ids, count_tokens, select_contributors
from wikifame.clients import MediaWikiClient, WikiWhoClient
from wikifame.errors import WikiFameError
from wikifame.models import utcnow
from wikifame.repository import Repository, WorkLease
from wikifame.runtime import Runtime, build_runtime

LOGGER = logging.getLogger(__name__)
METRIC = "wikiwho-surviving-alphanumeric-tokens"


class Worker:
    def __init__(self, runtime: Runtime, worker_id: str | None = None) -> None:
        self.settings = runtime.settings
        self.repository: Repository = runtime.repository
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.mediawiki = MediaWikiClient(
            self.settings.user_agent, self.settings.request_timeout_seconds
        )
        self.wikiwho = WikiWhoClient(
            self.settings.wikiwho_base_url,
            self.settings.user_agent,
            self.settings.wikiwho_timeout_seconds,
            self.settings.wikiwho_max_response_bytes,
        )
        self.stopping = False

    def stop(self, *_args: Any) -> None:
        self.stopping = True

    def run_once(self) -> bool:
        lease = self.repository.claim(
            worker_id=self.worker_id,
            lease_seconds=self.settings.worker_lease_seconds,
        )
        if lease is None:
            return False
        LOGGER.info(
            "processing wiki=%s page_id=%s revision_id=%s attempt=%s",
            lease.wiki,
            lease.page_id,
            lease.revision_id,
            lease.attempts,
        )
        try:
            self.process(lease)
        except WikiFameError as error:
            LOGGER.warning("job failed code=%s error=%s", error.code, error)
            self.repository.fail(
                lease,
                code=error.code,
                message=str(error),
                max_attempts=self.settings.worker_max_attempts,
                permanent=error.permanent,
            )
        except Exception as error:  # A poisoned job must not kill a continuous worker.
            LOGGER.exception("unexpected worker failure")
            self.repository.fail(
                lease,
                code="unexpected_error",
                message=str(error),
                max_attempts=self.settings.worker_max_attempts,
            )
        return True

    def process(self, lease: WorkLease) -> None:
        page = self.mediawiki.get_page(lease.wiki, lease.page_id)
        if page.namespace != 0:
            self.repository.supersede(lease.id, "La page n’est plus dans l’espace principal")
            return
        if page.revision_id != lease.revision_id:
            self.repository.supersede(
                lease.id,
                f"Révision demandée {lease.revision_id}, révision courante {page.revision_id}",
            )
            self.repository.enqueue(
                lease.wiki,
                page.page_id,
                page.revision_id,
                lease.algorithm_version,
                lease.priority,
            )
            return

        tokens = self.wikiwho.fetch_revision(lease.wiki, lease.revision_id)
        counts, total_tokens = count_tokens(tokens)
        user_ids = candidate_user_ids(counts, self.settings.candidate_pool_size)
        users = self.mediawiki.resolve_users(lease.wiki, user_ids)
        contributors = select_contributors(
            counts=counts,
            total_tokens=total_tokens,
            users=users,
            minimum_tokens=self.settings.minimum_tokens,
            minimum_share=self.settings.minimum_share,
        )

        editor_count = self.mediawiki.get_editor_count(lease.wiki, page.title)
        bot_count = self.mediawiki.get_bot_contributor_count(lease.wiki, lease.page_id)
        distinct_contributors = max(
            len(contributors),
            editor_count.count - bot_count,
        )

        self.repository.save_result(
            {
                "wiki": lease.wiki,
                "page_id": lease.page_id,
                "revision_id": lease.revision_id,
                "algorithm_version": lease.algorithm_version,
                "title": page.title,
                "metric": METRIC,
                "contributors": contributors,
                "distinct_contributors": distinct_contributors,
                "count_limited": editor_count.limited,
                "countable_tokens": total_tokens,
                "wikiwho_revision_id": lease.revision_id,
                "computed_at": utcnow(),
            }
        )
        self.repository.complete(lease.id)
        LOGGER.info(
            "ready wiki=%s page_id=%s revision_id=%s contributors=%s tokens=%s",
            lease.wiki,
            lease.page_id,
            lease.revision_id,
            len(contributors),
            total_tokens,
        )

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self.stopping:
            if not self.run_once():
                time.sleep(self.settings.worker_poll_seconds)

    def close(self) -> None:
        self.mediawiki.close()
        self.wikiwho.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process WikiFame attribution jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = build_runtime()
    runtime.database.create_schema()
    worker = Worker(runtime)
    try:
        if args.once:
            worker.run_once()
        else:
            worker.run_forever()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
