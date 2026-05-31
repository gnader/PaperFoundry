"""Paper analysis module.

Extracts text from a local PDF and sends it to a local LLM (via `LLMClient` /
Ollama) for structured analysis: TLDR, what it does, what it improves over
prior work.

Usage:
    from PaperFoundry import LLMClient, PaperAnalyzer

    client = LLMClient(model="gemma4:e2b")
    client.load(keep_alive="30m")

    analyzer = PaperAnalyzer(llm=client)
    text = analyzer.extract_text("paper.pdf")
    result = analyzer.analyze("paper.pdf")
    # result: {"tldr": ..., "what_it_does": ..., "what_it_improves": ...}

    client.unload()
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import pypdf
except ImportError:
    raise ImportError("pypdf is required: pip install pypdf")

from .prompt import PromptLibrary

_DEFAULT_MAX_CHARS = 8_000


class SectionExtractor:
    """Detects section boundaries in PDF pages using paginated LLM calls, then
    extracts and concatenates sections in order up to a character budget.

    Pass 1 — detection: pages are fed in chunks of `chunk_pages` to the LLM
    with the `section_detect` prompt; each call returns a JSON array of header
    strings as they appear in the text.

    Pass 2 — extraction: each header is located in the full text via
    case-insensitive string search; text is sliced between adjacent boundaries
    and concatenated until `max_chars` is reached.

    Fallback: if fewer than 2 headers are detected, head-truncation is used.
    """

    def __init__(self, llm, chunk_pages: int = 2, prompts_dir: Optional[Path] = None, verbose: bool = False):
        self.llm = llm
        self.chunk_pages = chunk_pages
        self.verbose = verbose
        self.prompt = PromptLibrary(prompts_dir).load("section_detect")

    def _parse_headers(self, raw: str) -> List[str]:
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        # Accept {"sections": [...]} (preferred) or bare [...]
        if isinstance(data, dict):
            data = data.get("sections", [])
        if not isinstance(data, list):
            return []
        return [str(h).strip() for h in data if str(h).strip()]

    def detect_headers(self, pages: List[str]) -> List[str]:
        """Return all section header strings found across all page chunks, in order."""
        seen: set = set()
        headers: List[str] = []
        for i in range(0, len(pages), self.chunk_pages):
            chunk = "\n\n".join(pages[i : i + self.chunk_pages])
            if not chunk.strip():
                continue
            bound = self.prompt.render(text=chunk)
            raw = self.llm.generate(prompt=bound["user"], system=bound["system"], format="json")
            if self.verbose:
                page_range = f"pages {i + 1}–{min(i + self.chunk_pages, len(pages))}"
                print(f"  [{page_range}] raw: {raw!r}")
            for h in self._parse_headers(raw):
                if h not in seen:
                    seen.add(h)
                    headers.append(h)
        return headers

    def _find_boundaries(self, full_text: str, headers: List[str]) -> List[Tuple[int, str]]:
        """Locate each header in full_text; return sorted (offset, name) pairs."""
        lower = full_text.lower()
        boundaries: List[Tuple[int, str]] = []
        for h in headers:
            pos = lower.find(h.lower())
            if pos == -1:
                m = re.search(r"\b" + re.escape(h.lower()) + r"\b", lower)
                pos = m.start() if m else -1
            if pos != -1:
                boundaries.append((pos, h))
        boundaries.sort(key=lambda x: x[0])
        return boundaries

    def extract(self, pages: List[str], max_chars: int) -> str:
        """Extract sections from pages up to max_chars. Falls back to head-truncation."""
        full_text = "\n\n".join(pages)
        headers = self.detect_headers(pages)
        if len(headers) < 2:
            return full_text[:max_chars]

        boundaries = self._find_boundaries(full_text, headers)
        if len(boundaries) < 2:
            return full_text[:max_chars]

        parts: List[str] = []
        chars = 0
        for i, (pos, _) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(full_text)
            section = full_text[pos:end].strip()
            remaining = max_chars - chars
            if len(section) >= remaining:
                parts.append(section[:remaining])
                break
            parts.append(section)
            chars += len(section)

        return "\n\n".join(parts) if parts else full_text[:max_chars]


class PaperAnalyzer:
    """Analyzes a PDF paper using a local LLM.

    Uses `SectionExtractor` to detect section boundaries via paginated LLM calls,
    then feeds the assembled sections (up to `max_chars`) to the analysis LLM.
    Falls back to head-truncation if section detection yields fewer than 2 headers.
    """

    def __init__(
        self,
        llm,
        prompts_dir: Optional[Path] = None,
        max_chars: int = _DEFAULT_MAX_CHARS,
        chunk_pages: int = 2,
    ):
        self.llm = llm
        self.max_chars = max_chars
        self.section_extractor = SectionExtractor(llm, chunk_pages, prompts_dir)
        self.prompt = PromptLibrary(prompts_dir).load("analyze")

    def extract_text(self, pdf_path: str) -> str:
        """Extract and section-filter text from a PDF, capped at self.max_chars."""
        reader = pypdf.PdfReader(pdf_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return self.section_extractor.extract(pages, self.max_chars)

    def parse_response(self, raw: str) -> dict:
        """Parse the LLM's JSON response. Returns dict with tldr/what_it_does/what_it_improves."""
        text = raw.strip()

        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {
                "tldr": "",
                "what_it_does": "",
                "what_it_improves": "",
                "error": f"Failed to parse LLM response: {raw[:200]}",
            }

        return {
            "tldr": str(data.get("tldr", "")).strip(),
            "what_it_does": str(data.get("what_it_does", "")).strip(),
            "what_it_improves": str(data.get("what_it_improves", "")).strip(),
        }

    def analyze(self, pdf_path: str) -> Dict[str, str]:
        """Extract text from a PDF and return a structured LLM analysis.

        Returns a dict with keys: tldr, what_it_does, what_it_improves.
        """
        text = self.extract_text(pdf_path)
        bound = self.prompt.render(text=text)
        raw = self.llm.generate(prompt=bound["user"], system=bound["system"], format="json")
        return self.parse_response(raw)


