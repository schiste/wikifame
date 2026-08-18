from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wikipeople.app import create_app
from wikipeople.clients import CategoryMember, TitleInfo
from wikipeople.config import Settings
from wikipeople.errors import RetryableUpstreamError
from wikipeople.models import utcnow
from wikipeople.optout import collect_entries, parse_optout_page, resolve_target_wikis, sync_wiki
from wikipeople.runtime import Runtime, build_runtime

PAGE = """
Cette page liste les articles dont WikiPeople n'affiche pas les noms.
La discussion se fait en [[Discussion Wikipédia:WikiPeople/opt-out|page de discussion]].

== Articles ==
* [[Jean Dupont]]
* [[Affaire Machin]] <!-- demande OTRS #2026081710 -->
* [[Jean Dupont]]

== Catégories ==
* [[:Catégorie:Personnalité vivante]]

Voir aussi [[Wikipédia:WikiPeople]] pour le fonctionnement.
"""


class FakeMediaWiki:
    def __init__(
        self,
        wikitext: str | None,
        infos: list[TitleInfo] | None = None,
        members: dict[str, list[CategoryMember]] | None = None,
        fail: bool = False,
    ) -> None:
        self.wikitext = wikitext
        self.infos = infos or []
        self.members = members or {}
        self.fail = fail

    def get_wikitext(self, _wiki: str, _title: str) -> str | None:
        if self.fail:
            raise RetryableUpstreamError("Action API indisponible")
        return self.wikitext

    def classify_titles(self, _wiki: str, titles: list[str]) -> list[TitleInfo]:
        return [info for info in self.infos if info.title in titles]

    def category_members(
        self, _wiki: str, category: str, limit: int
    ) -> tuple[list[CategoryMember], bool]:
        members = self.members.get(category, [])
        return members[:limit], len(members) > limit


def test_only_bulleted_links_are_entries() -> None:
    """The page has to stay writable by the people who maintain it.

    Prose, headings, a note explaining an entry, and the link to the discussion that
    produced it all sit on the same page as the list and none of them is an instruction.
    """
    assert parse_optout_page(PAGE) == [
        "Jean Dupont",
        "Affaire Machin",
        "Catégorie:Personnalité vivante",
    ]


def test_a_link_hidden_in_a_comment_is_not_an_entry() -> None:
    """A commented-out entry is a removed entry, which is how a wiki editor undoes one."""
    assert parse_optout_page("* [[Gardé]]\n<!--\n* [[Retiré]]\n-->\n") == ["Gardé"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("* [[Page|autre nom]]", ["Page"]),
        ("*[[Page#Section]]", ["Page"]),
        ("**  [[ Page_avec_tiret_bas ]]", ["Page avec tiret bas"]),
        ("* [[:Catégorie:X]] et [[Y]]", ["Catégorie:X"]),
        ("[[Sans puce]]", []),
        ("* pas de lien", []),
        ("* [[]]", []),
    ],
)
def test_entry_shapes(line: str, expected: list[str]) -> None:
    assert parse_optout_page(line) == expected


def test_a_category_covers_its_members_and_an_article_keeps_its_own_reason() -> None:
    mediawiki = FakeMediaWiki(
        wikitext=None,
        infos=[
            TitleInfo(title="Jean Dupont", namespace=0, page_id=11),
            TitleInfo(title="Catégorie:Personnalité vivante", namespace=14, page_id=99),
            TitleInfo(title="Discussion:Quelque chose", namespace=1, page_id=77),
            TitleInfo(title="Page supprimée", namespace=0, page_id=None),
        ],
        members={
            "Catégorie:Personnalité vivante": [
                CategoryMember(page_id=11, title="Jean Dupont"),
                CategoryMember(page_id=12, title="Marie Durand"),
            ]
        },
    )

    entries, skipped = collect_entries(
        mediawiki,  # type: ignore[arg-type]
        "frwiki",
        [
            "Jean Dupont",
            "Catégorie:Personnalité vivante",
            "Discussion:Quelque chose",
            "Page supprimée",
        ],
        category_limit=5000,
    )

    by_id = {entry.page_id: entry for entry in entries}
    assert set(by_id) == {11, 12}
    assert by_id[11].source == "page"
    assert by_id[12].source == "category:Catégorie:Personnalité vivante"
    assert any("Page supprimée" in note for note in skipped)
    assert any("espace de noms 1" in note for note in skipped)


