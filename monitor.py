"""
Scientific literature monitor.

Fetches recent papers from configured sources (arXiv categories) and saves
them to a JSON file. Designed to be run periodically to track new publications.

Usage:
    python monitor.py cs.GR cs.CV -o papers.json --max 50
    python monitor.py https://arxiv.org/list/cs.GR/recent -o papers.json
"""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

try:
    import requests
except ImportError:
    raise SystemExit("requests is required: pip install requests")


# ============================================================================
# Data model
# ============================================================================


@dataclass
class Paper:
    id: str
    title: str
    authors: List[str]
    abstract: str
    url: str
    pdf_url: str
    published: str          # ISO 8601
    categories: List[str]
    source: str             # e.g. "arxiv:cs.GR"
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ============================================================================
# arXiv fetcher
# ============================================================================


class ArxivFetcher:
    """Fetches papers from arXiv via the official Atom API."""

    API_URL = "https://export.arxiv.org/api/query"
    _NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    def __init__(self, max_results: int = 50):
        self.max_results = max_results
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "PaperFoundry/1.0"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self, source: str) -> List[Paper]:
        """Fetch papers from a category name or an arXiv listing URL.

        Args:
            source: Either a category like "cs.GR" or a full listing URL like
                    "https://arxiv.org/list/cs.GR/recent".

        Returns:
            List of Paper objects, most recent first.
        """
        category = self._resolve_category(source)
        print(f"Fetching arxiv:{category} (up to {self.max_results} papers)...")

        params = {
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self.max_results,
        }

        response = self.session.get(self.API_URL, params=params, timeout=20)
        response.raise_for_status()

        papers = self._parse_atom(response.text, source_tag=f"arxiv:{category}")
        print(f"  -> {len(papers)} papers retrieved.")
        return papers

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _resolve_category(self, source: str) -> str:
        """Extract a category string from either a bare name or a listing URL."""
        if source.startswith("http"):
            match = re.search(r"arxiv\.org/list/([^/?#]+)", source)
            if not match:
                raise ValueError(f"Cannot extract arXiv category from URL: {source}")
            return match.group(1)
        return source

    def _parse_atom(self, xml_text: str, source_tag: str) -> List[Paper]:
        """Parse the arXiv Atom feed into Paper dataclasses."""
        ns = self._NS
        root = ET.fromstring(xml_text)
        papers = []

        for entry in root.findall("atom:entry", ns):
            # arXiv ID — strip base URL and version suffix
            raw_id = entry.findtext("atom:id", "", ns)
            arxiv_id = raw_id.split("/abs/")[-1]
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)

            title = (entry.findtext("atom:title", "", ns) or "").strip()
            title = re.sub(r"\s+", " ", title)

            abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
            abstract = re.sub(r"\s+", " ", abstract)

            published = entry.findtext("atom:published", "", ns) or ""

            authors = [
                (a.findtext("atom:name", "", ns) or "").strip()
                for a in entry.findall("atom:author", ns)
            ]

            categories = [
                tag.get("term", "")
                for tag in entry.findall("atom:category", ns)
            ]

            papers.append(Paper(
                id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                published=published,
                categories=categories,
                source=source_tag,
            ))

        return papers


# ============================================================================
# Monitor — orchestrates multiple sources
# ============================================================================


class LiteratureMonitor:
    """Fetches papers from multiple sources and merges the results."""

    def __init__(self, max_results: int = 50):
        self.arxiv = ArxivFetcher(max_results=max_results)

    def fetch_all(self, sources: List[str]) -> List[Paper]:
        """Fetch from all sources and return a deduplicated, date-sorted list."""
        all_papers: List[Paper] = []
        seen_ids: set = set()

        for source in sources:
            try:
                papers = self._fetch_source(source)
                for paper in papers:
                    if paper.id not in seen_ids:
                        seen_ids.add(paper.id)
                        all_papers.append(paper)
            except Exception as e:
                print(f"  [warning] Failed to fetch '{source}': {e}")

        # Sort by published date, newest first
        all_papers.sort(key=lambda p: p.published, reverse=True)
        return all_papers

    def _fetch_source(self, source: str) -> List[Paper]:
        """Route a source string to the right fetcher."""
        # Currently only arXiv is supported; extend here for other sites
        if "arxiv.org" in source or re.match(r"^[a-z]+\.[A-Z]+$", source):
            return self.arxiv.fetch(source)
        raise ValueError(f"Unsupported source: '{source}'. Only arXiv URLs/categories are supported.")

    @staticmethod
    def save(papers: List[Paper], path: str) -> None:
        """Save papers to a JSON file."""
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(papers),
            "papers": [asdict(p) for p in papers],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(papers)} papers to {path}")

    @staticmethod
    def load(path: str) -> List[Paper]:
        """Load papers from a previously saved JSON file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [Paper(**p) for p in data["papers"]]


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch recent papers from arXiv categories and save to JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python monitor.py cs.GR
  python monitor.py cs.GR cs.CV cs.LG -o papers.json --max 100
  python monitor.py https://arxiv.org/list/cs.GR/recent
        """,
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="arXiv category names (e.g. cs.GR) or listing URLs.",
    )
    parser.add_argument(
        "-o", "--output",
        default="papers.json",
        help="Output JSON file (default: papers.json).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=50,
        help="Max papers to fetch per source (default: 50).",
    )
    args = parser.parse_args()

    monitor = LiteratureMonitor(max_results=args.max)
    papers = monitor.fetch_all(args.sources)

    # Print a quick summary
    print(f"\n{'─' * 60}")
    print(f"{'Title':<55} {'Date':<12}")
    print(f"{'─' * 60}")
    for p in papers[:20]:
        date = p.published[:10]
        print(f"{p.title[:54]:<55} {date}")
    if len(papers) > 20:
        print(f"  ... and {len(papers) - 20} more")

    monitor.save(papers, args.output)


if __name__ == "__main__":
    main()
