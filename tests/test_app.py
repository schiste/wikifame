from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from wikifame.app import create_app
from wikifame.config import Settings
from wikifame.models import utcnow
from wikifame.runtime import build_runtime


def test_cache_miss_then_revalidatable_ready_response(tmp_path: Path) -> None:
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
        # The revision is fixed, but the policy applied to it is not, so the answer must
        # stay checkable rather than being declared final for a year.
        assert "immutable" not in ready.headers["cache-control"]
        assert ready.headers["etag"]


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


def test_any_wikiwho_covered_wikipedia_is_served_without_configuration(tmp_path: Path) -> None:
    """SUPPORTED_WIKIS=* means a reader on any covered Wikipedia gets an answer."""
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'universal.db'}",
        algorithm_version="test-v1",
        supported_wikis=("*",),
    )
    app = create_app(build_runtime(settings))

    with TestClient(app) as client:
        for wiki in ("frwiki", "dewiki", "jawiki", "simplewiki"):
            response = client.get(f"/v2/{wiki}/pages/100?revision_id=200")
            assert response.status_code == 202, wiki
            assert response.json()["wiki"] == wiki


def test_projects_without_wikiwho_coverage_are_refused(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'refused.db'}",
        algorithm_version="test-v1",
        supported_wikis=("*",),
    )
    app = create_app(build_runtime(settings))

    with TestClient(app) as client:
        for wiki in ("commonswiki", "wikidatawiki", "frwiktionary"):
            assert client.get(f"/v2/{wiki}/pages/100?revision_id=200").status_code == 404, wiki


def test_explicit_allowlist_still_narrows_serving(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'allowlist.db'}",
        algorithm_version="test-v1",
        supported_wikis=("frwiki",),
    )
    app = create_app(build_runtime(settings))

    with TestClient(app) as client:
        assert client.get("/v2/frwiki/pages/100?revision_id=200").status_code == 202
        assert client.get("/v2/dewiki/pages/100?revision_id=200").status_code == 404


def _result(algorithm_version: str, username: str) -> dict:
    return {
        "wiki": "frwiki",
        "page_id": 100,
        "revision_id": 200,
        "algorithm_version": algorithm_version,
        "title": "France",
        "metric": "test-metric",
        "contributors": [{"user_id": 1, "username": username, "token_count": 10, "share": 0.5}],
        "distinct_contributors": 4,
        "count_limited": False,
        "countable_tokens": 20,
        "wikiwho_revision_id": 200,
        "computed_at": utcnow(),
    }


def test_an_unchanged_answer_costs_a_request_but_not_a_body(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / 'etag.db'}",
        algorithm_version="test-v1",
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)

    with TestClient(app) as client:
        runtime.repository.save_result(_result("test-v1", "Alice"))

        first = client.get("/v2/frwiki/pages/100?revision_id=200")
        assert first.status_code == 200

        again = client.get(
            "/v2/frwiki/pages/100?revision_id=200",
            headers={"If-None-Match": first.headers["etag"]},
        )
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == first.headers["etag"]


def test_a_reader_holding_the_previous_policys_answer_is_not_told_it_is_current(
    tmp_path: Path,
) -> None:
    """
    The defect this guards against was observed in production: after a policy change the
    API was correct and the browser still drew the old names, because the reader's copy
    had a year of freshness ahead of it and nothing in the URL had changed. A validator
    only helps if it changes when the policy does.
    """
    database_url = f"sqlite:///{tmp_path / 'policy-change.db'}"
    before = build_runtime(
        replace(Settings.from_env(), database_url=database_url, algorithm_version="test-v1")
    )
    with TestClient(create_app(before)) as client:
        before.repository.save_result(_result("test-v1", "Roland45-Bot"))
        old = client.get("/v2/frwiki/pages/100?revision_id=200")
        assert old.json()["contributors"][0]["username"] == "Roland45-Bot"

    after = build_runtime(
        replace(Settings.from_env(), database_url=database_url, algorithm_version="test-v2")
    )
    with TestClient(create_app(after)) as client:
        after.repository.save_result(_result("test-v2", "Roland45"))

        response = client.get(
            "/v2/frwiki/pages/100?revision_id=200",
            headers={"If-None-Match": old.headers["etag"]},
        )

        assert response.status_code == 200, "a superseded answer must not validate as current"
        assert response.json()["contributors"][0]["username"] == "Roland45"
