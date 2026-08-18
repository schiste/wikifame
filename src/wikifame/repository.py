from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from wikifame.db import Database
from wikifame.models import (
    ActiveWiki,
    AppState,
    AttributionResult,
    ContributorStanding,
    PageOptOut,
    WorkItem,
    utcnow,
)
from wikifame.policy import AccountStanding


@dataclass(frozen=True)
class OptOutEntry:
    """One article the on-wiki list resolves to, with the entry that named it."""

    page_id: int
    title: str
    source: str


@dataclass(frozen=True)
class StandingRecord:
    """One account's standing, plus when its global lock was last confirmed.

    The timestamp is bookkeeping for the refresh rotation rather than part of the fact,
    so it travels beside `AccountStanding` instead of inside it — nothing on the serve
    path has any use for it.
    """

    standing: AccountStanding
    lock_checked_at: datetime | None


@dataclass(frozen=True)
class WorkLease:
    id: int
    wiki: str
    page_id: int
    revision_id: int
    algorithm_version: str
    priority: int
    attempts: int


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_result(
        self, wiki: str, page_id: int, revision_id: int, algorithm_version: str
    ) -> AttributionResult | None:
        with self.database.session() as session:
            return session.scalar(
                select(AttributionResult).where(
                    AttributionResult.wiki == wiki,
                    AttributionResult.page_id == page_id,
                    AttributionResult.revision_id == revision_id,
                    AttributionResult.algorithm_version == algorithm_version,
                )
            )

    def get_latest_result(
        self, wiki: str, page_id: int, algorithm_version: str
    ) -> AttributionResult | None:
        with self.database.session() as session:
            return session.scalar(
                select(AttributionResult)
                .where(
                    AttributionResult.wiki == wiki,
                    AttributionResult.page_id == page_id,
                    AttributionResult.algorithm_version == algorithm_version,
                )
                .order_by(AttributionResult.computed_at.desc(), AttributionResult.id.desc())
                .limit(1)
            )

    def get_work(
        self, wiki: str, page_id: int, revision_id: int, algorithm_version: str
    ) -> WorkItem | None:
        with self.database.session() as session:
            return session.scalar(
                select(WorkItem).where(
                    WorkItem.wiki == wiki,
                    WorkItem.page_id == page_id,
                    WorkItem.revision_id == revision_id,
                    WorkItem.algorithm_version == algorithm_version,
                )
            )

    def enqueue(
        self,
        wiki: str,
        page_id: int,
        revision_id: int,
        algorithm_version: str,
        priority: int,
        allow_cached_result: bool = False,
    ) -> None:
        now = utcnow()
        if (
            not allow_cached_result
            and self.get_result(wiki, page_id, revision_id, algorithm_version) is not None
        ):
            return
        try:
            with self.database.session() as session, session.begin():
                existing = session.scalar(
                    select(WorkItem).where(
                        WorkItem.wiki == wiki,
                        WorkItem.page_id == page_id,
                        WorkItem.revision_id == revision_id,
                        WorkItem.algorithm_version == algorithm_version,
                    )
                )
                newer_work_exists = session.scalar(
                    select(WorkItem.id)
                    .where(
                        WorkItem.wiki == wiki,
                        WorkItem.page_id == page_id,
                        WorkItem.algorithm_version == algorithm_version,
                        WorkItem.revision_id > revision_id,
                        WorkItem.state.in_(("pending", "leased")),
                    )
                    .limit(1)
                )
                if existing is None and newer_work_exists is not None:
                    return
                if existing is None:
                    session.add(
                        WorkItem(
                            wiki=wiki,
                            page_id=page_id,
                            revision_id=revision_id,
                            algorithm_version=algorithm_version,
                            priority=priority,
                            available_at=now,
                        )
                    )
                elif existing.state not in {"dead", "superseded"}:
                    existing.priority = max(existing.priority, priority)
                    existing.updated_at = now

                session.execute(
                    update(WorkItem)
                    .where(
                        WorkItem.wiki == wiki,
                        WorkItem.page_id == page_id,
                        WorkItem.algorithm_version == algorithm_version,
                        WorkItem.revision_id < revision_id,
                        WorkItem.state == "pending",
                    )
                    .values(state="superseded", updated_at=now)
                )
        except IntegrityError:
            # Two simultaneous cache misses may race to insert the same unique job.
            with self.database.session() as session, session.begin():
                session.execute(
                    update(WorkItem)
                    .where(
                        WorkItem.wiki == wiki,
                        WorkItem.page_id == page_id,
                        WorkItem.revision_id == revision_id,
                        WorkItem.algorithm_version == algorithm_version,
                    )
                    .values(priority=priority, updated_at=now)
                )

    def enqueue_if_stale(
        self,
        wiki: str,
        page_id: int,
        revision_id: int,
        algorithm_version: str,
        priority: int,
        freshness_seconds: int,
    ) -> bool:
        latest = self.get_latest_result(wiki, page_id, algorithm_version)
        cutoff = utcnow() - timedelta(seconds=freshness_seconds)
        if latest is not None and latest.computed_at >= cutoff:
            return False

        self.enqueue(
            wiki=wiki,
            page_id=page_id,
            revision_id=revision_id,
            algorithm_version=algorithm_version,
            priority=priority,
            allow_cached_result=True,
        )
        work = self.get_work(wiki, page_id, revision_id, algorithm_version)
        return work is not None and work.state in {"pending", "leased"}

    def claim(self, worker_id: str, lease_seconds: int) -> WorkLease | None:
        now = utcnow()
        with self.database.session() as session, session.begin():
            statement = (
                select(WorkItem)
                .where(
                    or_(
                        and_(WorkItem.state == "pending", WorkItem.available_at <= now),
                        and_(WorkItem.state == "leased", WorkItem.lease_until < now),
                    )
                )
                .order_by(WorkItem.priority.desc(), WorkItem.created_at.asc())
                .limit(1)
            )
            if self.database.engine.dialect.name != "sqlite":
                statement = statement.with_for_update(skip_locked=True)
            item = session.scalar(statement)
            if item is None:
                return None
            item.state = "leased"
            item.worker_id = worker_id
            item.lease_until = now + timedelta(seconds=lease_seconds)
            item.attempts += 1
            item.updated_at = now
            return WorkLease(
                id=item.id,
                wiki=item.wiki,
                page_id=item.page_id,
                revision_id=item.revision_id,
                algorithm_version=item.algorithm_version,
                priority=item.priority,
                attempts=item.attempts,
            )

    def save_result(self, values: dict[str, Any]) -> None:
        with self.database.session() as session, session.begin():
            existing = session.scalar(
                select(AttributionResult).where(
                    AttributionResult.wiki == values["wiki"],
                    AttributionResult.page_id == values["page_id"],
                    AttributionResult.revision_id == values["revision_id"],
                    AttributionResult.algorithm_version == values["algorithm_version"],
                )
            )
            if existing is None:
                session.add(AttributionResult(**values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)

    def complete(self, work_id: int) -> None:
        with self.database.session() as session, session.begin():
            session.execute(delete(WorkItem).where(WorkItem.id == work_id))

    def supersede(self, work_id: int, reason: str) -> None:
        with self.database.session() as session, session.begin():
            session.execute(
                update(WorkItem)
                .where(WorkItem.id == work_id)
                .values(
                    state="superseded",
                    lease_until=None,
                    worker_id=None,
                    error_code="revision_superseded",
                    last_error=reason[:2000],
                    updated_at=utcnow(),
                )
            )

    def fail(
        self,
        lease: WorkLease,
        code: str,
        message: str,
        max_attempts: int,
        permanent: bool = False,
    ) -> None:
        is_dead = permanent or lease.attempts >= max_attempts
        delay_seconds = min(6 * 60 * 60, 30 * (2 ** max(0, lease.attempts - 1)))
        with self.database.session() as session, session.begin():
            session.execute(
                update(WorkItem)
                .where(WorkItem.id == lease.id)
                .values(
                    state="dead" if is_dead else "pending",
                    available_at=utcnow() + timedelta(seconds=delay_seconds),
                    lease_until=None,
                    worker_id=None,
                    error_code=code[:64],
                    last_error=message[:2000],
                    is_permanent=permanent,
                    updated_at=utcnow(),
                )
            )

    def revive(self, work_id: int, priority: int) -> None:
        with self.database.session() as session, session.begin():
            session.execute(
                update(WorkItem)
                .where(WorkItem.id == work_id, WorkItem.state == "dead")
                .values(
                    state="pending",
                    attempts=0,
                    priority=priority,
                    available_at=utcnow(),
                    error_code=None,
                    last_error=None,
                    updated_at=utcnow(),
                )
            )

    def register_active_wiki(self, wiki: str) -> bool:
        """Record that a wiki has produced a result. Returns True on first sighting.

        A unique primary key makes concurrent workers safe: the loser of the race
        simply refreshes the timestamp.
        """
        now = utcnow()
        try:
            with self.database.session() as session, session.begin():
                existing = session.get(ActiveWiki, wiki)
                if existing is not None:
                    existing.last_result_at = now
                    return False
                session.add(ActiveWiki(wiki=wiki, first_seen_at=now, last_result_at=now))
                return True
        except IntegrityError:
            with self.database.session() as session, session.begin():
                session.execute(
                    update(ActiveWiki).where(ActiveWiki.wiki == wiki).values(last_result_at=now)
                )
            return False

    def active_wikis(self) -> list[str]:
        with self.database.session() as session:
            return sorted(session.scalars(select(ActiveWiki.wiki)).all())

    def is_opted_out(self, wiki: str, page_id: int) -> bool:
        """Whether this page's contributors may be counted but not named.

        On the serve path for every ready response, so it is a primary-key lookup and
        nothing more. A page absent from the table is the overwhelmingly common case and
        costs one index probe.
        """
        with self.database.session() as session:
            return session.get(PageOptOut, (wiki, page_id)) is not None

    def replace_optout(self, wiki: str, entries: Sequence[OptOutEntry]) -> tuple[int, int]:
        """Make the stored list for one wiki match `entries` exactly. Returns (added, removed).

        Only ever called after the on-wiki page has actually been read. An empty list is
        a legitimate instruction — it means nobody is opted out any more — so a failed
        read must raise long before it reaches here rather than arrive as an empty list
        and quietly un-opt-out a whole wiki.
        """
        now = utcnow()
        wanted = {entry.page_id: entry for entry in entries}
        with self.database.session() as session, session.begin():
            existing = {
                row.page_id: row
                for row in session.scalars(select(PageOptOut).where(PageOptOut.wiki == wiki))
            }
            removed = sorted(set(existing) - set(wanted))
            if removed:
                session.execute(
                    delete(PageOptOut).where(
                        PageOptOut.wiki == wiki, PageOptOut.page_id.in_(removed)
                    )
                )
            added = 0
            for page_id, entry in wanted.items():
                row = existing.get(page_id)
                if row is None:
                    session.add(
                        PageOptOut(
                            wiki=wiki,
                            page_id=page_id,
                            title=entry.title,
                            source=entry.source,
                            synced_at=now,
                        )
                    )
                    added += 1
                else:
                    row.title = entry.title
                    row.source = entry.source
                    row.synced_at = now
        return added, len(removed)

    def standing_for(self, wiki: str, user_ids: Sequence[int]) -> dict[int, AccountStanding]:
        """Return what is known about these accounts. On the serve path, so it stays one query.

        Called with at most three IDs — the accounts a ready response would name — and
        skipped entirely when there are none. An ID missing from the answer means nobody
        has looked at that account yet, which `is_nameable_account` treats as nameable.
        """
        if not user_ids:
            return {}
        with self.database.session() as session:
            rows = session.scalars(
                select(ContributorStanding).where(
                    ContributorStanding.wiki == wiki,
                    ContributorStanding.user_id.in_(tuple(user_ids)),
                )
            ).all()
        return {
            row.user_id: AccountStanding(
                user_id=row.user_id,
                username=row.username,
                blocked_at=row.blocked_at,
                block_expires_at=row.block_expires_at,
                block_partial=row.block_partial,
                block_reason=row.block_reason,
                globally_locked=row.globally_locked,
                lock_reason=row.lock_reason,
            )
            for row in rows
        }

    def standing_records(self, wiki: str) -> dict[int, StandingRecord]:
        """Everything stored about one wiki's tracked accounts, for the sync job.

        Separate from `standing_for` because the two callers want different things. The
        serve path wants three accounts and no bookkeeping; the job wants the whole wiki
        including `lock_checked_at`, which is how it knows whose turn it is.
        """
        with self.database.session() as session:
            rows = session.scalars(
                select(ContributorStanding).where(ContributorStanding.wiki == wiki)
            ).all()
        return {
            row.user_id: StandingRecord(
                standing=AccountStanding(
                    user_id=row.user_id,
                    username=row.username,
                    blocked_at=row.blocked_at,
                    block_expires_at=row.block_expires_at,
                    block_partial=row.block_partial,
                    block_reason=row.block_reason,
                    globally_locked=row.globally_locked,
                    lock_reason=row.lock_reason,
                ),
                lock_checked_at=row.lock_checked_at,
            )
            for row in rows
        }

    def lock_check_candidates(self, wiki: str, cutoff: datetime, limit: int) -> list[int]:
        """User IDs whose global lock status is stale or was never read, oldest first.

        Never-checked accounts sort first. That matters more than it looks: an account
        WikiFame has only just started naming is the one whose status nobody here has
        ever confirmed, so it goes ahead of one that was confirmed yesterday.
        """
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(ContributorStanding.user_id)
                    .where(
                        ContributorStanding.wiki == wiki,
                        or_(
                            ContributorStanding.lock_checked_at.is_(None),
                            ContributorStanding.lock_checked_at < cutoff,
                        ),
                    )
                    .order_by(
                        ContributorStanding.lock_checked_at.is_(None).desc(),
                        ContributorStanding.lock_checked_at.asc(),
                    )
                    .limit(limit)
                ).all()
            )

    def named_contributor_ids(self, wiki: str) -> list[int]:
        """Every account this wiki's stored results would name, deduplicated.

        Read in Python rather than with a JSON query because the two supported engines
        spell that differently, and this runs in a job, once an hour, over one column.
        The set is small — a few thousand accounts for tens of thousands of articles,
        because the top contributors of popular pages are the same people repeatedly.
        """
        seen: set[int] = set()
        with self.database.session() as session:
            rows = session.scalars(
                select(AttributionResult.contributors).where(AttributionResult.wiki == wiki)
            )
            for contributors in rows:
                for contributor in contributors or ():
                    user_id = contributor.get("user_id")
                    if isinstance(user_id, int) and user_id > 0:
                        seen.add(user_id)
        return sorted(seen)

    def replace_standing(self, wiki: str, records: Sequence[StandingRecord]) -> tuple[int, int]:
        """Make one wiki's tracked accounts match `records` exactly. Returns (added, removed).

        A full replacement, because MediaWiki reports only active blocks: an account
        whose block lapsed comes back with nothing said about it, and merging would keep
        the stale row for ever. Removal also covers an account no stored result names any
        more, so the table tracks what is displayed rather than growing without bound.
        """
        now = utcnow()
        wanted = {record.standing.user_id: record for record in records}
        with self.database.session() as session, session.begin():
            existing = {
                row.user_id: row
                for row in session.scalars(
                    select(ContributorStanding).where(ContributorStanding.wiki == wiki)
                )
            }
            removed = sorted(set(existing) - set(wanted))
            if removed:
                session.execute(
                    delete(ContributorStanding).where(
                        ContributorStanding.wiki == wiki,
                        ContributorStanding.user_id.in_(removed),
                    )
                )
            added = 0
            for user_id, record in wanted.items():
                row = existing.get(user_id)
                if row is None:
                    row = ContributorStanding(wiki=wiki, user_id=user_id)
                    session.add(row)
                    added += 1
                standing = record.standing
                row.username = standing.username
                row.blocked_at = standing.blocked_at
                row.block_expires_at = standing.block_expires_at
                row.block_partial = standing.block_partial
                row.block_reason = standing.block_reason
                row.globally_locked = standing.globally_locked
                row.lock_reason = standing.lock_reason
                row.lock_checked_at = record.lock_checked_at
                row.synced_at = now
        return added, len(removed)

    def standing_counts(self) -> dict[str, dict[str, int]]:
        """Per wiki: how many accounts are tracked, and how many carry a block or a lock.

        Deliberately not "how many are hidden". That depends on the threshold, which is
        applied per response; reporting it here would put a second copy of the rule
        somewhere it could drift from the first.
        """
        with self.database.session() as session:
            rows = session.execute(
                select(
                    ContributorStanding.wiki,
                    func.count(),
                    func.sum(case((ContributorStanding.blocked_at.is_not(None), 1), else_=0)),
                    func.sum(case((ContributorStanding.globally_locked, 1), else_=0)),
                ).group_by(ContributorStanding.wiki)
            ).all()
        return {
            str(wiki): {
                "tracked": int(tracked),
                "blocked": int(blocked or 0),
                "locked": int(locked or 0),
            }
            for wiki, tracked, blocked, locked in rows
        }

    def optout_counts(self) -> dict[str, int]:
        with self.database.session() as session:
            rows = session.execute(
                select(PageOptOut.wiki, func.count()).group_by(PageOptOut.wiki)
            ).all()
            return {str(wiki): int(count) for wiki, count in rows}

    def get_state(self, key: str) -> str | None:
        with self.database.session() as session:
            state = session.get(AppState, key)
            return state.value if state else None

    def set_state(self, key: str, value: str) -> None:
        with self.database.session() as session, session.begin():
            state = session.get(AppState, key)
            if state is None:
                session.add(AppState(key=key, value=value))
            else:
                state.value = value
                state.updated_at = utcnow()

    def stats(self) -> dict[str, int]:
        with self.database.session() as session:
            ready = session.scalar(select(func.count()).select_from(AttributionResult)) or 0
            rows = session.execute(
                select(WorkItem.state, func.count()).group_by(WorkItem.state)
            ).all()
            return {"ready": int(ready), **{str(state): int(count) for state, count in rows}}

    def cleanup(self, queue_days: int = 30, superseded_result_days: int = 30) -> dict[str, int]:
        queue_cutoff = utcnow() - timedelta(days=queue_days)
        result_cutoff = utcnow() - timedelta(days=superseded_result_days)
        with self.database.session() as session, session.begin():
            removed_queue = session.execute(
                delete(WorkItem).where(
                    WorkItem.state.in_(("dead", "superseded")),
                    WorkItem.updated_at < queue_cutoff,
                )
            ).rowcount

            newer = aliased(AttributionResult)
            obsolete_ids = session.scalars(
                select(AttributionResult.id)
                .join(
                    newer,
                    and_(
                        newer.wiki == AttributionResult.wiki,
                        newer.page_id == AttributionResult.page_id,
                        newer.algorithm_version == AttributionResult.algorithm_version,
                        newer.computed_at > AttributionResult.computed_at,
                    ),
                )
                .where(AttributionResult.computed_at < result_cutoff)
                .distinct()
                .limit(10_000)
            ).all()
            removed_results = 0
            if obsolete_ids:
                removed_results = session.execute(
                    delete(AttributionResult).where(AttributionResult.id.in_(obsolete_ids))
                ).rowcount
        return {
            "queue": int(removed_queue or 0),
            "results": int(removed_results or 0),
        }
