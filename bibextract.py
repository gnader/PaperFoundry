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
from typing import Dict, List, Optional, Tuple


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
        self.section_map = self._build_section_text_map()  # title -> (start, end) in full_text

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
    # Title Extraction
    # ============================================================================

    def extract_title(self) -> str:
        """Extract the paper title from the PDF.

        Strategy:
        1. PDF metadata (fast, works when the PDF was exported with metadata).
        2. First-page largest-font span — the title is usually the biggest text
           on the first page, located in the top half.
        """
        # 1. Try PDF metadata
        metadata = self.document.metadata
        if metadata.get("title", "").strip():
            return metadata["title"].strip()

        # 2. Scan first-page spans; pick the largest font in the top 60% of the page
        first_page = self.document[0]
        page_height = first_page.rect.height
        cutoff_y = page_height * 0.6

        candidates: List[Tuple[float, str]] = []  # (font_size, text)
        page_dict = first_page.get_text("dict")
        for block in page_dict.get("blocks", []):
            if "lines" not in block:
                continue
            block_y = block["bbox"][1]
            if block_y > cutoff_y:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    size = span["size"]
                    if 15 <= len(text) <= 300 and size > 0:
                        candidates.append((size, text))

        if not candidates:
            return ""

        # Group spans by font size; merge adjacent spans at the same (largest) size
        max_size = max(s for s, _ in candidates)
        title_spans = [t for s, t in candidates if abs(s - max_size) < 0.5]
        return " ".join(title_spans).strip()

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

    def _detect_sections(self) -> List[Dict]:
        """Detect document sections by looping over pages and analyzing text blocks."""
        section_headers = []

        # First pass: detect column starting positions
        column_starts = self._detect_column_starts()

        # Second pass: identify section headers based on text content and position
        for page_num, page in enumerate(self.document):
            blocks = page.get_text("blocks")
            for block in blocks:
                if len(block) >= 5:
                    x0, y0, x1, y1, text = block[:5]
                    text = text.replace("\n", " ").strip()

                    if not text:
                        continue

                    if 5 < len(text) < 100:
                        if self._is_likely_section_header(text, x0, column_starts):
                            section_num = self._extract_section_number(text)
                            clean_title = self._strip_section_number(text)
                            section_headers.append({
                                "title": clean_title,
                                "text": text,  # original block text (with number) for locating in full_text
                                "number": section_num,
                                "page": page_num,
                                "bbox": (x0, y0, x1, y1),
                            })
                        elif self._is_abstract_section(text):
                            section_headers.append({
                                "title": "Abstract",
                                "text": text,
                                "number": None,
                                "is_abstract": True,
                                "page": page_num,
                                "bbox": (x0, y0, x1, y1),
                            })
                        elif self._is_references_section(text):
                            section_headers.append({
                                "title": "References",
                                "text": text,
                                "number": None,
                                "is_references": True,
                                "page": page_num,
                                "bbox": (x0, y0, x1, y1),
                            })

        return section_headers

    def _build_section_text_map(self) -> Dict[str, Tuple[int, Optional[int]]]:
        """Build mapping from section title -> (start, end) char positions in full_text."""
        sections_with_pos = []

        for section in self.sections:
            # Search using original block text first (e.g., "1 Introduction")
            search_text = section.get("text", section["title"])
            pos = self.full_text.find(search_text)

            # Fallback: search for clean title alone
            if pos == -1:
                pos = self.full_text.find(section["title"])

            if pos != -1:
                sections_with_pos.append((section["title"], pos))

        # Sort by position, deduplicate (keep first occurrence of each title)
        sections_with_pos.sort(key=lambda x: x[1])
        seen: set = set()
        unique: List[Tuple[str, int]] = []
        for title, pos in sections_with_pos:
            if title not in seen:
                seen.add(title)
                unique.append((title, pos))

        # Map each section to its (start, end) span in full_text
        result: Dict[str, Tuple[int, Optional[int]]] = {}
        for i, (title, start) in enumerate(unique):
            end = unique[i + 1][1] if i + 1 < len(unique) else None
            result[title] = (start, end)

        return result

    def get_section_text(self, section_name: str) -> str:
        """Return the body text of the named section (header line excluded)."""
        # Case-insensitive lookup
        matched_key = None
        if section_name in self.section_map:
            matched_key = section_name
        else:
            for key in self.section_map:
                if key.lower() == section_name.lower():
                    matched_key = key
                    break

        if matched_key is None:
            return ""

        start, end = self.section_map[matched_key]
        text = self.full_text[start:end] if end is not None else self.full_text[start:]

        # Drop the first line (the section header itself)
        lines = text.split("\n")
        return "\n".join(lines[1:]).strip()

    def get_abstract(self) -> str:
        """Extract the abstract text."""
        return self.get_section_text("Abstract")

    def get_introduction(self) -> str:
        """Extract the introduction text."""
        return self.get_section_text("Introduction")

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
        """Extract, parse, and rank all references from the PDF document.

        Returns references sorted by importance_score (descending) so the most
        relevant papers appear first.
        """
        ref_section = self._find_references_section()
        if not ref_section:
            raise ValueError("No references section detected.")

        raw_refs = self._split_references(ref_section)
        parsed_refs = [self._parse_reference(ref) for ref in raw_refs]
        enriched_refs = self._enrich_with_citation_locations(parsed_refs)

        return sorted(enriched_refs, key=lambda r: r.get("importance_score", 0), reverse=True)

    def _refs_start_position(self) -> int:
        """Return the character position in full_text where the references section begins."""
        # Prefer the section_map entry for References
        if "References" in self.section_map:
            return self.section_map["References"][0]

        # Fallback: regex search (same patterns as _find_references_section)
        for pattern in (r"\nReferences\n", r"\nREFERENCES\n", r"\nBibliography\n"):
            m = re.search(pattern, self.full_text)
            if m:
                return m.start()

        return len(self.full_text)

    def _enrich_with_citation_locations(self, references: List[Dict]) -> List[Dict]:
        """Add citation location and importance scoring to references."""
        refs_pos = self._refs_start_position()
        text_before_refs = self.full_text[:refs_pos]

        for ref in references:
            tag = ref.get("tag")
            if not tag:
                ref["cited_in_sections"] = []
                ref["importance_score"] = 0
                continue

            cited_sections = self._find_citation_in_sections(tag, text_before_refs)
            ref["cited_in_sections"] = list(cited_sections.keys())
            ref["section_citations"] = cited_sections
            ref["importance_score"] = self._calculate_importance_score(cited_sections)

        return references

    def _find_citation_in_sections(self, tag: str, text: str) -> Dict[str, int]:
        """Find which sections cite this reference tag, using section_map."""
        cited_sections: Dict[str, int] = {}
        tag_pattern = re.escape(tag).replace(r"\ ", r"\s")

        for section_name, (start, end) in self.section_map.items():
            section_text = text[start:end] if end is not None else text[start:]
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
    # Top-level Analysis
    # ============================================================================

    def analyze(self, top_n: int = 10) -> Dict:
        """Run the full analysis pipeline and return a structured result.

        Returns:
            {
                "title":        str,
                "abstract":     str,
                "introduction": str,
                "sections":     [str, ...],          # detected section titles
                "references":   [...],               # all refs, sorted by importance
                "top_references": [...],             # top_n most important refs
            }
        """
        references = self.extract()
        return {
            "title": self.extract_title(),
            "abstract": self.get_abstract(),
            "introduction": self.get_introduction(),
            "sections": list(self.section_map.keys()),
            "references": references,
            "top_references": references[:top_n],
        }


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
    argparser = argparse.ArgumentParser(
        description="Analyse a research PDF: extract title, abstract, introduction, and ranked references."
    )
    argparser.add_argument("pdf_path", help="Path to the PDF file to process.")
    argparser.add_argument("-o", "--output", default="output.json", help="Path to save the JSON output.")
    argparser.add_argument("--top", type=int, default=10, help="Number of top references to highlight (default: 10).")
    argparser.add_argument("--enrich", action="store_true", help="Fetch OpenAlex metadata for each reference (slow).")
    argparser.add_argument("--debug-text", action="store_true", help="Dump extracted full text to text.txt for debugging.")
    args = argparser.parse_args()

    extractor = ReferenceExtractor(args.pdf_path)

    if args.debug_text:
        with open("text.txt", "w", encoding="utf-8") as f:
            f.write(extractor.full_text)
        print("Full text written to text.txt")

    result = extractor.analyze(top_n=args.top)

    if args.enrich:
        print(f"Enriching {len(result['references'])} references via OpenAlex...")
        result["references"] = enrich_references_with_scholar(result["references"])
        result["top_references"] = result["references"][: args.top]

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Print a summary to stdout
    print(f"\nTitle:    {result['title']}")
    print(f"Sections: {', '.join(result['sections'])}")
    print(f"\nAbstract ({len(result['abstract'])} chars):\n{result['abstract'][:300]}...")
    print(f"\nTop {args.top} most-cited references:")
    for i, ref in enumerate(result["top_references"], 1):
        score = ref.get("importance_score", 0)
        title = ref.get("title", "(unknown title)")
        year = ref.get("year", "")
        print(f"  {i:2}. [{score:5.1f}] {title[:80]} ({year})")

    print(f"\nFull output saved to {args.output}")


if __name__ == "__main__":
    main()
