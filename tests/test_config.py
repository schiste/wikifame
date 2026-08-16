from pytest import MonkeyPatch

from wikifame.config import Settings


def test_toolforge_credentials_build_default_toolsdb_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TOOL_TOOLSDB_USER", "s12345")
    monkeypatch.setenv("TOOL_TOOLSDB_PASSWORD", "secret:/ value")
    monkeypatch.delenv("TOOLSDB_DATABASE", raising=False)

    settings = Settings.from_env()

    assert settings.database_url == (
        "mysql+pymysql://s12345:secret%3A%2F+value@"
        "tools.db.svc.wikimedia.cloud/s12345__wikifame?charset=utf8mb4"
    )


def test_explicit_database_url_wins_over_toolforge_credentials(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///explicit.db")
    monkeypatch.setenv("TOOL_TOOLSDB_USER", "s12345")
    monkeypatch.setenv("TOOL_TOOLSDB_PASSWORD", "secret")

    assert Settings.from_env().database_url == "sqlite:///explicit.db"


def test_page_freshness_defaults_are_explicit(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("PAGE_FRESHNESS_SECONDS", raising=False)
    monkeypatch.delenv("PAGE_CACHE_SECONDS", raising=False)
    monkeypatch.delenv("PAGE_STALE_WHILE_REVALIDATE_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.page_freshness_seconds == 90 * 24 * 60 * 60
    assert settings.page_cache_seconds == 24 * 60 * 60
    assert settings.page_stale_while_revalidate_seconds == 7 * 24 * 60 * 60
