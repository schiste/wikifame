from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote_plus


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    toolsdb_user = os.getenv("TOOL_TOOLSDB_USER")
    toolsdb_password = os.getenv("TOOL_TOOLSDB_PASSWORD")
    if toolsdb_user and toolsdb_password:
        database_name = os.getenv("TOOLSDB_DATABASE", f"{toolsdb_user}__wikifame")
        return (
            f"mysql+pymysql://{quote_plus(toolsdb_user)}:{quote_plus(toolsdb_password)}"
            f"@tools.db.svc.wikimedia.cloud/{quote_plus(database_name)}?charset=utf8mb4"
        )

    return "sqlite:///./wikifame.db"


@dataclass(frozen=True)
class Settings:
    database_url: str
    user_agent: str
    algorithm_version: str
    supported_wikis: tuple[str, ...]
    cors_origins: tuple[str, ...]
    wikiwho_base_url: str
    request_timeout_seconds: float
    wikiwho_timeout_seconds: float
    wikiwho_max_response_bytes: int
    candidate_pool_size: int
    minimum_tokens: int
    minimum_share: float
    worker_poll_seconds: float
    worker_lease_seconds: int
    worker_max_attempts: int
    dead_retry_seconds: int
    ready_cache_seconds: int
    methodology_url: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=_database_url(),
            user_agent=os.getenv(
                "WIKIFAME_USER_AGENT",
                "WikiFame/0.1 (https://github.com/schiste/wikifame)",
            ),
            algorithm_version=os.getenv("ALGORITHM_VERSION", "surviving-tokens-v1"),
            supported_wikis=_csv(os.getenv("SUPPORTED_WIKIS", "frwiki")),
            cors_origins=_csv(
                os.getenv(
                    "CORS_ORIGINS",
                    "https://fr.wikipedia.org,https://fr.m.wikipedia.org",
                )
            ),
            wikiwho_base_url=os.getenv(
                "WIKIWHO_BASE_URL", "https://wikiwho-api.wmcloud.org"
            ).rstrip("/"),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
            wikiwho_timeout_seconds=float(os.getenv("WIKIWHO_TIMEOUT_SECONDS", "90")),
            wikiwho_max_response_bytes=int(
                os.getenv("WIKIWHO_MAX_RESPONSE_BYTES", str(32 * 1024 * 1024))
            ),
            candidate_pool_size=int(os.getenv("CANDIDATE_POOL_SIZE", "50")),
            minimum_tokens=int(os.getenv("MINIMUM_TOKENS", "20")),
            minimum_share=float(os.getenv("MINIMUM_SHARE", "0.01")),
            worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "2")),
            worker_lease_seconds=int(os.getenv("WORKER_LEASE_SECONDS", "300")),
            worker_max_attempts=int(os.getenv("WORKER_MAX_ATTEMPTS", "8")),
            dead_retry_seconds=int(os.getenv("DEAD_RETRY_SECONDS", "86400")),
            ready_cache_seconds=int(os.getenv("READY_CACHE_SECONDS", "31536000")),
            methodology_url=os.getenv(
                "METHODOLOGY_URL",
                "https://github.com/schiste/wikifame/blob/main/docs/architecture.md",
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
