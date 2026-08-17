import re
from pathlib import Path

GADGET_PATH = Path(__file__).parents[1] / "wikifame.js"
GADGET_SOURCE = GADGET_PATH.read_text()
GADGET_STYLES = (Path(__file__).parents[1] / "wikifame.css").read_text()


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


def test_every_config_value_the_gadget_reads_is_actually_requested() -> None:
    """mw.config.get returns only what it was asked for; a missing name is undefined.

    configPage() reads wgUserName, so leaving it out of the request list silently
    disables the configuration page for everyone instead of failing loudly.
    """
    requested = set(re.findall(r"'(wg\w+)'", GADGET_SOURCE.split("mw.config.get( [", 1)[1]))
    used = set(re.findall(r"config\.(wg\w+)", GADGET_SOURCE))

    assert used - requested == set()


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


def test_custom_content_is_parsed_by_mediawiki_rather_than_built_here() -> None:
    """Wikitext gives images and video for free, and the parser does the sanitising.

    The gadget must never assemble markup from a configuration string: that is the
    difference between rich content and an injection point.
    """
    body = GADGET_SOURCE.split("function renderCustomContent(", 1)[1].split("\n\t}", 1)[0]
    assert "DOMParser" in body
    assert "innerHTML" not in GADGET_SOURCE
    # Wikitext cannot emit one, but the guarantee should survive reading this function
    # alone rather than the whole parser pipeline.
    assert "querySelectorAll( 'script' )" in body
    # Media in a note box loads when reached and never plays by itself.
    assert "'loading', 'lazy'" in body
    assert "removeAttribute( 'autoplay' )" in body


def test_custom_content_is_fetched_anonymously_and_falls_back_to_built_in_wording() -> None:
    fetch = GADGET_SOURCE.split("async function fetchParsedPage(", 1)[1].split("\n\t}", 1)[0]
    assert "action=parse" in fetch
    # Anonymous keeps the response CDN-cacheable and reader-independent.
    assert "credentials: 'omit'" in fetch
    assert "return null" in fetch

    load = GADGET_SOURCE.split("async function loadCustomContent(", 1)[1].split("\n\t}", 1)[0]
    # A configured but unwritten page must not cost a lookup on every history view.
    assert "writeCache( cacheKey, { html: html } )" in load

    intro = GADGET_SOURCE.split("async function addHistoryIntroduction(", 1)[1].split(
        "\n\tasync function", 1
    )[0]
    assert "if ( custom ) {" in intro
    assert "} else {" in intro
    # The edit link is built here in every case: a page parsed on its own cannot know
    # which article the reader is on, so wikitext magic words would name the wrong page.
    assert "createEditLink()" in intro.split("} else {", 1)[1].split("\n\t\t}", 1)[1]


def test_translations_live_on_language_subpages() -> None:
    """One reviewable page per language, unlike the language-blind messages object."""
    body = GADGET_SOURCE.split("function contentCandidates(", 1)[1].split("\n\t}", 1)[0]
    assert "base + '/' + language" in body
    assert "language.split( '-' )[ 0 ]" in body
    # The base title is the last resort, not the first choice.
    assert body.index("candidates.push( base )") > body.index("base + '/' + language")


def test_javascript_extension_uses_hooks_instead_of_code_in_configuration() -> None:
    """Config pages stay declarative; arbitrary JS belongs in the reader's common.js."""
    assert "mw.hook( 'wikifame.history' ).fire(" in GADGET_SOURCE
    assert "mw.hook( 'wikifame.summary' ).fire(" in GADGET_SOURCE
    assert "eval(" not in GADGET_SOURCE
    assert "new Function" not in GADGET_SOURCE


def test_both_views_share_one_cached_attribution_request() -> None:
    """Reading an article and then its history must not cost two API calls."""
    summary = GADGET_SOURCE.split("async function addArticleSummary(", 1)[1].split(
        "\n\tasync function", 1
    )[0]
    assert "contributionData( PENDING_RETRY_DELAYS_MS )" in summary
    # Caching belongs to the shared helper now, not to one of the two callers.
    assert "readCache" not in summary
    assert "writeCache" not in summary

    shared = GADGET_SOURCE.split("async function contributionData(", 1)[1].split("\n\t}", 1)[0]
    assert "readCache( cacheKey, CLIENT_CACHE_MAX_AGE_MS )" in shared
    assert "writeCache( cacheKey, data )" in shared


