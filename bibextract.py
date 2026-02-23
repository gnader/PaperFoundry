"""
Reference extraction from PDF documents.

This module provides functionality to extract and parse bibliographic references
from PDF files, supporting multiple citation formats (Eurographics, SIGGRAPH, etc.)
"""

import argparse
import fitz  # PyMuPDF
import json
import re
import time
from typing import Dict, List, Optional


try:
    import requests
except ImportError:
    requests = None


class ReferenceExtractor:
    """Extracts and parses bibliographic references from PDF documents."""

    def __init__(self, pdf_path: str):
        """Initialize the extractor with a PDF file path."""
        self.pdf_path = pdf_path
        self.document = fitz.open(pdf_path)

        self.full_text = self._extract_text()
        self.sections = self._detect_sections()
        self.citation_tag_pattern = None  # Detected citation pattern

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
            return "".join(b[4] for b in blocks)

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
    # Section Detection
    # ============================================================================

    def _detect_column_starts(self) -> List[float]:
        """Detect the starting x-coordinates of columns in the document."""
        x_positions = []
        for page in self.document:
            blocks = page.get_text("blocks")
            for block in blocks:
                if len(block) >= 5 and block[4].strip():
                    x_positions.append((block[0], block[2]))  # x0 (start) and x1 (end)

        if not x_positions:
            return []

        # Group nearby x-positions together (tolerance for rounding/slight variations)
        tolerance = 5  # Points tolerance for grouping
        grouped = {}
        for x, _ in x_positions:
            # Find if this x belongs to an existing group
            found_group = False
            for group_key in grouped.keys():
                if abs(x - group_key) < tolerance:
                    grouped[group_key] += 1
                    found_group = True
                    break
            if not found_group:
                grouped[x] = 1

        # Sort groups by frequency (most common first)
        sorted_groups = sorted(grouped.items(), key=lambda item: item[1], reverse=True)
        sorted_groups = [(x, freq / len(x_positions)) for x, freq in sorted_groups if freq / len(x_positions) > 0.1]

        if len(sorted_groups) == 1:
            return [sorted_groups[0][0]]

        # return the most two frequent x-positions as column starts
        return [sorted_groups[0][0], sorted_groups[1][0]]

    def _detect_sections(self) -> Dict[str, tuple]:
        """Detect document sections by looping over pages and analyzing text blocks."""
        section_headers = []
        char_pos = 0

        # First pass: detect column starting positions
        column_starts = self._detect_column_starts()

        # Second pass: identify section headers based on text content and position
        for page_num, page in enumerate(self.document):
            blocks = page.get_text("blocks")
            for block in blocks:
                # Block tuple: (x0, y0, x1, y1, text, ...)
                # bbox is first 4 elements, text content is index 4
                if len(block) >= 5:
                    x0, y0, x1, y1, text = block[:5]
                    text = text.replace("\n", " ").strip()

                    # Skip empty blocks
                    if not text:
                        continue

                    # Check if block looks like a section header
                    if 5 < len(text) < 100:  # quick heuristic to skip very short or very long blocks
                        # Check for numbered sections (1 Title, 2. Title, etc.)
                        if self._is_likely_section_header(text, x0, column_starts):
                            section_num = self._extract_section_number(text)
                            clean_title = self._strip_section_number(text)
                            section_headers.append({"title": clean_title, "position": char_pos, "number": section_num, "page": page_num, "bbox": (x0, y0, x1, y1)})
                        # Check for unnumbered Abstract section
                        elif self._is_abstract_section(text):
                            section_headers.append({"title": text, "position": char_pos, "number": None, "is_abstract": True, "page": page_num, "bbox": (x0, y0, x1, y1)})
                        # Check for unnumbered References/Bibliography
                        elif self._is_references_section(text):
                            section_headers.append({"title": text, "position": char_pos, "number": None, "is_references": True, "page": page_num, "bbox": (x0, y0, x1, y1)})

                    # Update position counter
                    char_pos += len(text) + 1

        return section_headers

    def _strip_section_number(self, text: str) -> str:
        """Remove leading number and optional period from section title.

        Examples:
        - "1 Introduction" -> "Introduction"
        - "2. Background" -> "Background"
        - "3.1 Subsection" -> "3.1 Subsection" (unchanged if subsection)
        """
        # Remove leading single-digit section number (optionally with period) followed by whitespace
        return re.sub(r"^\d+\.?\s+", "", text).strip()

    def _is_likely_section_header(self, text: str, x0: float = None, column_starts: List[float] = None) -> bool:
        """Check if text looks like a section header.

        A section should be: number (optionally with period) followed by title
        - "1 Title" ✓
        - "1. Title" ✓
        - "1.1 Title" ✗ (subsection)
        - "2.3 Title" ✗ (subsection)

        Also checks if the block starts at a column boundary (if column info is available).
        """
        # Match section headers: number optionally followed by period, then space/title
        # Use lookahead to exclude subsections (1.x where x is digit)
        section_pattern = r"^\d+(?!\.(?:\d))\s*\.?\s+"

        if not re.match(section_pattern, text):
            return False

        # If we have column information, verify the block starts at a column boundary
        if x0 is not None and column_starts:
            tolerance = 1  # Points tolerance for matching x0 to column start
            is_at_column_start = any(abs(x0 - col_start) < tolerance for col_start in column_starts)
            return is_at_column_start

        return True

    def _extract_section_number(self, text: str) -> Optional[int]:
        """Extract the section number from a section header like '1 Title' or '2. Title'."""
        match = re.match(r"^(\d+)", text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def _is_references_section(self, text: str) -> bool:
        """Check if text is an unnumbered References or Bibliography section."""
        text_lower = text.strip().lower()
        return text_lower in ("references", "bibliography", "works cited")

    def _is_abstract_section(self, text: str) -> bool:
        """Check if text is an Abstract section."""
        text_lower = text.strip().lower()
        return text_lower == "abstract"

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
        tag = self._extract_citation_tag(reference)

        return {
            "raw": reference,
            "tag": tag,
            "authors": authors,
            "title": title,
            # "rest": rest,
            "year": year,
        }

    def _extract_citation_tag(self, reference: str) -> Optional[str]:
        """Extract the citation tag/label from raw reference (e.g., [1], [Smith2020])."""
        # Try numeric bracket: [1], [2], etc.
        match = re.match(r"\[(\d+)\]", reference)
        if match:
            return f"[{match.group(1)}]"

        # Try author-year bracket: [Smith2020], [ABC2015], etc.
        match = re.match(r"\[([A-Za-z\+\*]{2,}[0-9]{2,4})\]", reference)
        if match:
            return f"[{match.group(1)}]"

        # Try numeric dot: 1., 2., etc.
        match = re.match(r"(\d+)\.", reference)
        if match:
            return f"{match.group(1)}."

        return None

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
        parsed_refs = [self._parse_reference(ref) for ref in raw_refs]

        # Enrich with citation location data
        enriched_refs = self._enrich_with_citation_locations(parsed_refs)
        return enriched_refs

    def _enrich_with_citation_locations(self, references: List[Dict]) -> List[Dict]:
        """Add citation location and importance scoring to references."""
        # Find where each reference is cited in the document
        text_before_refs = self.full_text[: self.full_text.find(self._find_references_section())]

        for ref in references:
            tag = ref.get("tag")
            if not tag:
                ref["cited_in_sections"] = []
                ref["importance_score"] = 0
                continue

            # Find sections where this tag appears
            cited_sections = self._find_citation_in_sections(tag, text_before_refs)
            ref["cited_in_sections"] = list(cited_sections.keys())
            ref["section_citations"] = cited_sections

            # Calculate importance score
            ref["importance_score"] = self._calculate_importance_score(cited_sections)

        return references

    def _find_citation_in_sections(self, tag: str, text: str) -> Dict[str, int]:
        """Find which sections cite this reference tag."""
        cited_sections = {}

        # Escape special regex characters
        tag_pattern = re.escape(tag).replace(r"\ ", r"\s")

        for section_name, (start, end) in self.sections.items():
            if end is None:
                section_text = text[start:]
            else:
                section_text = text[start:end]

            # Count occurrences in this section
            count = len(re.findall(tag_pattern, section_text, re.IGNORECASE))
            if count > 0:
                cited_sections[section_name] = count

        return cited_sections

    def _calculate_importance_score(self, cited_sections: Dict[str, int]) -> float:
        """Calculate paper importance based on citation patterns and locations."""
        if not cited_sections:
            return 0.0

        score = 0.0
        for section_title, count in cited_sections.items():
            weight = self._get_section_weight(section_title)
            score += count * weight

        # Bonus for being cited in multiple sections (shows breadth of relevance)
        num_sections = len(cited_sections)
        if num_sections >= 3:
            score *= 1.5
        elif num_sections >= 2:
            score *= 1.2

        return round(score, 2)

    def _get_section_weight(self, section_title: str) -> float:
        """Determine importance weight based on section title keywords."""
        title_lower = section_title.lower()

        # High importance: core content
        if any(word in title_lower for word in ["method", "approach", "technique", "algorithm", "result", "experiment", "evaluation"]):
            return 3.0

        # Medium-high importance: context and analysis
        if any(word in title_lower for word in ["related", "state", "art", "background", "discussion", "analysis", "comparison"]):
            return 2.0

        # Medium importance: positioning
        if any(word in title_lower for word in ["conclusion", "future", "work", "summary"]):
            return 1.5

        # Lower importance: motivation and intro
        if any(word in title_lower for word in ["introduction", "motivation", "abstract"]):
            return 1.0

        # Default: neutral importance
        return 1.2


# ============================================================================
# Google Scholar Metadata Lookup
# ============================================================================


def lookup_google_scholar_metadata(title: str, year: Optional[str] = None) -> Optional[Dict]:
    """
    Lookup reference metadata using OpenAlex API (covers all databases like Scholar).

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
        # OpenAlex API endpoint
        url = "https://api.openalex.org/works"

        # Search parameters
        params = {"search": title, "per_page": 10, "sort": "relevance_score:desc"}

        headers = {"User-Agent": "PaperFoundry (mailto:contact@example.com)"}

        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Check if results found
        if not data.get("results"):
            print(f"No results found for: {title}" + (f" ({year})" if year else ""))
            return None

        # Find the best match - prefer exact year match if year was provided
        results = data["results"]
        work = None

        if year:
            try:
                year_int = int(year)
                # First, try to find a work with matching year
                for candidate in results:
                    pub_year = candidate.get("publication_year")
                    if pub_year == year_int:
                        work = candidate
                        break
            except (ValueError, TypeError):
                pass

        # Fall back to first result if no year match found
        if not work:
            work = results[0]

        # Extract authors
        authors = []
        if "authorships" in work:
            for authorship in work["authorships"]:
                author = authorship.get("author", {})
                author_name = author.get("display_name", "")
                if author_name:
                    authors.append(author_name)

        # Extract metadata
        metadata = {
            "title": work.get("title", ""),
            "authors": ", ".join(authors),
            "year": work.get("publication_year", ""),
            "journal": work.get("primary_location", {}).get("source", {}).get("display_name", "") if work.get("primary_location") else "",
            "volume": work.get("biblio", {}).get("volume", "") if work.get("biblio") else "",
            "issue": work.get("biblio", {}).get("issue", "") if work.get("biblio") else "",
            "pages": work.get("biblio", {}).get("first_page", "") if work.get("biblio") else "",
            "doi": work.get("doi", "").replace("https://doi.org/", "") if work.get("doi") else "",
            "url": work.get("primary_location", {}).get("landing_page_url", "") if work.get("primary_location") else "",
            "citation_count": work.get("cited_by_count", 0),
            "publisher": work.get("primary_location", {}).get("source", {}).get("publisher", "") if work.get("primary_location") else "",
        }

        return metadata

    except requests.exceptions.RequestException as e:
        print(f"Network error looking up '{title}': {e}")
        return None
    except Exception as e:
        print(f"Error looking up '{title}': {e}")
        return None


def enrich_references_with_scholar(references: List[Dict], delay: float = 1.0) -> List[Dict]:
    """
    Enrich extracted references with metadata from OpenAlex (covers Scholar-like coverage).

    Args:
        references: List of reference dictionaries from extract().
        delay: Delay in seconds between requests (default 1s to be polite).

    Returns:
        List of references with added metadata from OpenAlex.
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
    argparser = argparse.ArgumentParser(description="Extract references from a PDF document and save to JSON.")
    argparser.add_argument("pdf_path", help="Path to the PDF file to process.")
    argparser.add_argument("-o", "--output", default="output.json", help="Path to save the extracted references JSON.")

    args = argparser.parse_args()

    """Main entry point: extract references and save to JSON."""
    extractor = ReferenceExtractor(args.pdf_path)
    print(extractor.sections)  # Debug: print detected sections
    with open("text.txt", "w", encoding="utf-8") as f:
        f.write(extractor.full_text)
    # references = extractor.extract()

    # # Optionally enrich with Semantic Scholar metadata
    # # Uncomment the line below to enable metadata lookup (uses 3s delay per request)
    # references = enrich_references_with_scholar(references)  # Uses default 3s delay

    # with open(args.output, "w", encoding="utf-8") as f:
    # json.dump(references, f, indent=2, ensure_ascii=False)
    # data = lookup_google_scholar_metadata("A microfacetbased brdf for the accurate and efficient rendering of high-definition specular normal maps", "2020")
    # print(data)


if __name__ == "__main__":
    main()
