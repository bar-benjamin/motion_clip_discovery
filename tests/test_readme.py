import re
from pathlib import Path


def test_readme_lists_codes_and_required_release_headings():
    readme = Path("README.md").read_text()
    code_text = "\n".join(p.read_text() for p in Path("src").rglob("*.py"))
    codes = set(re.findall(r"\b[EW]_[A-Z_]+\b", code_text))
    assert codes <= set(re.findall(r"\b[EW]_[A-Z_]+\b", readme))
    for heading in (
        "Product Description",
        "Usage Documentation / Example",
        "Known Issues",
        "Coming Soon",
    ):
        assert f"### {heading}" in readme