def test_a_category_past_the_cap_is_reported_rather_than_silently_halved() -> None:
    mediawiki = FakeMediaWiki(
        wikitext=None,
        infos=[TitleInfo(title="Catégorie:Immense", namespace=14, page_id=99)],
        members={
            "Catégorie:Immense": [CategoryMember(page_id=i, title=f"A{i}") for i in range(1, 6)]
        },
    )

    entries, skipped = collect_entries(
        mediawiki,  # type: ignore[arg-type]
        "frwiki",
        ["Catégorie:Immense"],
        category_limit=3,
    )

    assert len(entries) == 3
    assert any("tronquée" in note for note in skipped)


def _runtime(tmp_path: Path, name: str = "optout.db") -> Runtime:
    settings = replace(
        Settings.from_env(),
        database_url=f"sqlite:///{tmp_path / name}",
        algorithm_version="test-optout",
        optout_page="Project:WikiPeople/opt-out",
    )
    runtime = build_runtime(settings)
    runtime.database.create_schema()
    return runtime


def test_a_removed_entry_stops_hiding_the_names(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    listed = FakeMediaWiki(
        wikitext="* [[Jean Dupont]]",
        infos=[TitleInfo(title="Jean Dupont", namespace=0, page_id=11)],
    )

    assert sync_wiki(runtime, listed, "frwiki") == (1, 1, 0)  # type: ignore[arg-type]
    assert runtime.repository.is_opted_out("frwiki", 11) is True

    emptied = FakeMediaWiki(wikitext="Plus personne.", infos=[])
    assert sync_wiki(runtime, emptied, "frwiki") == (0, 0, 1)  # type: ignore[arg-type]
    assert runtime.repository.is_opted_out("frwiki", 11) is False


def test_an_unreachable_wiki_keeps_its_list_instead_of_losing_it(tmp_path: Path) -> None:
    """An empty list means "nobody is opted out any more". A network error does not.

    Conflating the two would turn one bad minute at the Action API into every opted-out
    article on the wiki being named again, which is the exact failure the list exists to
    prevent.
    """
    runtime = _runtime(tmp_path, "optout-outage.db")
    listed = FakeMediaWiki(
        wikitext="* [[Jean Dupont]]",
        infos=[TitleInfo(title="Jean Dupont", namespace=0, page_id=11)],
    )
    sync_wiki(runtime, listed, "frwiki")  # type: ignore[arg-type]

    with pytest.raises(RetryableUpstreamError):
        sync_wiki(runtime, FakeMediaWiki(wikitext=None, fail=True), "frwiki")  # type: ignore[arg-type]

    assert runtime.repository.is_opted_out("frwiki", 11) is True


def test_a_dry_run_reports_without_changing_what_is_served(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "optout-dry.db")
    listed = FakeMediaWiki(
        wikitext="* [[Jean Dupont]]",
        infos=[TitleInfo(title="Jean Dupont", namespace=0, page_id=11)],
    )

    assert sync_wiki(runtime, listed, "frwiki", dry_run=True) == (1, 0, 0)  # type: ignore[arg-type]
    assert runtime.repository.is_opted_out("frwiki", 11) is False


def test_only_wikis_that_have_served_something_are_read(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "optout-wikis.db")

    assert resolve_target_wikis(runtime, None) == []
    runtime.repository.register_active_wiki("frwiki")
    assert resolve_target_wikis(runtime, None) == ["frwiki"]
    assert resolve_target_wikis(runtime, "dewiki") == ["dewiki"]


def _save(runtime: Runtime) -> None:
    runtime.repository.save_result(
        {
            "wiki": "frwiki",
            "page_id": 100,
            "revision_id": 200,
            "algorithm_version": "test-optout",
            "title": "Jean Dupont",
            "metric": "surviving-tokens",
            "contributors": [
                {"user_id": 1, "username": "Alice", "token_count": 10, "share": 0.5},
                {"user_id": 2, "username": "Bob", "token_count": 5, "share": 0.25},
            ],
            "distinct_contributors": 47,
            "count_limited": False,
            "countable_tokens": 20,
            "wikiwho_revision_id": 200,
            "computed_at": utcnow(),
        }
    )


@pytest.mark.parametrize(
    "path", ["/v1/frwiki/pages/100?revision_id=200", "/v2/frwiki/pages/100?revision_id=200"]
)
def test_an_opted_out_page_is_served_as_a_total_with_no_names(tmp_path: Path, path: str) -> None:
    """Both endpoints, because a list only one of them honours is a list with a way round it."""
    runtime = _runtime(tmp_path, f"optout-serve-{path.count('v2')}.db")
    _save(runtime)
    app = create_app(runtime)

    with TestClient(app) as client:
        named = client.get(path)
        assert named.status_code == 200
        assert [c["username"] for c in named.json()["contributors"]] == ["Alice", "Bob"]
        assert named.json()["opted_out"] is False
        assert named.json()["other_contributors"] == 45

        sync_wiki(  # type: ignore[arg-type]
            runtime,
            FakeMediaWiki(
                wikitext="* [[Jean Dupont]]",
                infos=[TitleInfo(title="Jean Dupont", namespace=0, page_id=100)],
            ),
            "frwiki",
        )

        anonymous = client.get(path)
        assert anonymous.status_code == 200
        assert anonymous.json()["contributors"] == []
        assert anonymous.json()["opted_out"] is True
        # The count survives: the sentence becomes "written by 47 people" rather than
        # disappearing, and 47 is the whole total now that nobody is named separately.
        assert anonymous.json()["distinct_contributors"] == 47
        assert anonymous.json()["other_contributors"] == 47
        # Nothing was recomputed and nothing was deleted; only the presentation changed.
        assert runtime.repository.get_latest_result("frwiki", 100, "test-optout").contributors


def test_opting_out_invalidates_a_readers_cached_copy(tmp_path: Path) -> None:
    """The names must not survive in a browser that already holds them.

    ADR-0007 made every answer carry a body-derived ETag precisely so a change of this
    kind cannot validate as current. An opt-out is such a change.
    """
    runtime = _runtime(tmp_path, "optout-etag.db")
    _save(runtime)
    app = create_app(runtime)

    with TestClient(app) as client:
        first = client.get("/v2/frwiki/pages/100?revision_id=200")
        assert first.status_code == 200

        unchanged = client.get(
            "/v2/frwiki/pages/100?revision_id=200",
            headers={"If-None-Match": first.headers["etag"]},
        )
        assert unchanged.status_code == 304

        sync_wiki(  # type: ignore[arg-type]
            runtime,
            FakeMediaWiki(
                wikitext="* [[Jean Dupont]]",
                infos=[TitleInfo(title="Jean Dupont", namespace=0, page_id=100)],
            ),
            "frwiki",
        )

        after = client.get(
            "/v2/frwiki/pages/100?revision_id=200",
            headers={"If-None-Match": first.headers["etag"]},
        )
        assert after.status_code == 200, "a copy holding the names must not validate as current"
        assert after.json()["contributors"] == []


STARTER_PAGE = Path(__file__).resolve().parents[1] / "docs/onwiki/optout.fr.wiki"


def test_the_starter_page_ships_with_no_entries() -> None:
    """The copy communities paste must opt nobody out on arrival.

    It is dense with the syntax it documents — bulleted rules, bracketed examples,
    commented-out entries — and any of that read as an entry would hide names on an
    article nobody listed.
    """
    assert parse_optout_page(STARTER_PAGE.read_text(encoding="utf-8")) == []


def test_the_starter_page_documents_the_rules_the_parser_actually_applies() -> None:
    """The page tells editors what counts as an entry. This is that claim, executed.

    The page is not prose about the feature, it is the input format. A rule that drifts
    from the parser sends someone away believing an article is covered when it is not.
    """
    # "the first non-blank character after the bullet is a link"
    assert parse_optout_page("* [[Machin]]") == ["Machin"]
    assert parse_optout_page("* Voir [[Machin]]") == []
    # "only the first link is read; the rest of the line is free"
    assert parse_optout_page("* [[Machin]] — demandé en PdD, voir [[Truc]]") == ["Machin"]
    # "a display name, an anchor and underscores are tolerated"
    assert parse_optout_page("* [[Machin|un autre nom]]") == ["Machin"]
    assert parse_optout_page("* [[Machin#Section]]") == ["Machin"]
    assert parse_optout_page("* [[Machin_Truc]]") == ["Machin Truc"]
    # "nested bullets count as bullets"
    assert parse_optout_page("** [[Machin]]") == ["Machin"]
    # "a duplicate has no effect"
    assert parse_optout_page("* [[Machin]]\n* [[Machin]]") == ["Machin"]
    # "not an entry: prose, tables, numbered lists, indented lines"
    assert parse_optout_page("[[Machin]]") == []
    assert parse_optout_page("| [[Machin]] || et la suite") == []
    assert parse_optout_page("# [[Machin]]") == []
    assert parse_optout_page(": [[Machin]]") == []
    # "anything between comment markers, including an entry commented out"
    assert parse_optout_page("<!--\n* [[Machin]]\n-->") == []
    # "a colon prefixes a category link"
    assert parse_optout_page("* [[:Catégorie:Machin]]") == ["Catégorie:Machin"]
