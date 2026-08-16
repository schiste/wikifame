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


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(191), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
