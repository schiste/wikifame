"""Which wikis WikiFame can serve, and how to reach them.

WikiWho publishes one API per Wikipedia language edition. Every language code it
covers is dash-free, so a Wikimedia database name maps to a host by simple suffix
stripping: ``frwiki`` -> ``fr`` -> ``fr.wikipedia.org``.

That single rule also answers the capability question without any network call.
Non-Wikipedia projects either fail the suffix test (``frwikisource``) or resolve to
a language code WikiWho does not publish (``commonswiki`` -> ``commons``,
``wikidatawiki`` -> ``wikidata``). Keeping this purely syntactic is what lets the
FastAPI process validate a wiki without ever contacting an upstream service.

If WikiWho ever adds a dashed code such as ``be-tarask``, or extends beyond
Wikipedia, this module is where a real sitematrix lookup would replace the rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from wikifame.errors import PermanentDataError

WIKI_SUFFIX = "wiki"
ALL_WIKIS = "*"

#: Wikipedia language editions published by WikiWho, as observed on
#: https://wikiwho.wmcloud.org/. Override with WIKIWHO_LANGUAGES when the service
#: gains or drops an edition, so coverage changes do not require a code release.
DEFAULT_WIKIWHO_LANGUAGES: frozenset[str] = frozenset(
    {
        "af", "als", "ar", "az", "be", "bg", "bn", "bs", "ce", "cs",
        "cy", "da", "de", "dsb", "el", "en", "eo", "es", "et", "eu",
        "fa", "fi", "fr", "gl", "he", "hi", "hr", "hu", "ia", "id",
        "it", "ja", "ka", "kk", "ko", "ku", "lt", "lv", "mk", "ml",
        "mr", "ms", "mt", "ne", "nl", "no", "pl", "pt", "ro", "ru",
        "sh", "simple", "sk", "sl", "sq", "sr", "sv", "sw", "ta", "te",
        "tg", "th", "tl", "tr", "uk", "ur", "uz", "vec", "vi", "zh",
    }
)  # fmt: skip


@dataclass(frozen=True)
class SiteResolver:
    """Resolves a Wikimedia database name to a WikiWho language and a host."""

    languages: frozenset[str] = DEFAULT_WIKIWHO_LANGUAGES

    def language(self, wiki: str) -> str | None:
        """Return the WikiWho language code for a wiki, or None when uncovered."""
        if not wiki.endswith(WIKI_SUFFIX):
            return None
        code = wiki[: -len(WIKI_SUFFIX)]
        return code if code in self.languages else None

    def is_capable(self, wiki: str) -> bool:
        """Return whether WikiWho publishes provenance data for this wiki."""
        return self.language(wiki) is not None

    def is_enabled(self, wiki: str, supported_wikis: tuple[str, ...]) -> bool:
        """Return whether this deployment serves the wiki.

        Capability and enablement are deliberately separate. ``*`` opts into every
        wiki WikiWho covers; an explicit list narrows that further. Enabling a wiki
        WikiWho cannot analyse is always meaningless, so capability wins either way.
        """
        if not self.is_capable(wiki):
            return False
        return ALL_WIKIS in supported_wikis or wiki in supported_wikis

    def require_language(self, wiki: str) -> str:
        code = self.language(wiki)
        if code is None:
            raise PermanentDataError(f"WikiWho ne prend pas en charge {wiki}")
        return code

    def host(self, wiki: str) -> str:
        return f"{self.require_language(wiki)}.wikipedia.org"
