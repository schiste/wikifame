from collections import Counter

import pytest

from wikifame.attribution import (
    candidate_user_ids,
    count_tokens,
    select_contributors,
    select_top_editors,
)
from wikifame.policy import ResolvedUser, should_highlight_contributor


def test_count_tokens_ignores_markup_and_malformed_tokens() -> None:
    counts, total = count_tokens(
        [
            {"str": "Bonjour", "editor": "10"},
            {"str": "{{", "editor": "20"},
            {"str": "2026", "editor": "10"},
            {"str": " ", "editor": "10"},
            {"str": "sans-auteur"},
        ]
    )

    assert counts == Counter({"10": 2})
    assert total == 2


def test_candidates_only_include_registered_numeric_ids() -> None:
    counts = Counter({"0|192.0.2.1": 100, "~2026-1": 90, "12": 80, "34": 70})

    assert candidate_user_ids(counts, 10) == [12, 34]


def test_select_contributors_filters_bots_temporary_and_small_shares() -> None:
    counts = Counter({"1": 500, "2": 250, "3": 150, "4": 90, "5": 10})
    users = {
        1: ResolvedUser(1, "Alice", frozenset()),
        2: ResolvedUser(2, "Bot Exemple", frozenset({"bot"})),
        3: ResolvedUser(3, "~2026-123", frozenset()),
        4: ResolvedUser(4, "Bob", frozenset()),
        5: ResolvedUser(5, "Charlie", frozenset()),
    }

    result = select_contributors(
        counts,
        total_tokens=1000,
        users=users,
        minimum_tokens=20,
        minimum_share=0.01,
    )

    assert [item["username"] for item in result] == ["Alice", "Bob"]
    assert result[0]["share"] == 0.5


def test_temporary_accounts_can_never_be_highlighted() -> None:
    temporary_user = ResolvedUser(123, "~2026-12345", frozenset())

    assert should_highlight_contributor(temporary_user) is False


@pytest.mark.parametrize(
    ("username", "nameable"),
    [
        ("Gallicbot", False),
        ("Luckas-bot", False),
        ("EmausBot", False),
        ("Talbot", False),  # ADR-0006: a person excluded by their pseudonym, knowingly.
        ("Robotique", True),
        ("Alice", True),
    ],
)
def test_a_name_ending_in_bot_is_enough_to_exclude_an_account(
    username: str, nameable: bool
) -> None:
    user = ResolvedUser(1, username, frozenset())

    assert should_highlight_contributor(user, lambda _name: frozenset()) is nameable


def test_a_globally_flagged_bot_is_excluded_although_the_wiki_flags_nothing() -> None:
    """Addbot's case: `list=users` returns an ordinary account, CentralAuth knows better."""
    addbot = ResolvedUser(1, "Addbot", frozenset({"user", "autoconfirmed"}))
    unflagged_spelling = ResolvedUser(2, "Loveless", frozenset({"user"}))
    global_groups = {"Addbot": frozenset({"local-bot"}), "Loveless": frozenset({"global-bot"})}

    assert should_highlight_contributor(addbot, global_groups.__getitem__) is False
    assert should_highlight_contributor(unflagged_spelling, global_groups.__getitem__) is False


def test_the_global_lookup_is_skipped_for_accounts_already_excluded() -> None:
    """One HTTP request per account, so an account excluded for free must stay free."""
    asked: list[str] = []

    def global_groups(username: str) -> frozenset[str]:
        asked.append(username)
        return frozenset()

    for user in (
        ResolvedUser(1, "Botrix", frozenset({"bot"})),
        ResolvedUser(2, "~2026-1", frozenset()),
        ResolvedUser(3, "Gallicbot", frozenset()),
        ResolvedUser(4, "Ghost", frozenset(), missing=True),
    ):
        assert should_highlight_contributor(user, global_groups) is False

    assert asked == []


def test_both_rankings_exclude_the_same_bot_for_the_same_reason() -> None:
    """The two metrics may disagree on order; they may not disagree on who is a person."""
    users = {
        1: ResolvedUser(1, "Addbot", frozenset()),
        2: ResolvedUser(2, "Gallicbot", frozenset()),
        3: ResolvedUser(3, "Alice", frozenset()),
    }
    global_groups = {
        "Addbot": frozenset({"local-bot"}),
        "Gallicbot": frozenset(),
        "Alice": frozenset(),
    }

    by_tokens = select_contributors(
        Counter({"1": 500, "2": 300, "3": 200}),
        total_tokens=1000,
        users=users,
        minimum_tokens=20,
        minimum_share=0.01,
        global_groups=global_groups.__getitem__,
    )
    by_edits = select_top_editors(
        Counter({1: 50, 2: 30, 3: 20}),
        total_revisions=100,
        users=users,
        global_groups=global_groups.__getitem__,
    )

    assert [item["username"] for item in by_tokens] == ["Alice"]
    assert [item["username"] for item in by_edits] == ["Alice"]


def test_an_account_below_the_share_gate_is_never_looked_up() -> None:
    """The gate runs first, so the token path pays nothing for accounts it cannot name."""
    asked: list[str] = []

    def global_groups(username: str) -> frozenset[str]:
        asked.append(username)
        return frozenset()

    result = select_contributors(
        Counter({"1": 900, "2": 5}),
        total_tokens=1000,
        users={1: ResolvedUser(1, "Alice", frozenset()), 2: ResolvedUser(2, "Bob", frozenset())},
        minimum_tokens=20,
        minimum_share=0.01,
        global_groups=global_groups,
    )

    assert [item["username"] for item in result] == ["Alice"]
    assert asked == ["Alice"]