if __name__ == "__main__":
    # Usage:
    #   python -m PaperFoundry.analyzer [pdf] [model]            # full analysis
    #   python -m PaperFoundry.analyzer [pdf] [model] --sections # section extractor only
    from .llm import LLMClient

    test_dir = Path(__file__).parent.parent / "test"
    pdfs = sorted(test_dir.glob("*.pdf"))

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    sections_mode = "--sections" in flags

    if not pdfs and not args:
        print(f"No PDFs found in {test_dir}")
        sys.exit(1)

    if args:
        pdf_path = Path(args[0])
    else:
        pdf_path = pdfs[0]
        print(f"No PDF specified — using first found: {pdf_path.name}")
        print(f"Available: {[p.name for p in pdfs]}\n")

    model = args[1] if len(args) > 1 else "gemma4:e2b"

    print(f"Model : {model}")
    print(f"Paper : {pdf_path.name}")
    print(f"Mode  : {'section extraction' if sections_mode else 'full analysis'}")
    print("-" * 60)

    reader = pypdf.PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    print(f"Pages : {len(pages)}  |  Raw chars: {sum(len(p) for p in pages)}")

    client = LLMClient(model=model)
    client.load(keep_alive="10m")

    try:
        if sections_mode:
            extractor = SectionExtractor(llm=client, verbose=True)
            print("\n[detecting headers — paginated]\n")
            headers = extractor.detect_headers(pages)
            print(f"Headers found ({len(headers)}):")
            for h in headers:
                print(f"  • {h!r}")

            print("\n[locating boundaries in full text]\n")
            full_text = "\n\n".join(pages)
            boundaries = extractor._find_boundaries(full_text, headers)
            for pos, name in boundaries:
                print(f"  offset {pos:6d} — {name!r}")

            # print("\n[extracted text]\n")
            # result_text = extractor.extract(pages, _DEFAULT_MAX_CHARS)
            # print(f"Extracted: {len(result_text)} chars")
            # print("-" * 60)
            # print(result_text)
        else:
            analyzer = PaperAnalyzer(llm=client)
            text = analyzer.extract_text(str(pdf_path))
            bound = analyzer.prompt.render(text=text)
            print(f"Text sent: {len(text)} chars")
            raw = client.generate(prompt=bound["user"], system=bound["system"], format="json")
            print(f"\n[raw LLM response]\n{raw}\n")
            result = analyzer.parse_response(raw)
            print("-" * 60)
            for key, value in result.items():
                print(f"\n[{key}]\n{value}")
    finally:
        client.unload()
