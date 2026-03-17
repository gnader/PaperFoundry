"""
PDF text extraction and section detection.

Extracts full text, detects sections, and provides structured access to paper content.
First stage of the analysis pipeline — later stages add keyword extraction and citation checking.
"""

import argparse
import fitz  # PyMuPDF
import json
import re
from keybert import KeyBERT
from typing import Dict, List, Optional, Tuple


class PaperAnalyzer:
    """Extracts text and detects sections from a PDF document."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.document = fitz.open(pdf_path)
        self.full_text = self._extract_text()
        self.sections = self._detect_sections()
        self.section_map = self._build_section_text_map()

    # ========================================================================
    # Text Extraction
    # ========================================================================

    def _extract_text(self) -> str:
        """Extract text from PDF, handling single and multi-column layouts."""
        full_text = ""

        for page in self.document:
            full_text += self._extract_page_text(page) + "\n"

        # Remove hyphenation artifacts from column/justification line breaks
        full_text = re.sub(r"-\n", "", full_text)

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

    # ========================================================================
    # Title Extraction
    # ========================================================================

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

    # ========================================================================
    # Section Detection
    # ========================================================================

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
        """Detect document sections. Tries PDF bookmarks first, falls back to text heuristics."""
        # Strategy 1: PDF bookmarks (table of contents) — most reliable
        toc = self.document.get_toc()
        if toc:
            return self._sections_from_bookmarks(toc)

        # Strategy 2: text-based heuristic detection
        return self._sections_from_text()

    def _sections_from_bookmarks(self, toc: List) -> List[Dict]:
        """Build section list from PDF bookmarks (get_toc() output).

        Each toc entry is [level, title, page_number].
        We only keep top-level sections (level 1).
        """
        section_headers = []
        for level, title, page in toc:
            if level != 1:
                continue
            title = title.strip()
            section_num = self._extract_section_number(title)
            clean_title = self._strip_section_number(title)
            is_refs = self._is_references_section(clean_title)
            is_abs = self._is_abstract_section(clean_title)
            entry = {
                "title": clean_title,
                "text": title,  # original text for locating in full_text
                "number": section_num,
                "page": page - 1,  # bookmarks use 1-based pages
            }
            if is_refs:
                entry["is_references"] = True
            if is_abs:
                entry["is_abstract"] = True
            section_headers.append(entry)
        return section_headers

    def _sections_from_text(self) -> List[Dict]:
        """Detect sections by analyzing text blocks (fallback when no bookmarks)."""
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
        """Remove leading number and optional period from section title."""
        return re.sub(r"^\d+\.?\s+", "", text).strip()

    def _is_likely_section_header(self, text: str, x0: float = None, column_starts: List[float] = None) -> bool:
        """Check if text looks like a section header.

        Matches: "1 Title", "1. Title", "2 Title"
        Rejects: "1.1 Subsection", "2 of 33 Author et al..."
        """
        # Match: digit(s), optional period, then space + title word (capitalized)
        # Reject subsections like "1.1" (digit.digit)
        section_pattern = r"^\d+\.?\s+[A-Z]"

        if not re.match(section_pattern, text):
            return False

        # Reject page headers like "2 of 33" or "10 of 33"
        if re.match(r"^\d+\s+of\s+\d+", text):
            return False

        # Reject subsection numbers like "1.1 Title"
        if re.match(r"^\d+\.\d+", text):
            return False

        # Reject pseudocode/algorithm lines (contain ← or other non-prose symbols)
        if "←" in text or "≤" in text:
            return False

        if x0 is not None and column_starts:
            tolerance = 5  # points — section headers may be slightly indented vs body text
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

        # Author-year style: use author last name as search term
        if authors:
            last_name = authors.split()[-1].rstrip(".,") if " " in authors else authors.rstrip(".,")
            if last_name:
                return last_name

        return None

    # ========================================================================
    # Summary
    # ========================================================================

    def summary(self) -> Dict:
        """Return a quick overview of the paper."""
        return {
            "title": self.extract_title(),
            "keywords": self.extract_keywords(),
            "sections": [{"title": s["title"], "page": s["page"] + 1} for s in self.sections],
            "text_length": len(self.full_text),
        }


def main():
    parser = argparse.ArgumentParser(description="Extract text and sections from a PDF.")
    parser.add_argument("pdf", nargs="+", help="Path(s) to PDF file(s)")
    parser.add_argument("-o", "--output", help="Save structured output to JSON file")
    parser.add_argument("--debug-text", action="store_true", help="Dump full extracted text")
    parser.add_argument("--check-cite", help="Check if a paper (by title) is cited")
    parser.add_argument("--check-author", help="Author last name (used with --check-cite)")
    parser.add_argument("--check-cited", help="Check multiple papers from a JSON file")
    args = parser.parse_args()

    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    results = []
    for pdf_path in args.pdf:
        analyzer = PaperAnalyzer(pdf_path)

        if args.debug_text:
            print(f"=== {pdf_path} ===")
            print(analyzer.full_text)
            print()
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
