"""Topic-based paper filter.

Reads a papers.json file (produced by monitor.py) and a topics.json config,
then matches papers against topic keywords and outputs the results.

Usage:
    python filter.py papers.json --topics topics.json [-o filtered.json]
"""

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


# ============================================================================
# Data model
# ============================================================================


@dataclass
class Topic:
    name: str
    keywords: List[str]
    description: str = ""
    papers: List[str] = field(default_factory=list)


# ============================================================================
# I/O helpers
# ============================================================================


def load_papers(path: str) -> List[dict]:
    """Load papers from a papers.json file produced by monitor.py."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    papers = data.get("papers", [])
    if not papers:
        raise ValueError(f"No papers found in {path!r}")
    return papers


def load_topics(path: str) -> List[Topic]:
    """Load and validate topics from a topics.json config file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    topics = []
    for t in data.get("topics", []):
        name = t.get("name", "").strip()
        if not name:
            raise ValueError("Topic entry is missing a 'name' field")
        keywords = t.get("keywords", [])
        if not isinstance(keywords, list):
            raise ValueError(f"Topic '{name}': 'keywords' must be a list")
        topics.append(Topic(
            name=name,
            keywords=[str(k) for k in keywords],
            description=t.get("description", ""),
            papers=t.get("papers", []),
        ))
    if not topics:
        raise ValueError(f"No topics found in {path!r}")
    return topics


def save_results(results: Dict[str, List[dict]], source_file: str, topics_file: str, path: str) -> None:
    """Write filtered results to a JSON file."""
    total = sum(len(papers) for papers in results.values())
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "topics_file": topics_file,
        "total_matched_papers": total,
        "results": [
            {
                "topic": topic_name,
                "match_count": len(papers),
                "papers": papers,
            }
            for topic_name, papers in results.items()
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nSaved results to {path}")


def format_results(results: Dict[str, List[dict]]) -> None:
    """Print a human-readable summary to stdout."""
    if not results:
        print("No papers matched any topic.")
        return

    for topic_name, papers in results.items():
        print(f"\n{topic_name}  ({len(papers)} paper{'s' if len(papers) != 1 else ''})")
        for paper in papers:
            date = (paper.get("published") or "")[:10]
            title = paper.get("title", "(no title)")
            url = paper.get("url", "")
            keywords = paper.get("matched_keywords", [])
            print(f"  [{date}] {title}")
            if keywords:
                print(f"           Keywords: {', '.join(keywords)}")
            if url:
                print(f"           {url}")


# ============================================================================
# Matching logic
# ============================================================================


def match_paper(paper: dict, topic: Topic) -> List[str]:
    """Return the list of topic keywords found in the paper's title + abstract."""
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    haystack = (title + " " + abstract).lower()
    return [kw for kw in topic.keywords if kw.lower() in haystack]


class TopicFilter:
    def __init__(self, topics: List[Topic]):
        self.topics = topics

    def run(self, papers: List[dict]) -> Dict[str, List[dict]]:
        """Match papers against all topics.

        Returns a dict mapping topic name → list of matching paper dicts.
        Each paper dict is a copy of the original with an added
        'matched_keywords' field. Topics with zero matches are omitted.
        A paper can appear under multiple topics.
        """
        results: Dict[str, List[dict]] = {}

        for topic in self.topics:
            matched = []
            for paper in papers:
                hits = match_paper(paper, topic)
                if hits:
                    # Compact copy — no abstract, just what's needed for triage
                    matched.append({
                        "id": paper.get("id", ""),
                        "title": paper.get("title", ""),
                        "authors": paper.get("authors", []),
                        "published": paper.get("published", ""),
                        "url": paper.get("url", ""),
                        "matched_keywords": hits,
                    })
            if matched:
                results[topic.name] = matched

        return results


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter papers by topic keywords.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python filter.py papers.json
  python filter.py papers.json --topics topics.json -o filtered.json
        """,
    )
    parser.add_argument(
        "papers",
        help="Path to papers.json produced by monitor.py.",
    )
    parser.add_argument(
        "--topics",
        default="topics.json",
        metavar="FILE",
        help="Path to topics.json config (default: topics.json).",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="Write filtered results to this JSON file.",
    )
    args = parser.parse_args()

    papers = load_papers(args.papers)
    topics = load_topics(args.topics)

    print(f"Loaded {len(papers)} papers, {len(topics)} topic(s).")

    filt = TopicFilter(topics)
    results = filt.run(papers)

    format_results(results)

    if args.output:
        save_results(results, args.papers, args.topics, args.output)


if __name__ == "__main__":
    main()
