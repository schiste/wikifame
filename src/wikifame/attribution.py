from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from wikifame.policy import (
    GlobalGroupLookup,
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


def select_top_editors(
    counts: Counter[int],
    total_revisions: int,
    users: Mapping[int, ResolvedUser],
    global_groups: GlobalGroupLookup | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Rank accounts by how often they edited the page, for pages tokens cannot rank.

    The exclusion rule is not reimplemented here: `should_highlight_contributor` is the
    same function the token path calls, so bots, temporary accounts and missing users
    disappear from both rankings for the same reason and at the same time. Only the
    ranking differs, which is exactly what the two metrics disagree about.

    There is no minimum-share gate, unlike the token path. A share of the edits is not a
    share of the text, so reusing the 1% figure would import a threshold that was chosen
    against a different measurement.
    """
    editors: list[dict[str, Any]] = []
    if total_revisions <= 0:
        return editors

    for user_id, edit_count in counts.most_common():
        user = users.get(user_id)
        if user is None or not should_highlight_contributor(user, global_groups):
            continue
        editors.append(
            {
                "user_id": user.user_id,
                "username": user.username,
                "edit_count": edit_count,
                "share": round(edit_count / total_revisions, 4),
            }
        )
        if len(editors) == limit:
            break
    return editors


def select_contributors(
    counts: Counter[str],
    total_tokens: int,
    users: Mapping[int, ResolvedUser],
    minimum_tokens: int,
    minimum_share: float,
    global_groups: GlobalGroupLookup | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    contributors: list[dict[str, Any]] = []
    if total_tokens <= 0:
        return contributors

    for editor, token_count in counts.most_common():
        if not is_registered_editor(editor):
            continue
        share = token_count / total_tokens
        if token_count < minimum_tokens or share < minimum_share:
            continue
        # The significance gate runs before the exclusion rule, not after: the rule can
        # cost an HTTP request per account, and an account below the threshold cannot be
        # named whatever it turns out to be. The set of names is the same either way.
        user = users.get(int(editor))
        if user is None or not should_highlight_contributor(user, global_groups):
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
