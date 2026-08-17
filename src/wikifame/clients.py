from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from itertools import islice
from typing import Any
from urllib.parse import quote, unquote

import httpx

from wikifame.errors import PermanentDataError, ResponseTooLargeError, RetryableUpstreamError
from wikifame.policy import ResolvedUser
from wikifame.sites import SiteResolver


@dataclass(frozen=True)
class PageMetadata:
    page_id: int
    revision_id: int
    title: str
    namespace: int


@dataclass(frozen=True)
class EditorCount:
    count: int
    limited: bool


def _batched(values: Iterable[int], size: int) -> Iterable[list[int]]:
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


class MediaWikiClient:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float,
        resolver: SiteResolver | None = None,
    ) -> None:
        self.resolver = resolver or SiteResolver()
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    def host(self, wiki: str) -> str:
        return self.resolver.host(wiki)

    def _action(self, wiki: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.get(
                f"https://{self.host(wiki)}/w/api.php",
                params={"format": "json", "formatversion": 2, **params},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RetryableUpstreamError(f"Action API indisponible : {error}") from error
        if "error" in data:
            raise RetryableUpstreamError(f"Erreur Action API : {data['error']}")
        return data

    def get_page(self, wiki: str, page_id: int) -> PageMetadata:
        data = self._action(
            wiki,
            {
                "action": "query",
                "pageids": page_id,
                "prop": "revisions",
                "rvprop": "ids",
            },
        )
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            raise PermanentDataError(f"Page inexistante : {wiki}/{page_id}")
        page = pages[0]
        revisions = page.get("revisions", [])
        if not revisions:
            raise PermanentDataError(f"Page sans révision : {wiki}/{page_id}")
        return PageMetadata(
            page_id=int(page["pageid"]),
            revision_id=int(revisions[0]["revid"]),
            title=str(page["title"]),
            namespace=int(page["ns"]),
        )

    def get_editor_count(self, wiki: str, title: str) -> EditorCount:
        encoded_title = quote(title.replace(" ", "_"), safe="")
        url = f"https://{self.host(wiki)}/w/rest.php/v1/page/{encoded_title}/history/counts/editors"
        try:
            response = self.client.get(url)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RetryableUpstreamError(f"Comptage REST indisponible : {error}") from error
        if not isinstance(data.get("count"), int):
            raise RetryableUpstreamError("Réponse de comptage REST invalide")
        return EditorCount(count=data["count"], limited=bool(data.get("limit")))

    def get_bot_contributor_count(self, wiki: str, page_id: int) -> int:
        continuation: dict[str, Any] = {}
        count = 0
        for _page in range(100):
            data = self._action(
                wiki,
                {
                    "action": "query",
                    "pageids": page_id,
                    "prop": "contributors",
                    "pcrights": "bot",
                    "pclimit": "max",
                    **continuation,
                },
            )
            pages = data.get("query", {}).get("pages", [])
            if pages:
                count += len(pages[0].get("contributors", []))
            continuation = data.get("continue", {})
            if not continuation:
                return count
        raise RetryableUpstreamError("Pagination des bots anormalement longue")

    def resolve_users(self, wiki: str, user_ids: Iterable[int]) -> dict[int, ResolvedUser]:
        users: dict[int, ResolvedUser] = {}
        for batch in _batched(user_ids, 50):
            data = self._action(
                wiki,
                {
                    "action": "query",
                    "list": "users",
                    "ususerids": "|".join(str(user_id) for user_id in batch),
                    "usprop": "groups",
                },
            )
            for item in data.get("query", {}).get("users", []):
                user_id = int(item.get("userid", 0))
                if user_id <= 0:
                    continue
                users[user_id] = ResolvedUser(
                    user_id=user_id,
                    username=str(item.get("name", user_id)),
                    groups=frozenset(item.get("groups", [])),
                    missing=bool(item.get("missing") or item.get("invalid")),
                )
        return users

    def resolve_titles(self, wiki: str, titles: list[str]) -> list[PageMetadata]:
        pages: list[PageMetadata] = []
        for start in range(0, len(titles), 50):
            data = self._action(
                wiki,
                {
                    "action": "query",
                    "titles": "|".join(titles[start : start + 50]),
                    "redirects": 1,
                    "prop": "revisions",
                    "rvprop": "ids",
                },
            )
            for page in data.get("query", {}).get("pages", []):
                revisions = page.get("revisions", [])
                if page.get("missing") or not revisions:
                    continue
                pages.append(
                    PageMetadata(
                        page_id=int(page["pageid"]),
                        revision_id=int(revisions[0]["revid"]),
                        title=str(page["title"]),
                        namespace=int(page["ns"]),
                    )
                )
        return pages

    def all_pages_batch(
        self, wiki: str, cursor: str | None
    ) -> tuple[list[PageMetadata], str | None]:
        params: dict[str, Any] = {
            "action": "query",
            "generator": "allpages",
            "gapnamespace": 0,
            "gaplimit": "max",
            "prop": "revisions",
            "rvprop": "ids",
        }
        if cursor:
            params["gapcontinue"] = cursor
        data = self._action(wiki, params)
        pages = []
        for page in data.get("query", {}).get("pages", []):
            revisions = page.get("revisions", [])
            if not revisions:
                continue
            pages.append(
                PageMetadata(
                    page_id=int(page["pageid"]),
                    revision_id=int(revisions[0]["revid"]),
                    title=str(page["title"]),
                    namespace=int(page["ns"]),
                )
            )
        return pages, data.get("continue", {}).get("gapcontinue")

    def close(self) -> None:
        self.client.close()


class WikiWhoClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        timeout_seconds: float,
        max_response_bytes: int,
        resolver: SiteResolver | None = None,
    ) -> None:
        self.base_url = base_url
        self.max_response_bytes = max_response_bytes
        self.resolver = resolver or SiteResolver()
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    def fetch_revision(self, wiki: str, revision_id: int) -> list[dict[str, Any]]:
        language = self.resolver.require_language(wiki)
        url = f"{self.base_url}/{language}/api/v1.0.0-beta/rev_content/rev_id/{revision_id}/"
        params = {
            "o_rev_id": "false",
            "editor": "true",
            "token_id": "false",
            "out": "false",
            "in": "false",
        }
        try:
            with self.client.stream("GET", url, params=params) as response:
                # 400 is WikiWho stating a fact about the page, not a hiccup: the two
                # forms observed are a rejected namespace and a revision it has no
                # article for. Neither heals, so retrying costs thirteen seconds and two
                # more upstream calls to reach the same answer. Nor is it a lag signal —
                # measured against fr.wikipedia edits seconds old, WikiWho answered 200
                # with the exact revision every time, so a fresh revision is not the
                # reason for a 400.
                if response.status_code == 400:
                    raise PermanentDataError(
                        f"WikiWho ne sert pas la révision {revision_id} (HTTP 400)"
                    )
                if response.status_code in {408, 425, 429} or response.status_code >= 500:
                    raise RetryableUpstreamError(
                        f"WikiWho HTTP {response.status_code} pour la révision {revision_id}"
                    )
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise ResponseTooLargeError(
                            f"Réponse WikiWho supérieure à {self.max_response_bytes} octets"
                        )
                    chunks.append(chunk)
            data = json.loads(b"".join(chunks))
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise RetryableUpstreamError(f"WikiWho indisponible : {error}") from error

        if not data.get("success"):
            raise RetryableUpstreamError(f"WikiWho a refusé la révision : {data.get('message')}")
        for revision_wrapper in data.get("revisions", []):
            revision = revision_wrapper.get(str(revision_id))
            if isinstance(revision, dict) and isinstance(revision.get("tokens"), list):
                return revision["tokens"]
        raise RetryableUpstreamError(
            f"WikiWho n’a pas encore renvoyé la révision exacte {revision_id}"
        )

    def close(self) -> None:
        self.client.close()


class AnalyticsClient:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float,
        resolver: SiteResolver | None = None,
    ) -> None:
        self.resolver = resolver or SiteResolver()
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    def top_pages(self, wiki: str, day: date) -> list[str] | None:
        host = self.resolver.host(wiki)
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            f"{host}/all-access/{day:%Y/%m/%d}"
        )
        try:
            response = self.client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RetryableUpstreamError(f"Pageviews indisponible : {error}") from error
        items = data.get("items", [])
        articles = items[0].get("articles", []) if items else []
        return [unquote(str(article["article"])).replace("_", " ") for article in articles]

    def close(self) -> None:
        self.client.close()
