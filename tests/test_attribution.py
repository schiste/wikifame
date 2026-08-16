from collections import Counter

from wikifame.attribution import candidate_user_ids, count_tokens, select_contributors
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
