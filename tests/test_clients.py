import json

import httpx
import pytest

from wikifame.clients import WikiWhoClient
from wikifame.errors import ResponseTooLargeError


def make_client(payload: dict, max_bytes: int = 10_000) -> WikiWhoClient:
    client = WikiWhoClient("https://example.test", "tests", 1, max_bytes)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode())

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
