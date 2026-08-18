from __future__ import annotations

from dataclasses import dataclass

from wikipeople.config import Settings, get_settings
from wikipeople.db import Database
from wikipeople.repository import Repository
from wikipeople.sites import SiteResolver


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
