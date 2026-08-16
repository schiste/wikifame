from pathlib import Path

GADGET_SOURCE = (Path(__file__).parents[1] / "ContributeursHumains.js").read_text()


def test_production_gadget_contains_no_page_fixture() -> None:
    assert "CONTRIBUTION_FIXTURES" not in GADGET_SOURCE
    assert "Victor Hugo" not in GADGET_SOURCE
    assert "Jean de la Fontaine" not in GADGET_SOURCE


def test_pending_attribution_is_retried_without_becoming_an_error() -> None:
    assert "data.status !== 'pending'" in GADGET_SOURCE
    assert "PENDING_RETRY_DELAYS_MS" in GADGET_SOURCE
    assert "Attribution en cours de calcul." not in GADGET_SOURCE


def test_gadget_uses_page_freshness_api_and_bounded_session_cache() -> None:
    assert "'/v2/frwiki/pages/'" in GADGET_SOURCE
    assert "CLIENT_CACHE_MAX_AGE_MS" in GADGET_SOURCE
    assert "computed_at" in GADGET_SOURCE
    cache_key_body = GADGET_SOURCE.split("function getCacheKey()", 1)[1].split(
        "function readCache", 1
    )[0]
    assert "wgCurRevisionId" not in cache_key_body
