"""Topic definitions and markdown topic-file loader.

Topics live as one markdown file per topic, typically under a `topics/` directory.

File layout:
    # Topic Name

    ## Description
    <free-form prose, multiple paragraphs OK>

    ## Keywords
    - keyword 1
    - keyword 2

    ## Papers
    - Title (Authors, Year)

Parsing rules:
- The single `# ...` heading is the topic name (required).
- `## Description` / `## Keywords` / `## Papers` are section headers; a body runs
  until the next `##` or EOF.
- Description body is joined as free-form text (stripped).
- Keywords and Papers bodies are parsed as bullet lists (`-` or `*` prefix).
  Non-bullet lines between bullets are ignored. Empty section → empty list.
- Missing `## Description` → empty string. Missing `## Keywords` or `## Papers`
  → empty list. Missing `# Title` → `ValueError`.

`load_topics(path)` accepts either a directory (loads all `*.md`, sorted by
filename) or a single `.md` file path.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Topic:
    name: str
    keywords: List[str]
    description: str = ""
    papers: List[str] = field(default_factory=list)


def _parse_topic_md(text: str, source: Path) -> Topic:
    """Parse a single topic markdown file. See module docstring for the layout."""
    name = ""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            name = stripped[2:].strip()
            current = None
        elif stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    if not name:
        raise ValueError(f"Topic file {source} is missing a '# Title' heading")

    def _bullets(key: str) -> List[str]:
        items: List[str] = []
        for raw in sections.get(key, []):
            s = raw.strip()
            if s.startswith(("- ", "* ")):
                items.append(s[2:].strip())
        return items

    description = "\n".join(sections.get("description", [])).strip()
    keywords = _bullets("keywords")
    papers = _bullets("papers")

    return Topic(name=name, keywords=keywords, description=description, papers=papers)


def load_topics(path: str) -> List[Topic]:
    """Load topics from a directory of .md files, or from a single .md file."""
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("*.md"))
        if not files:
            raise ValueError(f"No .md topic files found in {path!r}")
    elif p.is_file() and p.suffix.lower() == ".md":
        files = [p]
    else:
        raise ValueError(f"Topics path {path!r} is not a directory or .md file")

    topics = [_parse_topic_md(f.read_text(encoding="utf-8"), f) for f in files]
    if not topics:
        raise ValueError(f"No topics found in {path!r}")
    return topics
