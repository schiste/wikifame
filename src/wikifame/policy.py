from __future__ import annotations

from dataclasses import dataclass


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


def should_highlight_contributor(user: ResolvedUser) -> bool:
    """Return whether an editor may appear among the three highlighted accounts.

    Product decision ADR-0001 excludes missing users, current bots, and temporary
    accounts. Any change to this rule requires a new algorithm version so that cached
    results cannot mix two attribution policies.
    """
    return not user.missing and "bot" not in user.groups and not user.username.startswith("~")
