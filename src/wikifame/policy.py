from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

BOT_NAME_PATTERN = re.compile(r"bot$", re.IGNORECASE)

GlobalGroupLookup = Callable[[str], frozenset[str]]


@dataclass(frozen=True)
class ResolvedUser:
    user_id: int
    username: str
    groups: frozenset[str]
    missing: bool = False


def is_countable_token(value: str) -> bool:
    """Count words/numbers, while ignoring pure whitespace and wikitext punctuation."""
    return any(character.isalnum() for character in value)


def is_registered_editor(editor: str) -> bool:
    return editor.isdigit() and int(editor) > 0


def has_bot_name(username: str) -> bool:
    """Return whether a username ends in "bot", in any case.

    This is a guess about who an account belongs to, not a measurement of what it did,
    and it is wrong for every human whose pseudonym happens to end that way — Talbot,
    Abbot, Thibot. ADR-0006 records that cost being accepted deliberately: bot operators
    on Wikimedia wikis are required to name their accounts this way, so the rule catches
    unflagged and retired bots that no group membership identifies.
    """
    return BOT_NAME_PATTERN.search(username) is not None


def is_bot_group(group: str) -> bool:
    """Match any group whose name contains "bot", local or global.

    CentralAuth spells the same idea several ways (`bot`, `local-bot`, `global-bot`) and
    may add another. Matching the substring keeps a new spelling from silently making
    bots nameable again, at the cost of matching a hypothetical future group that
    contains "bot" without meaning one.
    """
    return "bot" in group.lower()


def should_highlight_contributor(
    user: ResolvedUser,
    global_groups: GlobalGroupLookup | None = None,
) -> bool:
    """Return whether an editor may appear among the three highlighted accounts.

    ADR-0001 excludes missing users, bots, and temporary accounts; ADR-0006 widens "bot"
    beyond the local flag. Checks run cheapest-first, and `global_groups` is consulted
    only for an account nothing else has already excluded, because it costs one HTTP
    request per account.

    Passing no lookup skips the CentralAuth check rather than failing: a caller with no
    client still gets the local rule. Both rankings pass one.

    Any change to this rule requires a new algorithm version so that cached results
    cannot mix two attribution policies.
    """
    if user.missing or user.username.startswith("~"):
        return False
    if any(is_bot_group(group) for group in user.groups):
        return False
    if has_bot_name(user.username):
        return False
    if global_groups is not None and any(
        is_bot_group(group) for group in global_groups(user.username)
    ):
        return False
    return True
