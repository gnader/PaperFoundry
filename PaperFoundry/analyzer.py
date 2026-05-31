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
from typing import Dict, Optional

try:
    import pypdf
except ImportError:
    raise ImportError("pypdf is required: pip install pypdf")

from .prompt import PromptLibrary

_DEFAULT_MAX_CHARS = 8_000


class PaperAnalyzer:
    """Analyzes a PDF paper using a local LLM.

    Extracts text with `pypdf`, truncates to `max_chars` characters from the
    start (abstract + intro carry the most signal), then sends to the LLM via
    the `analyze` prompt.
    """

    def __init__(self, llm, prompts_dir: Optional[Path] = None, max_chars: int = _DEFAULT_MAX_CHARS):
        self.llm = llm
        self.max_chars = max_chars
        self.prompt = PromptLibrary(prompts_dir).load("analyze")

    def extract_text(self, pdf_path: str) -> str:
        """Extract plain text from a PDF, truncated to self.max_chars from the start."""
        reader = pypdf.PdfReader(pdf_path)
        pages = []
        total_chars = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
            total_chars += len(page_text)

        full_text = "\n\n".join(pages)
        if len(full_text) > self.max_chars:
            full_text = full_text[: self.max_chars] + f"\n\n[truncated — {total_chars} chars total]"

        return full_text

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
    from .llm import LLMClient

    test_dir = Path(__file__).parent.parent / "test"
    pdfs = sorted(test_dir.glob("*.pdf"))

    if not pdfs:
        print(f"No PDFs found in {test_dir}")
        sys.exit(1)

    # Use only the first PDF unless one is passed as an argument
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1])
    else:
        pdf_path = pdfs[0]
        print(f"No PDF specified — using first found: {pdf_path.name}")
        print(f"Available: {[p.name for p in pdfs]}\n")

    model = sys.argv[2] if len(sys.argv) > 2 else "gemma4:e2b"

    print(f"Model : {model}")
    print(f"Paper : {pdf_path.name}")
    print("-" * 60)

    client = LLMClient(model=model)
    client.load(keep_alive="10m")

    try:
        analyzer = PaperAnalyzer(llm=client)
        text = analyzer.extract_text(str(pdf_path))
        bound = analyzer.prompt.render(text=text)
        print(f"Text sent: {len(text)} chars")
        raw = client.generate(prompt=bound["user"], system=bound["system"], format="json")
        print(f"\n[raw LLM response]\n{raw}\n")
        result = analyzer.parse_response(raw)
    finally:
        client.unload()

    print("-" * 60)
    for key, value in result.items():
        print(f"\n[{key}]\n{value}")
