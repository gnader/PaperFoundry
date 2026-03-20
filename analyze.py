"""
PDF text extraction and section detection.

Extracts full text, detects sections, and provides structured access to paper content.
Uses marker-pdf (ML-based PDF→Markdown) for layout-aware text extraction.
First stage of the analysis pipeline — later stages add keyword extraction and citation checking.
"""

import argparse
import json
import re
from keybert import KeyBERT
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from pypdf import PdfReader
from typing import Dict, List, Optional, Tuple


# ============================================================================
# Model caching — marker models are expensive to load
# ============================================================================

_cached_model_dict = None


def get_model_dict():
    """Return cached marker model dict, loading on first call."""
    global _cached_model_dict
    if _cached_model_dict is None:
        _cached_model_dict = create_model_dict()
    return _cached_model_dict


class PaperAnalyzer:
    """Extracts text and detects sections from a PDF document."""

    def __init__(self, pdf_path: str, model_dict=None):
        self.pdf_path = pdf_path

        # Run marker-pdf conversion
        if model_dict is None:
            model_dict = get_model_dict()
        converter = PdfConverter(artifact_dict=model_dict)
        rendered = converter(pdf_path)
        self.markdown = rendered.markdown
        self._metadata = rendered.metadata

        # Derive plain text and sections
        self.full_text = self._strip_markdown(self.markdown)
        self.sections = self._detect_sections()
        self.section_map = self._build_section_text_map()

    def close(self):
        """No-op for backward compatibility with callers that close the document."""
        pass

    # ========================================================================
    # Markdown → Plain Text
    # ========================================================================

    def _strip_markdown(self, md: str) -> str:
        """Convert marker markdown to plain text."""
        text = md
        # Remove HTML tags (marker-pdf sometimes emits <span>, <sup>, <sub>, etc.)
        text = re.sub(r'<[^>]+>', '', text)
        # Remove image references ![alt](path)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        # Convert links [text](url) → text
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        # Remove heading prefixes (keep heading text)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove bold/italic markers (skip backslash-escaped markers like \*)
        text = re.sub(r"(?<!\\)\*{1,3}([^*\n]+?)(?<!\\)\*{1,3}", r"\1", text)
        text = re.sub(r"(?<!\\)_{1,3}([^_\n]+?)(?<!\\)_{1,3}", r"\1", text)
        # Unescape markdown backslash escapes (\* → *, \_ → _, etc.)
        text = re.sub(r'\\([*_\\~`#\[\](){}+\-.!|])', r'\1', text)
        # Strip inline LaTeX delimiters
        text = re.sub(r"\$([^$]+)\$", r"\1", text)
        # Strip display LaTeX delimiters
        text = re.sub(r"\$\$([^$]+)\$\$", r"\1", text)
        # Remove horizontal rules
        text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
        # Collapse runs of 3+ newlines into 2
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ========================================================================
    # Title Extraction
    # ========================================================================

    def extract_title(self) -> str:
        """Extract the paper title from the PDF.

        Strategy:
        1. pypdf metadata (fast, works when the PDF was exported with metadata).
        2. First heading from marker markdown output.
        """
        # 1. Try pypdf metadata
        try:
            reader = PdfReader(self.pdf_path)
            meta_title = reader.metadata.title if reader.metadata else None
            if meta_title and meta_title.strip():
                return meta_title.strip()
        except Exception:
            pass

        # 2. First heading from markdown
        match = re.search(r"^#{1,2}\s+(.+)$", self.markdown, re.MULTILINE)
        if match:
            return match.group(1).strip()

        return ""

    # ========================================================================
    # Section Detection
    # ========================================================================

    _NON_SECTION_RE = re.compile(
        r"^(Fig(ure|\.)?|Table|Algorithm|Listing|Appendix)\s+\d",
        re.IGNORECASE,
    )

    def _is_real_section(self, title: str) -> bool:
        """Check if a heading is a real document section (not a caption or title)."""
        stripped = title.strip()
        if not stripped:
            return False
        # Reject figure/table/algorithm captions
        if self._NON_SECTION_RE.match(stripped):
            return False
        # Accept numbered sections: "1 Introduction", "2. Methods"
        if re.match(r"^\d+\.?\s+[A-Z]", stripped):
            return True
        # Accept known unnumbered sections
        lower = self._strip_section_number(stripped).lower()
        if lower in ("abstract", "introduction", "conclusion", "conclusions",
                      "discussion", "acknowledgements", "acknowledgments",
                      "references", "bibliography", "works cited",
                      "related work", "background", "methods", "methodology",
                      "results", "evaluation", "experiments", "overview",
                      "limitations", "future work", "supplementary material"):
            return True
        if lower.startswith("appendix"):
            return True
        return False

    def _detect_sections(self) -> List[Dict]:
        """Detect document sections from marker metadata or markdown headings.

        Only keeps top-level numbered sections and known unnumbered sections
        (Abstract, References, etc.). Skips the paper title, subsections,
        and non-section headings (Algorithm, Figure, Table captions).
        """
        sections = []

        # Strategy 1: marker's table_of_contents metadata
        toc = self._metadata.get("table_of_contents") if isinstance(self._metadata, dict) else None
        if toc and isinstance(toc, list) and len(toc) > 0:
            # Find the most common heading level among numbered sections
            # to determine which level represents top-level sections
            levels_of_numbered = []
            for entry in toc:
                title = entry.get("title", "").strip()
                level = entry.get("heading_level") or entry.get("level") or 1
                if re.match(r"^\d+\.?\s+[A-Z]", title):
                    levels_of_numbered.append(level)
            section_level = min(levels_of_numbered) if levels_of_numbered else 1

            for entry in toc:
                level = entry.get("heading_level") or entry.get("level") or 1
                title = entry.get("title", "").strip()
                if not title:
                    continue
                # Skip deeper headings (subsections)
                if level > section_level and not self._is_references_section(self._strip_section_number(title)) and not self._is_abstract_section(self._strip_section_number(title)):
                    continue
                if not self._is_real_section(title):
                    continue
                section_num = self._extract_section_number(title)
                clean_title = self._strip_section_number(title)
                is_refs = self._is_references_section(clean_title)
                is_abs = self._is_abstract_section(clean_title)
                sect = {
                    "title": clean_title,
                    "text": title,
                    "number": section_num,
                    "page": entry.get("page_id", entry.get("page", 0)),
                }
                if is_refs:
                    sect["is_references"] = True
                if is_abs:
                    sect["is_abstract"] = True
                sections.append(sect)
            if sections:
                return sections

        # Strategy 2: regex on markdown headings
        # First pass: find the heading level used for numbered sections
        heading_levels = []
        for match in re.finditer(r"^(#{1,6})\s+(.+)$", self.markdown, re.MULTILINE):
            level = len(match.group(1))
            title = match.group(2).strip()
            if re.match(r"^\d+\.?\s+[A-Z]", title):
                heading_levels.append(level)
        section_level = min(heading_levels) if heading_levels else 2

        for match in re.finditer(r"^(#{1,6})\s+(.+)$", self.markdown, re.MULTILINE):
            level = len(match.group(1))
            title = match.group(2).strip()
            # Skip deeper headings (subsections)
            if level > section_level and not self._is_references_section(self._strip_section_number(title)) and not self._is_abstract_section(self._strip_section_number(title)):
                continue
            if not self._is_real_section(title):
                continue
            section_num = self._extract_section_number(title)
            clean_title = self._strip_section_number(title)
            is_refs = self._is_references_section(clean_title)
            is_abs = self._is_abstract_section(clean_title)
            sect = {
                "title": clean_title,
                "text": title,
                "number": section_num,
                "page": 0,
            }
            if is_refs:
                sect["is_references"] = True
            if is_abs:
                sect["is_abstract"] = True
            sections.append(sect)

        return sections

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
        """Remove leading number and optional period from section title."""
        return re.sub(r"^\d+\.?\s+", "", text).strip()

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

    # ========================================================================
    # Keyword Extraction
    # ========================================================================

    def extract_keywords(self, top_n: int = 20) -> List[str]:
        """Extract keywords from the introduction using KeyBERT.

        Uses sentence-transformers embeddings to find keywords semantically
        close to the document. Returns keywords sorted by relevance.
        """
        intro = self.get_introduction()
        if not intro:
            intro = self.get_abstract()
        if not intro:
            return []

        kw_model = KeyBERT()
        keywords = kw_model.extract_keywords(
            intro,
            keyphrase_ngram_range=(1, 1),
            stop_words="english",
            use_mmr=True,       # maximal marginal relevance for diversity
            diversity=0.5,
            top_n=top_n,
        )
        return [(kw, round(score, 3)) for kw, score in keywords]

    # ========================================================================
    # Citation Checking
    # ========================================================================

    def is_cited(self, papers: List[Dict]) -> List[Dict]:
        """Check whether specific papers are cited in this PDF and in which sections.

        Args:
            papers: list of {"title": str, "authors": str} dicts

        Returns:
            list of {"title", "authors", "cited", "key", "sections"} dicts
        """
        refs_text = self.get_section_text("References")
        ref_entries = self._split_reference_entries(refs_text) if refs_text else []

        results = []
        for paper in papers:
            title = paper.get("title", "")
            authors = paper.get("authors", "")
            result = {"title": title, "authors": authors, "cited": False, "key": None, "sections": []}

            # Find matching reference entry
            matched_entry = self._find_reference_entry(ref_entries, title, authors)
            if matched_entry is None:
                results.append(result)
                continue

            # Extract citation key
            key = self._extract_citation_key(matched_entry, authors)
            if key is None:
                results.append(result)
                continue

            result["cited"] = True
            result["key"] = key

            # Search each section for the key
            for section_title, (start, end) in self.section_map.items():
                section_text = self.full_text[start:end] if end else self.full_text[start:]
                if key in section_text:
                    result["sections"].append(section_title)

            # Remove the References section itself from results
            result["sections"] = [s for s in result["sections"]
                                  if s.lower() not in ("references", "bibliography", "works cited")]

            results.append(result)
        return results

    def _split_reference_entries(self, refs_text: str) -> List[str]:
        """Split references section text into individual entries."""
        # Try splitting on bullet-list markers (marker-pdf often formats refs as "- ")
        bullet_split = re.split(r'\n(?=- )', refs_text)
        entries = [e.strip().lstrip('- ').strip() for e in bullet_split if e.strip()]
        if len(entries) > 1:
            return entries

        # Try splitting on bracket patterns like [1], [2], [WAF23]
        bracket_split = re.split(r'(?=\[\S+?\])', refs_text)
        entries = [e.strip() for e in bracket_split if e.strip()]
        if len(entries) > 1:
            return entries

        # Fallback: split on blank lines or numbered lines
        line_split = re.split(r'\n\s*\n', refs_text)
        entries = [e.strip() for e in line_split if e.strip()]
        if len(entries) > 1:
            return entries

        # Last resort: each line is an entry
        return [line.strip() for line in refs_text.split('\n') if line.strip()]

    def _normalize_for_matching(self, text: str) -> str:
        """Normalize text for fuzzy matching: lowercase, collapse hyphens/whitespace."""
        text = text.lower()
        text = re.sub(r'[-\u2010-\u2015]', '', text)  # remove hyphens/dashes
        text = re.sub(r'\s+', ' ', text)               # collapse whitespace
        return text

    def _find_reference_entry(self, entries: List[str], title: str, authors: str) -> Optional[str]:
        """Find the reference entry matching a paper by title or author."""
        # Try title match first (more specific)
        if title:
            title_norm = self._normalize_for_matching(title)
            for entry in entries:
                if title_norm in self._normalize_for_matching(entry):
                    return entry

        # Fall back to author match
        if authors:
            # Extract last name (handle "Smith" or "John Smith" or "Smith, J.")
            last_name = authors.split()[-1].rstrip(".,") if " " in authors else authors.rstrip(".,")
            last_lower = last_name.lower()
            for entry in entries:
                if last_lower in entry.lower():
                    return entry

        return None

    def _extract_citation_key(self, entry: str, authors: str) -> Optional[str]:
        """Extract the citation key from a reference entry.

        Returns bracket key like "[1]" or "[WAF23]", or author last name for
        author-year style citations.
        """
        # Try bracket key: [1], [23], [WAF23], [Del*23], etc.
        match = re.match(r'(\[[^\]]+\])', entry)
        if match:
            return match.group(1)

        # Author-year style: use first author's last name as search term
        if authors:
            first_author = authors.split(",")[0].strip()
            if first_author and len(first_author) > 1:
                return first_author

        return None

    # ========================================================================
    # Reference Extraction
    # ========================================================================

    def extract_references(self, top_n: int = None) -> List[Dict]:
        """Extract all references as structured data, scored by citation importance.

        Returns list of dicts: {raw, tag, authors, title, year, sections, importance_score}
        sorted by importance_score descending.
        """
        refs_text = self.get_section_text("References")
        if not refs_text:
            return []

        entries = self._split_reference_entries(refs_text)
        refs = [self._parse_reference(entry) for entry in entries]
        refs = [r for r in refs if r is not None]
        refs = self._enrich_with_citations(refs)
        refs.sort(key=lambda r: r["importance_score"], reverse=True)

        if top_n is not None:
            refs = refs[:top_n]
        return refs

    def _parse_reference(self, entry: str) -> Optional[Dict]:
        """Parse a single reference entry into structured fields."""
        entry = re.sub(r'\s+', ' ', entry).strip()
        if not entry:
            return None

        # Reject entries too short to be a real reference
        if len(entry) < 20:
            return None

        # Reject entries that look like equations (high density of LaTeX chars)
        latex_chars = sum(1 for c in entry if c in r'\{}^_=')
        if len(entry) > 0 and latex_chars / len(entry) > 0.1:
            return None

        # Extract bracket tag if present
        tag = None
        remainder = entry
        m = re.match(r'(\[[^\]]+\])', entry)
        if m:
            tag = m.group(1)
            remainder = entry[m.end():].strip().lstrip(".,").strip()

        year = self._extract_year(entry)

        # Reject entries with no year AND no bracket tag (likely not a reference)
        if year is None and tag is None:
            return None

        # Parse authors and title from the remainder
        authors, title = self._parse_author_title(remainder, year)

        # If no bracket tag, try author-name fallback for citation key
        if tag is None and authors:
            tag = self._extract_citation_key(entry, authors)

        return {
            "raw": entry,
            "tag": tag,
            "authors": authors,
            "title": title,
            "year": year,
        }

    def _extract_year(self, text: str) -> Optional[str]:
        """Find a 4-digit year (19xx or 20xx) in text."""
        # Prefer years in parentheses like (2023) or at end of string
        m = re.search(r'\(?((?:19|20)\d{2})\)?', text)
        return m.group(1) if m else None

    def _parse_author_title(self, text: str, year: Optional[str]) -> Tuple[str, str]:
        """Split reference text into authors and title.

        Heuristic: authors come first, separated from title by a period or
        a quoted/italic title boundary. The title is typically the first
        sentence-like segment after the author block.
        """
        # Strategy 1: Colon-split (Eurographics style "AUTHOR X.: Title")
        # Remove year first for cleaner title extraction
        clean = text
        if year:
            clean = re.sub(r'\(?' + re.escape(year) + r'\)?,?\s*', '', clean, count=1)
        colon_parts = re.split(r':\s+(?=[A-Z])', clean, maxsplit=1)
        if len(colon_parts) >= 2:
            authors = colon_parts[0].strip().rstrip(":.")
            title_rest = colon_parts[1].strip()
            title_match = re.match(r'["\u201c]?(.+?)["\u201d]?(?:\.|$)', title_rest)
            title = title_match.group(1).strip(' ""\u201c\u201d') if title_match else title_rest.split(".")[0].strip()
            return authors, title

        # Strategy 2: Year-delimited (ACM style "AUTHORS. YEAR. Title. Venue.")
        # Split at the year when it follows a period or comma (not embedded in venue)
        if year:
            m = re.search(r'[.,]\s*\(?' + re.escape(year) + r'\)?\.?\s+', text)
            if m:
                authors = text[:m.start()].strip().rstrip('.,')
                title_rest = text[m.end():].strip()
                if title_rest:
                    title = title_rest.split(".")[0].strip()
                    if title and len(title) > 2:
                        return authors, title

        # Strategy 3: Period-split with word-length guard — only split on ". "
        # when the next word is ≥3 lowercase chars (avoids splitting on initials)
        parts = re.split(r'\.\s+(?=[A-Z][a-z]{2,})', clean, maxsplit=1)
        if len(parts) >= 2:
            authors = parts[0].strip().rstrip(".")
            title_rest = parts[1].strip()
            title_match = re.match(r'["\u201c]?(.+?)["\u201d]?(?:\.|$)', title_rest)
            title = title_match.group(1).strip(' ""\u201c\u201d') if title_match else title_rest.split(".")[0].strip()
            return authors, title

        # Fallback: treat everything before first comma-period as authors
        return clean.split(".")[0].strip(), ""

    def _enrich_with_citations(self, refs: List[Dict]) -> List[Dict]:
        """Add citation locations and importance scores to each reference."""
        for ref in refs:
            tag = ref.get("tag")
            section_counts: Dict[str, int] = {}

            if tag:
                for section_title, (start, end) in self.section_map.items():
                    # Skip references section itself
                    if self._is_references_section(section_title):
                        continue
                    section_text = self.full_text[start:end] if end else self.full_text[start:]
                    # Bracket tags like [1] or [ZK16]: exact substring match
                    # Author-name tags like "DONG": case-insensitive word boundary match
                    if tag.startswith('['):
                        count = section_text.count(tag)
                    else:
                        count = len(re.findall(r'\b' + re.escape(tag) + r'\b', section_text, re.IGNORECASE))
                    if count > 0:
                        section_counts[section_title] = count

            ref["sections"] = list(section_counts.keys())
            ref["citation_counts"] = section_counts
            ref["importance_score"] = self._calculate_importance(section_counts)

        return refs

    def _get_section_weight(self, title: str) -> float:
        """Weight sections by typical importance for citation analysis."""
        lower = title.lower()
        # Methods/results sections are most important
        if any(kw in lower for kw in ("method", "approach", "algorithm", "implementation",
                                       "result", "experiment", "evaluation")):
            return 3.0
        if any(kw in lower for kw in ("related work", "background", "previous")):
            return 2.0
        if any(kw in lower for kw in ("conclusion", "discussion", "future")):
            return 1.5
        # Introduction and other sections
        return 1.0

    def _calculate_importance(self, section_counts: Dict[str, int]) -> float:
        """Calculate importance score from section citation counts."""
        return sum(count * self._get_section_weight(section)
                   for section, count in section_counts.items())

    # ========================================================================
    # Summary
    # ========================================================================

    def summary(self) -> Dict:
        """Return a quick overview of the paper."""
        return {
            "title": self.extract_title(),
            "keywords": self.extract_keywords(),
            "sections": [{"title": s["title"], "page": s.get("page", 0) + 1} for s in self.sections],
            "text_length": len(self.full_text),
        }


