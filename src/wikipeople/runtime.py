from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from wikipeople.config import Settings, get_settings
from wikipeople.db import Database
from wikipeople.repository import Repository
from wikipeople.sites import SiteResolver


def configure_logging() -> None:
    """Configure logging for a job entry point.

    `httpx` emits one INFO line per request. The worker makes three requests per page,
    so at last count 77% of its output was request noise and `toolforge jobs logs
    --last 4000` reached back only an hour. Every request that carries meaning is
    already logged by its caller, and a failed one still surfaces because it raises.
    """
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    database: Database
    repository: Repository
    resolver: SiteResolver


def build_runtime(settings: Settings | None = None) -> Runtime:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings.database_url)
    return Runtime(
        settings=resolved_settings,
        database=database,
        repository=Repository(database),
        resolver=SiteResolver(resolved_settings.wikiwho_languages),
    )
