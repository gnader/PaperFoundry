"""
Reference extraction from PDF documents.

This module provides functionality to extract and parse bibliographic references
from PDF files, supporting multiple citation formats (Eurographics, SIGGRAPH, etc.)
"""

import json
import re
import time
import urllib.parse
from typing import Dict, List, Optional

import fitz  # PyMuPDF

try:
    import requests
except ImportError:
    requests = None


class ReferenceExtractor:
    """Extracts and parses bibliographic references from PDF documents."""

    def __init__(self, pdf_path: str):
        """Initialize the extractor with a PDF file path."""
        self.pdf_path = pdf_path
        self.full_text = self._extract_text()

    # ============================================================================
    # Text Extraction
    # ============================================================================

    def _extract_text(self) -> str:
        """Extract text from PDF, handling single and multi-column layouts."""
        doc = fitz.open(self.pdf_path)
        full_text = ""

        for page in doc:
            full_text += self._extract_page_text(page) + "\n"

        return full_text

    def _extract_page_text(self, page) -> str:
        """Extract text from a single page, respecting column layout."""
        blocks = page.get_text("blocks")
        blocks = [b for b in blocks if b[4].strip()]  # keep text only

        page_width = page.rect.width
        x_positions = [b[0] for b in blocks]
        spread = max(x_positions) - min(x_positions)

        # Single-column layout
        if spread < page_width * 0.4:
            blocks.sort(key=lambda b: b[1])
            return "\n".join(b[4] for b in blocks)

        # Multi-column layout
        return self._extract_multicolumn_text(page_width, blocks)

    def _extract_multicolumn_text(self, page_width: float, blocks: List) -> str:
        """Extract text from multi-column layout."""
        mid_x = page_width / 2
        left_blocks, right_blocks = [], []

        for b in blocks:
            x0, y0, _, _, text = b[:5]
            if x0 < mid_x:
                left_blocks.append((y0, text))
            else:
                right_blocks.append((y0, text))

        left_blocks.sort(key=lambda x: x[0])
        right_blocks.sort(key=lambda x: x[0])

        column_text = "\n".join(text for _, text in left_blocks)
        column_text += "\n" + "\n".join(text for _, text in right_blocks)
        return column_text

    # ============================================================================
    # Reference Section Detection
    # ============================================================================

    def _find_references_section(self) -> Optional[str]:
        """Find the references section in the document."""
        # Try common section headers
        patterns = [
            r"\nReferences\n",
            r"\nREFERENCES\n",
            r"\nBibliography\n",
            r"\nBIBLIOGRAPHY\n",
        ]
        for pattern in patterns:
            match = re.search(pattern, self.full_text)
            if match:
                return self.full_text[match.end() :]

        # Fallback: detect high DOI density
        doi_matches = list(re.finditer(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", self.full_text, re.I))
        if len(doi_matches) > 5:
            return self.full_text[doi_matches[0].start() :]

        return None

    # ============================================================================
    # Reference Splitting
    # ============================================================================

    def _split_references(self, ref_text: str) -> List[str]:
        """Split reference text into individual references."""
        ref_text = re.sub(r"\n+", "\n", ref_text)

        # Patterns for different reference styles
        patterns = {
            "numeric_bracket": r"\n?\[\d+\]",
            "author_year_bracket": r"\n?\[[A-Za-z\+\*]{2,}[0-9]{2,4}\]",
            "numeric_dot": r"\n?\d+\.\s",
            "generic_bracket": r"\n?\[[^\]]+\]",
        }

        # Detect dominant pattern
        pattern_counts = {name: len(re.findall(pat, ref_text)) for name, pat in patterns.items()}
        best_pattern_name = max(pattern_counts, key=pattern_counts.get)

        if pattern_counts[best_pattern_name] < 2:
            return self._fallback_split(ref_text)

        return self._split_by_pattern(ref_text, patterns[best_pattern_name])

    def _split_by_pattern(self, ref_text: str, pattern: str) -> List[str]:
        """Split references using a detected pattern."""
        matches = list(re.finditer(pattern, ref_text))
        references = []

        for i in range(len(matches)):
            start = matches[i].start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(ref_text)
            ref = ref_text[start:end].strip()
            references.append(self._normalize_reference(ref))

        return references

    def _fallback_split(self, ref_text: str) -> List[str]:
        """Split references by paragraph when pattern detection fails."""
        chunks = re.split(r"\n\s*\n", ref_text)
        return [self._normalize_reference(c) for c in chunks if len(c.strip()) > 40]

    # ============================================================================
    # Text Normalization
    # ============================================================================

    def _normalize_reference(self, reference: str) -> str:
        """Normalize reference text: fix hyphenation, line breaks, whitespace."""
        reference = self._fix_hyphenation(reference)
        reference = reference.replace("\n", " ")
        reference = re.sub(r"\s+", " ", reference)
        return reference.strip()

    def _fix_hyphenation(self, text: str) -> str:
        """Fix incorrectly hyphenated words across line breaks."""
        return re.sub(r"([a-zA-Z])-\s*\n\s*([a-zA-Z])", r"\1\2", text)

    # ============================================================================
    # Reference Parsing
    # ============================================================================

    def _parse_reference(self, reference: str) -> Dict:
        """Parse a reference into structured components."""
        authors, title, rest = self._split_author_title_rest(reference)
        year = self._extract_year(reference)

        return {
            "raw": reference,
            "authors": authors,
            "title": title,
            "rest": rest,
            "year": year,
        }

    def _split_author_title_rest(self, reference: str) -> tuple:
        """Split reference into authors, title, and rest based on detected style."""
        # Eurographics: authors end with colon
        if re.search(r":\s", reference):
            return self._split_eurographics_reference(reference)
        # SIGGRAPH or generic style
        return self._split_siggraph_or_generic(reference)

    def _extract_year(self, reference: str) -> Optional[str]:
        """Extract publication year from reference."""
        year_match = re.search(r"\b(19|20)\d{2}\b", reference)
        return year_match.group(0) if year_match else None

    def _split_eurographics_reference(self, reference: str) -> tuple:
        """Parse Eurographics-style reference (authors: title. rest)."""
        ref = re.sub(r"^\[[^\]]+\]\s*", "", reference).strip()

        if ":" not in ref:
            return self._fallback_reference_split(ref)

        authors_part, rest_part = ref.split(":", 1)
        authors = authors_part.strip().rstrip(".")

        if "." in rest_part:
            title_part, rest = rest_part.split(".", 1)
            title = title_part.strip().rstrip(".")
            rest = rest.strip()
        else:
            title = rest_part.strip()
            rest = ""

        return authors, title, rest

    def _split_siggraph_or_generic(self, reference: str) -> tuple:
        """Parse SIGGRAPH-style reference (authors year. title. rest)."""
        ref = re.sub(r"^\[[^\]]+\]\s*", "", reference).strip()

        year_match = re.search(r"\b(19|20)\d{2}\b", ref)
        if not year_match:
            return self._fallback_reference_split(ref)

        # SIGGRAPH style: year immediately after authors
        year_index = year_match.start()
        authors = ref[:year_index].strip().rstrip(".")
        after_year = ref[year_match.end() :].strip()

        period_index = after_year.find(".")
        if period_index != -1:
            title = after_year[:period_index].strip()
            rest = after_year[period_index + 1 :].strip()
        else:
            title = after_year
            rest = ""

        return authors, title, rest

    def _fallback_reference_split(self, ref: str) -> tuple:
        """Fallback: split reference by periods."""
        parts = ref.split(". ")
        authors = parts[0].strip() if len(parts) > 0 else ""
        title = parts[1].strip() if len(parts) > 1 else ""
        rest = ". ".join(parts[2:]).strip() if len(parts) > 2 else ""
        return authors.rstrip("."), title.rstrip("."), rest.rstrip(".")

    # ============================================================================
    # Public API
    # ============================================================================

    def extract(self) -> List[Dict]:
        """Extract and parse all references from the PDF document."""
        ref_section = self._find_references_section()
        if not ref_section:
            raise ValueError("No references section detected.")

        raw_refs = self._split_references(ref_section)
        return [self._parse_reference(ref) for ref in raw_refs]


# ============================================================================
# Google Scholar Metadata Lookup
# ============================================================================


def lookup_google_scholar_metadata(title: str, year: Optional[str] = None) -> Optional[Dict]:
    """
    Lookup reference metadata using CrossRef API.

    Args:
        title: The title of the reference to search for.
        year: The publication year to help narrow down the search (optional).

    Returns:
        Dictionary containing metadata (authors, year, journal, etc.) or None if not found.
    """
    if requests is None:
        print("Warning: requests library not installed. Install with: pip install requests")
        return None

    try:
        # CrossRef API endpoint
        url = "https://api.crossref.org/works"

        # Search parameters
        params = {"query.title": title, "rows": 100, "sort": "relevance", "order": "desc"}  # Get more results to find the best match

        headers = {"User-Agent": "PaperFoundry (mailto:contact@example.com)"}

        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Check if results found
        if not data.get("message", {}).get("items"):
            print(f"No results found for: {title}" + (f" ({year})" if year else ""))
            return None

        # Find the best match - prefer exact year match if year was provided
        items = data["message"]["items"]
        item = None

        if year:
            try:
                year_int = int(year)
                # First, try to find an item with matching year
                for candidate in items:
                    candidate_year = candidate.get("issued", {}).get("date-parts", [[None]])[0][0]
                    if candidate_year == year_int:
                        item = candidate
                        break
            except (ValueError, TypeError):
                pass

        # Fall back to first result if no year match found
        if not item:
            item = items[0]

        # Extract authors
        authors = []
        if "author" in item:
            authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in item["author"]]

        # Extract metadata
        metadata = {
            "title": item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title", ""),
            "authors": ", ".join(authors),
            "year": item.get("issued", {}).get("date-parts", [[None]])[0][0],
            "journal": item.get("container-title", [""])[0] if isinstance(item.get("container-title"), list) else item.get("container-title", ""),
            "volume": item.get("volume", ""),
            "issue": item.get("issue", ""),
            "pages": item.get("page", ""),
            "doi": item.get("DOI", ""),
            "url": item.get("URL", ""),
            "citation_count": item.get("is-referenced-by-count", 0),
            "publisher": item.get("publisher", ""),
        }

        return metadata

    except requests.exceptions.RequestException as e:
        print(f"Network error looking up '{title}': {e}")
        return None
    except Exception as e:
        print(f"Error looking up '{title}': {e}")


def enrich_references_with_scholar(references: List[Dict], delay: float = 1.0) -> List[Dict]:
    """
    Enrich extracted references with metadata from Google Scholar.

    Args:
        references: List of reference dictionaries from extract().
        delay: Delay in seconds between requests (to respect server limits).

    Returns:
        List of references with added Google Scholar metadata.
    """
    enriched = []

    for ref in references:
        title = ref.get("title", "").strip()
        year = ref.get("year", "")

        if title:
            print(f"Looking up: {title}" + (f" ({year})" if year else ""))
            scholar_data = lookup_google_scholar_metadata(title, year)

            if scholar_data:
                ref["scholar_metadata"] = scholar_data
            time.sleep(delay)  # Rate limiting
        enriched.append(ref)

    return enriched


# ============================================================================
# Main Execution
# ============================================================================


def main() -> None:
    """Main entry point: extract references and save to JSON."""
    extractor = ReferenceExtractor("test.pdf")
    references = extractor.extract()

    # Optionally enrich with Google Scholar metadata
    # Uncomment the line below to enable Google Scholar lookup
    references = enrich_references_with_scholar(references, delay=0.1)

    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(references, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
