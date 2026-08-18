"""Materialise the on-wiki opt-out list into rows the API can read.

An opt-out the API ignores is not an opt-out: enforcing it in the gadget would leave the
names one direct request away. So the decision has to be made where the answer is built,
and the answer is built somewhere that is not allowed to call MediaWiki. This job is the
bridge — it reads the list on a schedule and leaves behind a table of page IDs.

The list itself is an ordinary wiki page, because the people who maintain it are wiki
editors and the page needs a history, a watchlist and a talk page more than it needs a
schema. Only bulleted links are read; headings, prose, notes and links to the discussion
that produced an entry cost nothing.
"""

from __future__ import annotations

import argparse
import logging
import re

from wikipeople.clients import MediaWikiClient
from wikipeople.errors import WikiPeopleError
from wikipeople.repository import OptOutEntry
from wikipeople.runtime import Runtime, build_runtime

LOGGER = logging.getLogger(__name__)

ARTICLE_NAMESPACE = 0
CATEGORY_NAMESPACE = 14

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A bullet, then the first wiki link on that line. `[^]|#]` stops the target at the
# closing bracket, a display-text pipe, or a section anchor.
_BULLET_LINK = re.compile(r"^\s*\*+\s*\[\[\s*([^\]|#\n]*)")


def parse_optout_page(wikitext: str) -> list[str]:
    """Return the link targets listed as bullets, deduplicated, in page order.

    Deliberately forgiving about everything except the shape of an entry. A maintainer
    should be able to explain why a page is listed, group entries under headings, and
    leave a note for the next person, without any of it being mistaken for an entry. The
    rule they have to remember is one sentence: a bullet, then a link.
    """
    targets: list[str] = []
    seen: set[str] = set()
    for line in _COMMENT.sub("", wikitext).splitlines():
        match = _BULLET_LINK.match(line)
        if match is None:
            continue
        # A leading colon is how a category is linked rather than joined; strip it so
        # the title is the one MediaWiki will recognise.
        target = match.group(1).strip().lstrip(":").strip().replace("_", " ")
        if target and target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


def collect_entries(
    mediawiki: MediaWikiClient,
    wiki: str,
    targets: list[str],
    category_limit: int,
) -> tuple[list[OptOutEntry], list[str]]:
    """Turn link targets into the article IDs they cover, and a list of what was dropped.

    Articles are resolved before categories so that a page listed in its own right keeps
    that as its recorded reason even when a category also covers it.
    """
    entries: dict[int, OptOutEntry] = {}
    skipped: list[str] = []
    infos = mediawiki.classify_titles(wiki, targets) if targets else []

    for info in infos:
        if info.namespace != ARTICLE_NAMESPACE:
            continue
        if info.page_id is None:
            skipped.append(f"{info.title} (page inexistante)")
            continue
        entries[info.page_id] = OptOutEntry(page_id=info.page_id, title=info.title, source="page")

    for info in infos:
        if info.namespace == ARTICLE_NAMESPACE:
            continue
        if info.namespace != CATEGORY_NAMESPACE:
            skipped.append(f"{info.title} (espace de noms {info.namespace}, ignoré)")
            continue
        members, truncated = mediawiki.category_members(wiki, info.title, category_limit)
        if truncated:
            skipped.append(f"{info.title} (plus de {category_limit} articles, liste tronquée)")
        for member in members:
            entries.setdefault(
                member.page_id,
                OptOutEntry(
                    page_id=member.page_id,
                    title=member.title,
                    source=f"category:{info.title}",
                ),
            )

    return list(entries.values()), skipped


def sync_wiki(
    runtime: Runtime,
    mediawiki: MediaWikiClient,
    wiki: str,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Bring one wiki's stored opt-out list in line with its on-wiki page.

    Returns (covered, added, removed). Raises if MediaWiki cannot be reached, which
    leaves the stored list exactly as it was: a network failure must not read as
    "everybody withdrew their opt-out".
    """
    settings = runtime.settings
    wikitext = mediawiki.get_wikitext(wiki, settings.optout_page)
    if wikitext is None:
        # No page at all is a real state and means nobody has opted out here. It is only
        # reachable because the Action API answered; a failure raised above.
        LOGGER.info("%s: no %s page", wiki, settings.optout_page)
        wikitext = ""

    targets = parse_optout_page(wikitext)
    entries, skipped = collect_entries(mediawiki, wiki, targets, settings.optout_category_limit)
    for note in skipped:
        LOGGER.warning("%s: %s", wiki, note)

    if dry_run:
        LOGGER.info("%s: %s entries would cover %s articles", wiki, len(targets), len(entries))
        return len(entries), 0, 0

    added, removed = runtime.repository.replace_optout(wiki, entries)
    LOGGER.info(
        "%s: %s list entries cover %s articles (+%s, -%s)",
        wiki,
        len(targets),
        len(entries),
        added,
        removed,
    )
    return len(entries), added, removed


def resolve_target_wikis(runtime: Runtime, explicit: str | None) -> list[str]:
    """Wikis worth reading a list for: the ones that have produced a result.

    A wiki nobody has been served an attribution for has nothing to opt out of, and
    reading a page on all seventy WikiWho wikis every quarter of an hour to discover
    that would be traffic spent on nothing.
    """
    if explicit:
        return [explicit]
    return [wiki for wiki in runtime.repository.active_wikis() if runtime.resolver.is_capable(wiki)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the on-wiki WikiPeople opt-out lists")
    parser.add_argument("--wiki", default=None, help="Sync one wiki instead of every active one")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what the list covers without changing what is served",
    )
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    runtime = build_runtime()
    runtime.database.create_schema()
    wikis = resolve_target_wikis(runtime, args.wiki)
    if not wikis:
        LOGGER.info("no active wiki to read an opt-out list for yet")
        return

    mediawiki = MediaWikiClient(
        runtime.settings.user_agent, runtime.settings.request_timeout_seconds, runtime.resolver
    )
    try:
        covered = 0
        for wiki in wikis:
            try:
                covered += sync_wiki(runtime, mediawiki, wiki, args.dry_run)[0]
            except WikiPeopleError as error:
                # One unreachable wiki keeps its previous list rather than losing it.
                LOGGER.warning("%s: opt-out sync skipped, list unchanged (%s)", wiki, error)
        LOGGER.info("%s articles opted out across %s wikis", covered, len(wikis))
    finally:
        mediawiki.close()


if __name__ == "__main__":
    main()