def test_source_holds_no_control_characters() -> None:
    """A literal NUL made grep treat the file as binary and silently find nothing.

    The list-formatter sentinel is still a NUL at runtime; it is spelled as an escape
    so the source stays plain text and ordinary tooling keeps working.
    """
    assert "\x00" not in GADGET_PATH.read_bytes().decode()
    assert "'\\u0000' + index" in GADGET_SOURCE


def test_slow_requests_alone_get_a_placeholder() -> None:
    """A warm response beats the threshold, so the usual reader waits on nothing.

    Showing a spinner for a 150 ms answer manufactures a wait that does not exist,
    which is why the state machine starts at 'hidden' rather than at 'rolling'.
    """
    body = GADGET_SOURCE.split("function countDisplayState(", 1)[1].split("\n\t}", 1)[0]
    assert "COUNT_ROLL_START_MS" in body
    assert "'hidden'" in body
    assert "'rolling'" in body
    assert "'vague'" in body
    assert "'final'" in body
    # A result that is already in hand outranks every timer.
    assert body.index("'final'") < body.index("'hidden'")

    summary = GADGET_SOURCE.split("async function addArticleSummary(", 1)[1].split(
        "\n\tasync function", 1
    )[0]
    # The request is in flight before anything is drawn, never after.
    assert summary.index("contributionData( PENDING_RETRY_DELAYS_MS )") < summary.index(
        "runCountPlaceholder("
    )

    # 'hidden' outlasts the start threshold under reduced motion, so the wait has to
    # target whichever threshold is still ahead. Subtracting from the first one goes
    # negative and turns the driver's loop into a busy wait.
    driver = GADGET_SOURCE.split("async function runCountPlaceholder(", 1)[1].split(
        "\n\t}", 1
    )[0]
    assert "hiddenWaitMs(" in driver
    assert "COUNT_ROLL_START_MS - ( Date.now() - startedAt )" not in driver


def test_turning_digits_are_never_announced_or_left_spinning() -> None:
    """One digit per frame would make a screen reader unusable.

    The box is hidden while it turns and exposed only once it says something a reader
    can act on, and it stops turning long before the thirteen-second retry chain ends.
    """
    pending = GADGET_SOURCE.split("function buildPendingSummary(", 1)[1].split("\n\t}", 1)[0]
    assert "'aria-busy', 'true'" in pending
    assert "'aria-hidden', 'true'" in pending

    settle = GADGET_SOURCE.split("function settlePendingSummary(", 1)[1].split("\n\t}", 1)[0]
    assert "removeAttribute( 'aria-busy' )" in settle
    assert "removeAttribute( 'aria-hidden' )" in settle
    # Vague, but a complete and true sentence rather than a truncated one.
    assert "'wikifame-many-people'" in settle
    assert "'wikifame-summary-prefix'" in settle

    assert "prefersReducedMotion()" in GADGET_SOURCE
    assert "'(prefers-reduced-motion: reduce)'" in GADGET_SOURCE
    assert "animation: none" in GADGET_STYLES
    assert "prefers-reduced-motion: reduce" in GADGET_STYLES


def test_placeholder_reserves_its_width_so_the_sentence_cannot_jitter() -> None:
    """Digit widths differ, and the count sits mid-sentence with text beside it."""
    assert "font-variant-numeric: tabular-nums" in GADGET_STYLES
    assert "min-width: 4ch" in GADGET_STYLES


def test_a_failed_request_is_told_apart_from_one_still_computing() -> None:
    """An unsupported wiki must leave no trace, not claim that many people wrote it.

    Both paths yield no data, so collapsing them would leave the vague wording on a
    404 — an assertion about an article the API never agreed to serve.
    """
    summary = GADGET_SOURCE.split("async function addArticleSummary(", 1)[1].split(
        "\n\tasync function", 1
    )[0]
    assert "outcome.failed" in summary
    assert "removeArticleSummary()" in summary
    # A pending result keeps whatever the placeholder settled on.
    assert summary.index("if ( outcome.failed ) {") < summary.index("if ( !outcome.data ) {")
    # The real sentence replaces the placeholder in place rather than joining it.
    assert "existing.replaceWith( summary )" in summary
    # The hook still carries real data, so it must not fire for a placeholder.
    fired = summary.split("mw.hook( 'wikifame.summary' ).fire(", 1)[1]
    assert "outcome.data" in fired.split("\n", 1)[0]


def test_gadget_localises_plurals_and_lists_rather_than_hardcoding_french() -> None:
    assert "PLURAL:" in GADGET_SOURCE
    assert "Intl.ListFormat" in GADGET_SOURCE
    assert "wgUserLanguage" in GADGET_SOURCE
    # An unparsable MediaWiki language code must not break rendering.
    assert "safeFormatter" in GADGET_SOURCE
