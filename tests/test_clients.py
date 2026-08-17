import json
from datetime import date

import httpx
import pytest

from wikifame.clients import AnalyticsClient, MediaWikiClient, WikiWhoClient
from wikifame.errors import PermanentDataError, ResponseTooLargeError, RetryableUpstreamError


def make_client(payload: dict, max_bytes: int = 10_000) -> WikiWhoClient:
    client = WikiWhoClient("https://example.test", "tests", 1, max_bytes)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode())

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def make_failing_client(status: int) -> WikiWhoClient:
    client = WikiWhoClient("https://example.test", "tests", 1, 10_000)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b'{"Error":"nope"}')

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_wikiwho_extracts_tokens_from_real_nested_shape() -> None:
    client = make_client(
        {
            "success": True,
            "revisions": [
                {
                    "200": {
                        "tokens": [
                            {"str": "Bonjour", "editor": "1"},
                            {"str": "monde", "editor": "2"},
                        ]
                    }
                }
            ],
        }
    )

    assert client.fetch_revision("frwiki", 200)[0]["editor"] == "1"
    client.close()


def test_wikiwho_response_size_is_bounded() -> None:
    client = make_client(
        {"success": True, "revisions": [{"200": {"tokens": ["x" * 100]}}]},
        max_bytes=20,
    )

    with pytest.raises(ResponseTooLargeError):
        client.fetch_revision("frwiki", 200)
    client.close()


def test_a_refused_revision_is_permanent_rather_than_retried() -> None:
    """WikiWho answers 400 to state a fact about the page, and facts do not heal.

    The two forms it sends are a rejected namespace and a revision it has no article
    for. Retrying either burns the whole thirteen-second chain to arrive at the same
    answer, and leaves the job marked retryable so it is revived again later.
    """
    client = make_failing_client(400)

    with pytest.raises(PermanentDataError):
        client.fetch_revision("frwiki", 200)
    client.close()


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_a_busy_or_broken_wikiwho_is_still_worth_retrying(status: int) -> None:
    """Only 400 changed meaning: everything else remains a hiccup to wait out."""
    client = make_failing_client(status)

    with pytest.raises(RetryableUpstreamError):
        client.fetch_revision("frwiki", 200)
    client.close()


def test_global_groups_are_read_from_centralauth_and_asked_for_once() -> None:
    client = MediaWikiClient("tests", 1)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["guiuser"])
        return httpx.Response(
            200,
            json={"query": {"globaluserinfo": {"name": "Addbot", "groups": ["local-bot"]}}},
        )

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    assert client.global_groups("frwiki", "Addbot") == frozenset({"local-bot"})
    assert client.global_groups("frwiki", "Addbot") == frozenset({"local-bot"})
    assert asked == ["Addbot"]
    client.close()


def test_an_account_centralauth_does_not_know_has_no_global_groups() -> None:
    client = MediaWikiClient("tests", 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": {"globaluserinfo": {"missing": True}}})

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    assert client.global_groups("frwiki", "Inconnu") == frozenset()
    client.close()


def test_unpublished_pageview_day_returns_none() -> None:
    client = AnalyticsClient("tests", 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"title": "Not Found"})

    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    assert client.top_pages("frwiki", date(2026, 8, 15)) is None
    client.close()
