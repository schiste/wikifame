from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from wikifame.db import Database
from wikifame.models import AppState, AttributionResult, WorkItem, utcnow


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
