from pathlib import Path

GADGET_SOURCE = (Path(__file__).parents[1] / "wikifame.js").read_text()


def test_production_gadget_contains_no_page_fixture() -> None:
    assert "CONTRIBUTION_FIXTURES" not in GADGET_SOURCE
    assert "Victor Hugo" not in GADGET_SOURCE
    assert "Jean de la Fontaine" not in GADGET_SOURCE
    assert "ContributeursHumains" not in GADGET_SOURCE
    assert "contributeurs-humains" not in GADGET_SOURCE


def test_pending_attribution_is_retried_without_becoming_an_error() -> None:
    assert "data.status !== 'pending'" in GADGET_SOURCE
    assert "PENDING_RETRY_DELAYS_MS" in GADGET_SOURCE
    assert "Attribution en cours de calcul." not in GADGET_SOURCE


def test_gadget_uses_page_freshness_api_and_bounded_session_cache() -> None:
    assert "'/v2/'" in GADGET_SOURCE
    assert "CLIENT_CACHE_MAX_AGE_MS" in GADGET_SOURCE
    assert "computed_at" in GADGET_SOURCE
    cache_key_body = GADGET_SOURCE.split("function getCacheKey()", 1)[1].split(
        "function readCache", 1
    )[0]
    assert "wgCurRevisionId" not in cache_key_body


def test_gadget_is_wiki_agnostic() -> None:
    """The same file must ship unchanged on every Wikipedia."""
    assert "wgDBname" in GADGET_SOURCE
    assert "frwiki" not in GADGET_SOURCE
    assert "fr.wikipedia.org" not in GADGET_SOURCE
    # The session cache must not leak one wiki's attribution into another.
    cache_key_body = GADGET_SOURCE.split("function getCacheKey()", 1)[1].split(
        "function readCache", 1
    )[0]
    assert "wgDBname" in cache_key_body


def test_gadget_hardcodes_no_wiki_specific_page_titles() -> None:
    """Help and sandbox titles differ per wiki, so they come from local config."""
    assert "CONFIG_PAGE_SUFFIX" in GADGET_SOURCE
    assert "'/wikifame-config.json'" in GADGET_SOURCE
    assert "Bac à sable" not in GADGET_SOURCE
    assert "Aide:Comment modifier une page" not in GADGET_SOURCE
    assert "'Utilisateur:'" not in GADGET_SOURCE


def test_config_page_lives_beside_the_script_in_the_readers_own_user_space() -> None:
    """A personal script must not require interface-admin rights to configure."""
    body = GADGET_SOURCE.split("function configPage()", 1)[1].split("\n\t}", 1)[0]
    assert "wgUserName" in body
    # Namespace 2 by number, so the localised user-namespace name resolves per wiki.
    assert "CONFIG_PAGE_SUFFIX, 2" in body
    assert "'User:'" not in GADGET_SOURCE
    # The MediaWiki-namespace page is the future gadget location, a comment only.
    code = [line for line in GADGET_SOURCE.splitlines() if not line.lstrip().startswith("*")]
    assert not [line for line in code if "MediaWiki:" in line]


def test_gadget_localises_plurals_and_lists_rather_than_hardcoding_french() -> None:
    assert "PLURAL:" in GADGET_SOURCE
    assert "Intl.ListFormat" in GADGET_SOURCE
    assert "wgUserLanguage" in GADGET_SOURCE
    # An unparsable MediaWiki language code must not break rendering.
    assert "safeFormatter" in GADGET_SOURCE
