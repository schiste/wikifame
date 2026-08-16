from __future__ import annotations

from dataclasses import dataclass

from wikifame.config import Settings, get_settings
from wikifame.db import Database
from wikifame.repository import Repository


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    database: Database
    repository: Repository


def build_runtime(settings: Settings | None = None) -> Runtime:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings.database_url)
    return Runtime(
        settings=resolved_settings,
        database=database,
        repository=Repository(database),
    )
