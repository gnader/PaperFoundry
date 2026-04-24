"""Scientific literature monitor.

Fetches recent papers from configured sources (arXiv categories) and persists
them to a JSON file. Used as a library by `PaperFoundry.cli` (papertrack).
"""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

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
    published: str  # ISO 8601
    categories: List[str]
    source: str  # e.g. "arxiv:cs.GR"
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

    def fetch(
        self,
        source: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        known_ids: Optional[set] = None,
        target_new: Optional[int] = None,
    ) -> List[Paper]:
        """Fetch papers from a category name or an arXiv listing URL.

        Args:
            source: Either a category like "cs.GR" or a full listing URL like
                    "https://arxiv.org/list/cs.GR/recent".
            date_from: Start date in YYYYMMDD format (inclusive), or None.
            date_to: End date in YYYYMMDD format (inclusive), or None.
            known_ids: Set of paper IDs already in the output file. When
                       provided, fetching paginates until *target_new* unseen
                       papers are collected (or arXiv is exhausted).
            target_new: How many new (unseen) papers to collect. Defaults to
                        self.max_results.

        Returns:
            List of Paper objects, most recent first.
        """
        category = self._resolve_category(source)
        date_info = ""
        if date_from or date_to:
            date_info = f" [{date_from or '...'} -> {date_to or 'now'}]"

        if target_new is None:
            target_new = self.max_results

        if known_ids:
            print(f"Fetching arxiv:{category}{date_info} ({len(known_ids)} known, looking for {target_new} new)...")
        else:
            print(f"Fetching arxiv:{category}{date_info} (up to {target_new} papers)...")

        query = self._build_query(category, date_from, date_to)
        source_tag = f"arxiv:{category}"
        batch_size = self.max_results
        new_papers: List[Paper] = []
        start = 0

        while len(new_papers) < target_new:
            params = {
                "search_query": query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": start,
                "max_results": batch_size,
            }

            response = self.session.get(self.API_URL, params=params, timeout=20)
            response.raise_for_status()

            batch = self._parse_atom(response.text, source_tag=source_tag)

            if not batch:
                break

            for paper in batch:
                if known_ids and paper.id in known_ids:
                    continue
                new_papers.append(paper)
                if len(new_papers) >= target_new:
                    break

            if known_ids and len(new_papers) < target_new:
                print(f"  -> {len(new_papers)} new so far ({start + len(batch)} checked)...")

            if len(batch) < batch_size:
                break

            start += batch_size

        print(f"  -> {len(new_papers)} new papers retrieved.")
        return new_papers

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_query(self, category: str, date_from: Optional[str], date_to: Optional[str]) -> str:
        """Build an arXiv search query string with optional date range."""
        query = f"cat:{category}"
        if date_from or date_to:
            lo = date_from or "00000101"
            hi = date_to or datetime.now(timezone.utc).strftime("%Y%m%d")
            query += f" AND submittedDate:[{lo} TO {hi}]"
        return query

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

            authors = [(a.findtext("atom:name", "", ns) or "").strip() for a in entry.findall("atom:author", ns)]

            categories = [tag.get("term", "") for tag in entry.findall("atom:category", ns)]

            papers.append(
                Paper(
                    id=arxiv_id,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    url=f"https://arxiv.org/abs/{arxiv_id}",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                    published=published,
                    categories=categories,
                    source=source_tag,
                )
            )

        return papers


# ============================================================================
# Monitor — orchestrates multiple sources
# ============================================================================


class LiteratureMonitor:
    """Fetches papers from multiple sources and merges the results."""

    def __init__(self, max_results: int = 50):
        self.arxiv = ArxivFetcher(max_results=max_results)

    def fetch_all(
        self,
        sources: List[str],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        known_ids: Optional[set] = None,
        target_new: Optional[int] = None,
    ) -> List[Paper]:
        """Fetch from all sources and return a deduplicated, date-sorted list.

        When *known_ids* is provided the fetcher paginates past already-seen
        papers so that up to *target_new* genuinely new papers are returned.
        """
        all_papers: List[Paper] = []
        seen_ids: set = set(known_ids) if known_ids else set()

        for source in sources:
            try:
                papers = self._fetch_source(
                    source,
                    date_from,
                    date_to,
                    known_ids=known_ids,
                    target_new=target_new,
                )
                for paper in papers:
                    if paper.id not in seen_ids:
                        seen_ids.add(paper.id)
                        all_papers.append(paper)
            except Exception as e:
                print(f"  [warning] Failed to fetch '{source}': {e}")

        # Sort by published date, newest first
        all_papers.sort(key=lambda p: p.published, reverse=True)
        return all_papers

    def _fetch_source(self, source: str, date_from=None, date_to=None, known_ids=None, target_new=None) -> List[Paper]:
        """Route a source string to the right fetcher."""
        # Currently only arXiv is supported; extend here for other sites
        if "arxiv.org" in source or re.match(r"^[a-z]+\.[A-Z]+$", source):
            return self.arxiv.fetch(source, date_from=date_from, date_to=date_to, known_ids=known_ids, target_new=target_new)
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

    @staticmethod
    def load_ids(path: str) -> set:
        """Load just the paper IDs from a previously saved JSON file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {p["id"] for p in data["papers"]}