def main():
    parser = argparse.ArgumentParser(description="Extract text and sections from a PDF.")
    parser.add_argument("pdf", nargs="+", help="Path(s) to PDF file(s)")
    parser.add_argument("-o", "--output", help="Save structured output to JSON file")
    parser.add_argument("--debug-text", action="store_true", help="Dump full extracted text")
    parser.add_argument("--refs", action="store_true", help="Extract all references with importance scores")
    parser.add_argument("--top", type=int, default=None, help="Limit to top N references (used with --refs)")
    parser.add_argument("--check-cite", help="Check if a paper (by title) is cited")
    parser.add_argument("--check-author", help="Author last name (used with --check-cite)")
    parser.add_argument("--check-cited", help="Check multiple papers from a JSON file")
    args = parser.parse_args()

    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Pre-load models once for all PDFs
    model_dict = get_model_dict()

    results = []
    for pdf_path in args.pdf:
        analyzer = PaperAnalyzer(pdf_path, model_dict=model_dict)

        if args.debug_text:
            print(f"=== {pdf_path} ===")
            print(analyzer.full_text)
            print()
            continue

        # Reference extraction mode
        if args.refs:
            refs = analyzer.extract_references(top_n=args.top)

            if not args.output:
                if len(args.pdf) > 1:
                    print(f"=== {pdf_path} ===")
                print(f"Found {len(refs)} references")
                print()
                for i, ref in enumerate(refs, 1):
                    tag_str = ref["tag"] or "?"
                    authors_str = ref["authors"] or "Unknown"
                    title_str = f' — "{ref["title"]}"' if ref["title"] else ""
                    year_str = f' ({ref["year"]})' if ref["year"] else ""
                    print(f"  {i:>3}. [{ref['importance_score']:>5.1f}] {tag_str:<10} {authors_str}{title_str}{year_str}")
                    if ref["sections"]:
                        print(f"       Cited in: {', '.join(ref['sections'])}")
                print()

            results.append({"file": pdf_path, "references": refs})
            continue

        # Citation checking mode
        if args.check_cite or args.check_cited:
            papers = []
            if args.check_cited:
                with open(args.check_cited, "r", encoding="utf-8") as f:
                    papers = json.load(f)
            if args.check_cite:
                papers.append({"title": args.check_cite, "authors": args.check_author or ""})

            cite_results = analyzer.is_cited(papers)
            for cr in cite_results:
                status = "CITED" if cr["cited"] else "NOT FOUND"
                print(f"[{status}] {cr['title']}")
                if cr["cited"]:
                    print(f"  Key: {cr['key']}")
                    print(f"  Sections: {', '.join(cr['sections']) if cr['sections'] else '(references only)'}")
                print()

            if args.output:
                results.append({"file": pdf_path, "citations": cite_results})
            continue

        result = analyzer.summary()
        result["file"] = pdf_path
        results.append(result)

        if not args.output:
            if len(args.pdf) > 1:
                print(f"=== {pdf_path} ===")
            print(f"Title: {result['title']}")
            print(f"Text length: {result['text_length']} chars")
            print()
            if result["keywords"]:
                print("Keywords: " + ", ".join(f"{kw} ({score})" for kw, score in result["keywords"]))
                print()
            print("Sections:")
            for s in result["sections"]:
                print(f"  p{s['page']:>2}  {s['title']}")
            print()

    if args.output and results:
        output = results if len(results) > 1 else results[0]
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
