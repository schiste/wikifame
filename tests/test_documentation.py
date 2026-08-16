import re
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCAL_LINK = re.compile(r"\[[^]]+\]\((?!https?://|#)([^)]+)\)")


def test_local_documentation_links_exist() -> None:
    markdown_files = [
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "CONTRIBUTING.md",
        REPOSITORY_ROOT / "AGENTS.md",
        *sorted((REPOSITORY_ROOT / "docs").rglob("*.md")),
    ]
    broken: list[str] = []
    for markdown_file in markdown_files:
        content = markdown_file.read_text(encoding="utf-8")
        for raw_target in LOCAL_LINK.findall(content):
            target = unquote(raw_target.split("#", 1)[0])
            if not (markdown_file.parent / target).resolve().exists():
                broken.append(f"{markdown_file.relative_to(REPOSITORY_ROOT)} -> {raw_target}")

    assert broken == []


def test_handoff_documents_cover_required_topics() -> None:
    operations = (REPOSITORY_ROOT / "docs/operations.md").read_text(encoding="utf-8")
    agent_guide = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    decision = (REPOSITORY_ROOT / "docs/decisions/0001-attribution-policy.md").read_text(
        encoding="utf-8"
    )

    assert "Maintainer transfer checklist" in operations
    assert "Required validation" in agent_guide
    assert "temporary accounts" in decision
    assert "ALGORITHM_VERSION" in decision
