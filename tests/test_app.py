from dataclasses import replace
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
