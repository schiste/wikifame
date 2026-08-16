"""The published configuration defaults are an interface contract.

People copy these files onto wikis we do not control and cannot fix. A key the gadget stopped
reading, or a message key that no longer exists, would fail silently for that reader, so the
defaults and their documentation are checked against the gadget source rather than by hand.
"""

import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GADGET_SOURCE = (REPOSITORY_ROOT / "wikifame.js").read_text(encoding="utf-8")
SETUP_GUIDE = (REPOSITORY_ROOT / "docs/onwiki-setup.md").read_text(encoding="utf-8")
DEFAULTS = sorted((REPOSITORY_ROOT / "config").glob("*.json"))


def _block(source: str, opening: str) -> str:
    start = source.index(opening) + len(opening)
    depth = 1
    for offset, character in enumerate(source[start:]):
        depth += {"{": 1, "}": -1}.get(character, 0)
        if depth == 0:
            return source[start : start + offset]
    raise AssertionError(f"unbalanced braces after {opening!r}")


def _config_keys() -> set[str]:
    block = _block(GADGET_SOURCE, "DEFAULT_CONFIG = {")
    return set(re.findall(r"^\t\t(\w+):", block, re.MULTILINE))


def _message_keys() -> set[str]:
    return set(re.findall(r"'(wikifame-[a-z-]+)':", _block(GADGET_SOURCE, "MESSAGES = {")))


def test_defaults_are_published_for_the_wikis_the_gadget_already_speaks() -> None:
    assert [path.name for path in DEFAULTS] == ["enwiki.json", "frwiki.json"]


def test_defaults_are_valid_json_using_only_keys_the_gadget_reads() -> None:
    known = _config_keys()
    assert known == {
        "enabled",
        "showHistoryIntro",
        "editHelpPage",
        "sandboxPage",
        "historyIntroPage",
        "messages",
    }

    for path in DEFAULTS:
        default = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(default, dict), path.name
        assert set(default) <= known, path.name
        assert isinstance(default["enabled"], bool), path.name
        assert isinstance(default["showHistoryIntro"], bool), path.name
        # The help sentence renders only when both titles are present.
        assert bool(default["editHelpPage"]) == bool(default["sandboxPage"]), path.name
        # Rich content is opt-in: a published default must never point at a page that
        # only exists on the wiki it was copied from.
        assert default["historyIntroPage"] is None, path.name
        assert set(default.get("messages", {})) <= _message_keys(), path.name


def test_setup_guide_documents_every_field_and_message_key() -> None:
    missing = [key for key in _config_keys() | _message_keys() if f"`{key}`" not in SETUP_GUIDE]

    assert missing == []


def test_setup_guide_names_the_page_the_gadget_actually_fetches() -> None:
    suffix = re.search(r"CONFIG_PAGE_SUFFIX = '([^']+)'", GADGET_SOURCE)
    assert suffix is not None
    assert suffix.group(1).lstrip("/") in SETUP_GUIDE
    # The guide must not send people to a page only interface admins can create.
    assert "MediaWiki:Wikifame-config.json" not in SETUP_GUIDE.split("## Later", 1)[0]
