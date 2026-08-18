from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

BOT_NAME_PATTERN = re.compile(r"bot$", re.IGNORECASE)

# Stewards write lock reasons as free text, but the sanctioning ones are formulaic:
# "long-term abuse", "spam-only account", "lock evasion", "cross-wiki abuse",
# "globally or WMF banned user". None of the courtesy reasons share this vocabulary.
LOCK_SANCTION_PATTERN = re.compile(
    r"abuse|abusiv|\bspam|\blta\b|lock evasion|block evasion|\bbanned\b|\bban\b"
    r"|sock|vandal|harass|phishing|troll|\bufa\b|unauthori[sz]ed bot",
    re.IGNORECASE,
)

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
    # Kept as a fourth "if ...: return False" rather than inlined into the return, so the
    # exclusion criteria read as one list. Adding a fifth should mean copying a line.
    if global_groups is not None and any(  # noqa: SIM103
        is_bot_group(group) for group in global_groups(user.username)
    ):
        return False
    return True


@dataclass(frozen=True)
class AccountStanding:
    """What a wiki has formally decided about an account, at the moment it was read.

    Deliberately separate from `ResolvedUser`. That one describes what an account *is*
    and is consumed while a result is being computed; this one describes what has been
    *decided about it*, changes without anyone touching the article, and is consumed
    while a response is being built. Keeping them apart is what stops a sanction from
    being baked into a stored row (ADR-0009).

    `blocked_at` is None when no block is active. `block_expires_at` is None for an
    indefinite block, which is why the two fields cannot be collapsed into a duration:
    "no block" and "a block that never ends" are opposite answers.
    """

    user_id: int
    username: str
    blocked_at: datetime | None = None
    block_expires_at: datetime | None = None
    block_partial: bool = False
    globally_locked: bool = False
    lock_reason: str | None = None


def is_sanction_lock(standing: AccountStanding) -> bool:
    """Return whether a global lock was imposed as a sanction rather than as a courtesy.

    CentralAuth has one lock mechanism and uses it for opposite purposes. Stewards lock
    abusers, and they also lock the accounts of editors who have died, who have exercised
    the right to vanish, or whose account was compromised. Reading the flag alone treats
    a memorial as a ban: the first production run of ADR-0009 withheld the credit of five
    deceased Wikipedians, which is the exact opposite of what the rule is for.

    So the flag is not enough and the reason decides. A lock withholds a name only when
    its reason affirmatively reads as a sanction; an unreadable, missing or unfamiliar
    reason leaves the account nameable. That is the same principle the rest of this
    module runs on — absence of data is not a finding — and it fails the safe way: the
    cost of missing a sanction is a name that stays up, while the cost of guessing wrong
    in the other direction is erasing someone who did nothing wrong.
    """
    if not standing.globally_locked:
        return False
    if not standing.lock_reason:
        return False
    return LOCK_SANCTION_PATTERN.search(standing.lock_reason) is not None


def is_nameable_account(
    standing: AccountStanding | None,
    max_visible_block_seconds: int,
    now: datetime,
) -> bool:
    """Return whether an account may still be named among an article's contributors.

    The rule is about lasting exclusion from the community, not about sanction as such.
    A week's block is a normal editorial event and says nothing about the text the
    account wrote; being locked or blocked for years is the wiki saying this person is
    not one of its editors any more, and a credit line is a poor place to learn that.

    Evaluated when the response is built, never when the result is computed, so the
    threshold can be changed or the whole rule switched off without recomputing
    anything. See ADR-0009.

    An unknown account is nameable. Absence from the standing table means nobody has
    looked yet, not that a lookup came back clean — and a sync that never ran must not
    silently blank the names off a wiki. Same reasoning as the opt-out list: an empty
    answer is an instruction only when it is an answer.
    """
    if standing is None:
        return True
    if is_sanction_lock(standing):
        # A lock imposed as a sanction has no expiry to compare against; it is the
        # strongest statement the movement makes about an account, and it is global, so
        # no local threshold applies to it.
        return False
    if standing.blocked_at is None:
        return True
    if standing.block_partial:
        # A block scoped to a page or a namespace is a targeted editorial remedy, not a
        # ban. Someone barred from one article may be a respected contributor elsewhere.
        return True
    if standing.block_expires_at is None:
        return False
    if standing.block_expires_at <= now:
        # The row outlived the block it describes. MediaWiki keeps no trace of an expired
        # block, so the next sync will drop it; until then, treat it as already gone
        # rather than serving a sanction the wiki has stopped applying.
        return True
    duration = standing.block_expires_at - standing.blocked_at
    return duration.total_seconds() <= max_visible_block_seconds
