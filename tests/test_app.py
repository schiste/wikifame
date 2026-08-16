from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from wikifame.app import create_app
from wikifame.config import Settings
from wikifame.models import utcnow
from wikifame.runtime import build_runtime


def test_cache_miss_then_immutable_ready_response(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        algorithm_version="test-v1",
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)

    with TestClient(app) as client:
        pending = client.get("/v1/frwiki/pages/100?revision_id=200")
        assert pending.status_code == 202
        assert pending.json()["status"] == "pending"

        runtime.repository.save_result(
            {
                "wiki": "frwiki",
                "page_id": 100,
                "revision_id": 200,
                "algorithm_version": "test-v1",
                "title": "France",
                "metric": "test-metric",
                "contributors": [
                    {"user_id": 1, "username": "Alice", "token_count": 10, "share": 0.5}
                ],
                "distinct_contributors": 4,
                "count_limited": False,
                "countable_tokens": 20,
                "wikiwho_revision_id": 200,
                "computed_at": utcnow(),
            }
        )

        ready = client.get("/v1/frwiki/pages/100?revision_id=200")
        assert ready.status_code == 200
        assert ready.json()["other_contributors"] == 3
        assert "immutable" in ready.headers["cache-control"]


def test_v2_serves_fresh_page_result_across_revisions(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'fresh-v2.db'}",
        algorithm_version="test-v2",
        page_freshness_seconds=90 * 24 * 60 * 60,
        page_cache_seconds=86400,
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)

    with TestClient(app) as client:
        runtime.repository.save_result(
            {
                "wiki": "frwiki",
                "page_id": 100,
                "revision_id": 200,
                "algorithm_version": "test-v2",
                "title": "France",
                "metric": "test-metric",
                "contributors": [
                    {"user_id": 1, "username": "Alice", "token_count": 10, "share": 0.5}
                ],
                "distinct_contributors": 4,
                "count_limited": False,
                "countable_tokens": 20,
                "wikiwho_revision_id": 200,
                "computed_at": utcnow(),
            }
        )

        ready = client.get("/v2/frwiki/pages/100?revision_id=201")

        assert ready.status_code == 200
        assert ready.json()["requested_revision_id"] == 201
        assert ready.json()["source_revision_id"] == 200
        assert ready.json()["is_fresh"] is True
        assert ready.json()["refreshing"] is False
        assert ready.headers["cache-control"].startswith("public, max-age=86400")
        assert "immutable" not in ready.headers["cache-control"]
        assert runtime.repository.get_work("frwiki", 100, 201, "test-v2") is None


def test_v2_serves_stale_result_while_enqueuing_current_revision(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'stale-v2.db'}",
        algorithm_version="test-v2",
        page_freshness_seconds=90 * 24 * 60 * 60,
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)

    with TestClient(app) as client:
        runtime.repository.save_result(
            {
                "wiki": "frwiki",
                "page_id": 100,
                "revision_id": 200,
                "algorithm_version": "test-v2",
                "title": "France",
                "metric": "test-metric",
                "contributors": [],
                "distinct_contributors": 4,
                "count_limited": False,
                "countable_tokens": 20,
                "wikiwho_revision_id": 200,
                "computed_at": utcnow() - timedelta(days=91),
            }
        )

        stale = client.get("/v2/frwiki/pages/100?revision_id=201")

        assert stale.status_code == 200
        assert stale.json()["source_revision_id"] == 200
        assert stale.json()["is_fresh"] is False
        assert stale.json()["refreshing"] is True
        work = runtime.repository.get_work("frwiki", 100, 201, "test-v2")
        assert work is not None and work.state == "pending"


def test_v2_can_refresh_an_expired_result_for_the_same_revision(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'same-revision-v2.db'}",
        algorithm_version="test-v2",
        page_freshness_seconds=90 * 24 * 60 * 60,
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)

    with TestClient(app) as client:
        runtime.repository.save_result(
            {
                "wiki": "frwiki",
                "page_id": 100,
                "revision_id": 200,
                "algorithm_version": "test-v2",
                "title": "France",
                "metric": "test-metric",
                "contributors": [],
                "distinct_contributors": 4,
                "count_limited": False,
                "countable_tokens": 20,
                "wikiwho_revision_id": 200,
                "computed_at": utcnow() - timedelta(days=91),
            }
        )

        stale = client.get("/v2/frwiki/pages/100?revision_id=200")

        assert stale.status_code == 200
        assert stale.json()["refreshing"] is True
        assert runtime.repository.get_work("frwiki", 100, 200, "test-v2") is not None


def test_v2_returns_pending_when_page_has_never_been_calculated(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'pending-v2.db'}",
        algorithm_version="test-v2",
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)

    with TestClient(app) as client:
        pending = client.get("/v2/frwiki/pages/100?revision_id=200")

        assert pending.status_code == 202
        assert pending.headers["cache-control"] == "no-store"
        assert pending.json()["requested_revision_id"] == 200
        assert runtime.repository.get_work("frwiki", 100, 200, "test-v2") is not None
