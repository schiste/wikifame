from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from wikifame.policy import (
    ResolvedUser,
    is_countable_token,
    is_registered_editor,
    should_highlight_contributor,
)


def count_tokens(tokens: Iterable[Mapping[str, Any]]) -> tuple[Counter[str], int]:
    counts: Counter[str] = Counter()
    total = 0
    for token in tokens:
        value = token.get("str")
        editor = token.get("editor")
        if not isinstance(value, str) or not isinstance(editor, str):
            continue
        if not is_countable_token(value):
            continue
        counts[editor] += 1
        total += 1
    return counts, total


def candidate_user_ids(counts: Counter[str], limit: int) -> list[int]:
    return [int(editor) for editor, _count in counts.most_common() if is_registered_editor(editor)][
        :limit
    ]


def select_contributors(
    counts: Counter[str],
    total_tokens: int,
    users: Mapping[int, ResolvedUser],
    minimum_tokens: int,
    minimum_share: float,
    limit: int = 3,
) -> list[dict[str, Any]]:
    contributors: list[dict[str, Any]] = []
    if total_tokens <= 0:
        return contributors

    for editor, token_count in counts.most_common():
        if not is_registered_editor(editor):
            continue
        user = users.get(int(editor))
        if user is None or not should_highlight_contributor(user):
            continue
        share = token_count / total_tokens
        if token_count < minimum_tokens or share < minimum_share:
            continue
        contributors.append(
            {
                "user_id": user.user_id,
                "username": user.username,
                "token_count": token_count,
                "share": round(share, 4),
            }
        )
        if len(contributors) == limit:
            break
    return contributors
