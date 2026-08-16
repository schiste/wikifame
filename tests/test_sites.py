import pytest

from wikifame.errors import PermanentDataError
from wikifame.sites import ALL_WIKIS, SiteResolver

RESOLVER = SiteResolver()


@pytest.mark.parametrize(
    ("wiki", "language"),
    [
        ("frwiki", "fr"),
        ("enwiki", "en"),
        ("simplewiki", "simple"),
        ("alswiki", "als"),
        ("dsbwiki", "dsb"),
        ("nowiki", "no"),
    ],
)
def test_wikipedia_database_names_resolve_to_wikiwho_languages(wiki: str, language: str) -> None:
    assert RESOLVER.language(wiki) == language
    assert RESOLVER.host(wiki) == f"{language}.wikipedia.org"


@pytest.mark.parametrize(
    "wiki",
    [
        "commonswiki",  # ends in "wiki" but "commons" is not a WikiWho language
        "wikidatawiki",
        "metawiki",
        "frwikisource",  # not a Wikipedia at all
        "frwiktionary",
        "be_x_oldwiki",  # WikiWho publishes "be", not this legacy database name
        "zh_yuewiki",
        "",
        "wiki",
    ],
)
def test_uncovered_wikis_are_rejected_without_a_network_call(wiki: str) -> None:
    assert RESOLVER.language(wiki) is None
    assert RESOLVER.is_capable(wiki) is False
    with pytest.raises(PermanentDataError):
        RESOLVER.host(wiki)


def test_wildcard_enables_every_capable_wiki_but_never_an_incapable_one() -> None:
    supported = (ALL_WIKIS,)

    assert RESOLVER.is_enabled("dewiki", supported) is True
    assert RESOLVER.is_enabled("jawiki", supported) is True
    assert RESOLVER.is_enabled("commonswiki", supported) is False
    assert RESOLVER.is_enabled("wikidatawiki", supported) is False


def test_explicit_allowlist_narrows_serving() -> None:
    supported = ("frwiki", "enwiki")

    assert RESOLVER.is_enabled("frwiki", supported) is True
    assert RESOLVER.is_enabled("dewiki", supported) is False


def test_capability_wins_over_an_operator_typo() -> None:
    """Listing a wiki WikiWho cannot analyse must not enable it."""
    assert RESOLVER.is_enabled("commonswiki", ("commonswiki",)) is False


def test_language_coverage_is_overridable_without_a_code_change() -> None:
    resolver = SiteResolver(frozenset({"fr"}))

    assert resolver.is_capable("frwiki") is True
    assert resolver.is_capable("dewiki") is False
