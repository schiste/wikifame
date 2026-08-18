from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import time
from typing import Any

from wikipeople.attribution import (
    candidate_user_ids,
    count_tokens,
    select_contributors,
    select_top_editors,
)
from wikipeople.clients import MediaWikiClient, PageMetadata, WikiWhoClient
from wikipeople.errors import PermanentDataError, WikiPeopleError
from wikipeople.models import utcnow
from wikipeople.repository import Repository, WorkLease
from wikipeople.runtime import Runtime, build_runtime

LOGGER = logging.getLogger(__name__)
METRIC_TOKENS = "wikiwho-surviving-alphanumeric-tokens"
METRIC_EDITS = "mediawiki-revision-count"


class Worker:
    def __init__(self, runtime: Runtime, worker_id: str | None = None) -> None:
        self.settings = runtime.settings
        self.repository: Repository = runtime.repository
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.mediawiki = MediaWikiClient(
            self.settings.user_agent,
            self.settings.request_timeout_seconds,
            runtime.resolver,
        )
        self.wikiwho = WikiWhoClient(
            self.settings.wikiwho_base_url,
            self.settings.user_agent,
            self.settings.wikiwho_timeout_seconds,
            self.settings.wikiwho_max_response_bytes,
            runtime.resolver,
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
        except WikiPeopleError as error:
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

        contributors, total_tokens, metric = self.attribute(lease, page)

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
                "metric": metric,
                "contributors": contributors,
                "distinct_contributors": distinct_contributors,
                "count_limited": editor_count.limited,
                "countable_tokens": total_tokens,
                "wikiwho_revision_id": lease.revision_id,
                "computed_at": utcnow(),
            }
        )
        self.repository.complete(lease.id)
        if self.repository.register_active_wiki(lease.wiki):
            LOGGER.info(
                "discovered wiki=%s; its popular pages will be prewarmed from now on",
                lease.wiki,
            )
        LOGGER.info(
            "ready wiki=%s page_id=%s revision_id=%s contributors=%s tokens=%s",
            lease.wiki,
            lease.page_id,
            lease.revision_id,
            len(contributors),
            total_tokens,
        )

    def attribute(self, lease: WorkLease, page: PageMetadata) -> tuple[list[Any], int, str]:
        """Name up to three accounts, preferring the strongest evidence available.

        Three rungs, in descending order of what they actually claim (ADR-0005):

        1. surviving tokens — who wrote the text on screen right now;
        2. edit counts — who touched the page most, which is a weaker and different
           claim, and is worded differently in the interface for that reason;
        3. nothing — the caller still has the aggregate count to fall back on.

        Rung 2 is reached both when WikiWho refuses the page and when it answers but no
        account clears the significance thresholds. The two failures look different from
        here but identical to a reader: either way there is no name to show.

        Returns the contributors, the countable token total (zero when WikiWho never
        answered) and the metric that produced the names.
        """
        contributors: list[Any] = []
        total_tokens = 0

        def global_groups(username: str) -> frozenset[str]:
            return self.mediawiki.global_groups(lease.wiki, username)

        try:
            tokens = self.wikiwho.fetch_revision(lease.wiki, lease.revision_id)
            counts, total_tokens = count_tokens(tokens)
            users = self.mediawiki.resolve_users(
                lease.wiki, candidate_user_ids(counts, self.settings.candidate_pool_size)
            )
            contributors = select_contributors(
                counts=counts,
                total_tokens=total_tokens,
                users=users,
                minimum_tokens=self.settings.minimum_tokens,
                minimum_share=self.settings.minimum_share,
                global_groups=global_groups,
            )
        except PermanentDataError as error:
            # Only WikiWho's refusal is caught here. A missing page raises the same class
            # from get_page, before this runs, and must still kill the job: there is
            # nothing to attribute.
            LOGGER.info(
                "wikiwho declined wiki=%s revision_id=%s (%s); trying edit counts",
                lease.wiki,
                lease.revision_id,
                error,
            )

        if contributors:
            return contributors, total_tokens, METRIC_TOKENS

        history = self.mediawiki.get_editor_history(
            lease.wiki, lease.page_id, self.settings.top_editor_max_revisions
        )
        if not history.complete:
            LOGGER.info(
                "history of wiki=%s page_id=%s exceeds %s revisions; leaving it unranked",
                lease.wiki,
                lease.page_id,
                self.settings.top_editor_max_revisions,
            )
            return [], total_tokens, METRIC_EDITS

        candidates = history.counts.most_common(self.settings.candidate_pool_size)
        users = self.mediawiki.resolve_users(lease.wiki, [user_id for user_id, _ in candidates])
        return (
            select_top_editors(
                counts=history.counts,
                total_revisions=history.revisions,
                users=users,
                global_groups=global_groups,
            ),
            total_tokens,
            METRIC_EDITS,
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
    parser = argparse.ArgumentParser(description="Process WikiPeople attribution jobs")
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
