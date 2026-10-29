import re
from pathlib import Path


def test_markdown_links_resolve():
    files = [Path("README.md"), Path("AGENTS.md"), *Path("docs").rglob("*.md")]
    broken = []
    for file in files:
        for target in re.findall(r"\]\(([^)]+)\)", file.read_text()):
            if ":" in target or target.startswith("#"):
                continue
            path = target.split("#")[0]
            if not (file.parent / path).exists():
                broken.append((str(file), target))
    assert broken == []
