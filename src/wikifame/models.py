from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SQL_ID = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    """Return a timezone-naive UTC value, suitable for MariaDB DATETIME."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class AttributionResult(Base):
    __tablename__ = "attribution_results"
    __table_args__ = (
        UniqueConstraint(
            "wiki",
            "page_id",
            "revision_id",
            "algorithm_version",
            name="uq_result_revision_algorithm",
        ),
        Index("ix_result_page", "wiki", "page_id", "algorithm_version", "computed_at"),
    )

    id: Mapped[int] = mapped_column(SQL_ID, primary_key=True, autoincrement=True)
    wiki: Mapped[str] = mapped_column(String(32), nullable=False)
    page_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    contributors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    distinct_contributors: Mapped[int] = mapped_column(Integer, nullable=False)
    count_limited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    countable_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    wikiwho_revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class WorkItem(Base):
    __tablename__ = "work_queue"
    __table_args__ = (
        UniqueConstraint(
            "wiki",
            "page_id",
            "revision_id",
            "algorithm_version",
            name="uq_work_revision_algorithm",
        ),
        Index("ix_work_claim", "state", "available_at", "priority", "created_at"),
        Index("ix_work_page", "wiki", "page_id", "algorithm_version"),
    )

    id: Mapped[int] = mapped_column(SQL_ID, primary_key=True, autoincrement=True)
    wiki: Mapped[str] = mapped_column(String(32), nullable=False)
    page_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class ActiveWiki(Base):
    """A wiki that has produced at least one real result.

    Workers register a wiki here the first time they store a result for it. The
    daily prewarm job reads this table, so a wiki discovered through genuine reader
    demand starts keeping its own top-1000 warm without any configuration change.
    Registration is deliberately driven by a completed calculation rather than by an
    API request, so scripted traffic cannot enrol wikis into scheduled work.
    """

    __tablename__ = "active_wikis"

    wiki: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_result_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(191), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class PageOptOut(Base):
    """A page whose contributors are counted but not named.

    Rows are materialised from an on-wiki list by the `optout` job and read on every
    ready response. Storing page IDs rather than titles keeps the serve path to one
    primary-key lookup: it never has to normalise a title, and it is not allowed to ask
    MediaWiki anything. A page also keeps its ID across a rename, so the opt-out follows
    the article rather than the name someone happened to list it under.

    `source` records which list entry put the row here — the page itself, or the
    category it belongs to — so "why is this article opted out?" is answerable without
    re-deriving the whole list.
    """

    __tablename__ = "page_optout"

    wiki: Mapped[str] = mapped_column(String(64), primary_key=True)
    page_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ContributorStanding(Base):
    """What each named account's wiki has decided about it, materialised for the serve path.

    Only accounts WikiFame actually names are tracked. That is a few thousand rows rather
    than the wiki's whole block log, because the top three contributors of popular
    articles are the same prolific editors over and over.

    A row is a fact with a timestamp, not a verdict: the threshold that turns a block
    into a withheld name is applied when the response is built (`is_nameable_account`),
    so an operator can move it, or switch the rule off, without this table changing and
    without a single result being recomputed. ADR-0009 explains why the verdict may not
    be stored here and may not be baked into `attribution_results`.

    `lock_checked_at` is separate from `synced_at` because the two facts cost very
    different amounts to obtain. Block status arrives fifty accounts per request; a
    global lock costs one request per account, so locks are refreshed on a rotation and
    each row remembers when its turn last came.

    `lock_reason` is stored because the flag alone does not say what the lock means.
    Stewards use one mechanism for opposite purposes, and a memorial lock must not read
    as a ban; `is_sanction_lock` is what tells them apart, and it needs the text.
    """

    __tablename__ = "contributor_standing"
    __table_args__ = (Index("ix_standing_lock_age", "wiki", "lock_checked_at"),)

    wiki: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    block_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    block_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    globally_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lock_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lock_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
