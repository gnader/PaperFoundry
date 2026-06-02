"""Paper analysis module.

Extracts text from a local PDF and runs it through the ``analyze_paper``
pipeline: section detection → section classification → structured analysis.

The pipeline is defined in ``pipelines/analyze_paper.toml`` at the repo root
and executed by :class:`Pipeline`. Intermediate results are cached per-step as
JSON files so any step can be re-run without repeating earlier work.

Usage:
    from PaperFoundry import LLMClient, PaperAnalyzer
    from pathlib import Path

    client = LLMClient(model="gemma4:e2b")
    client.load(keep_alive="30m")

    analyzer = PaperAnalyzer(llm=client, cache_dir=Path(".papertrack_cache"))
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

from .pipeline import Pipeline

_DEFAULT_PIPELINE = Path(__file__).parent.parent / "pipelines" / "analyze_paper.toml"


class PaperAnalyzer:
    """Analyzes a PDF paper using a local LLM via a declarative pipeline.

    The pipeline (``analyze_paper.toml`` by default) runs three steps:
    1. ``detect_sections`` — identify section headers in the text.
    2. ``classify_sections`` — select which headers are most relevant.
    3. ``analyze`` — produce tldr / what_it_does / what_it_improves.

    Intermediate results are cached per-step in ``cache_dir`` so re-runs
    skip steps whose outputs are already stored.
    """

    def __init__(
        self,
        llm,
        prompts_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        pipeline_path: Optional[Path] = None,
        chunk_pages: int = 2,  # kept for API compatibility; unused
        max_chars: Optional[int] = None,  # kept for API compatibility; set on LLMClient instead
    ):
        self.llm = llm
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.pipeline = Pipeline.load(
            pipeline_path or _DEFAULT_PIPELINE,
            prompts_dir=prompts_dir,
        )

    def extract_text(self, pdf_path: str) -> str:
        """Extract the full raw text from a PDF."""
        reader = pypdf.PdfReader(pdf_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)

    def parse_response(self, raw: str) -> dict:
        """Parse a raw LLM JSON response into the standard analysis dict.

        Kept for backwards compatibility. Returns dict with keys
        tldr / what_it_does / what_it_improves (empty strings on error).
        """
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
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

    def analyze(
        self,
        pdf_path: str,
        run_id: Optional[str] = None,
        resume_from: Optional[str] = None,
        verbose: bool = False,
    ) -> Dict[str, str]:
        """Extract text from a PDF and return a structured LLM analysis.

        Args:
            pdf_path: Path to the PDF file.
            run_id: Stable cache identifier. Defaults to the PDF's stem.
            resume_from: Pipeline step name to force-rerun from (e.g.
                ``"classify_sections"``). Prior steps are loaded from cache.
            verbose: Print step-by-step pipeline progress.

        Returns:
            Dict with keys: tldr, what_it_does, what_it_improves.
        """
        text = self.extract_text(pdf_path)
        rid = run_id or Path(pdf_path).stem
        ctx = self.pipeline.run(
            self.llm,
            initial_context={"raw_text": text},
            cache_dir=self.cache_dir,
            run_id=rid,
            resume_from=resume_from,
            verbose=verbose,
        )
        return {
            "tldr": str(ctx.get("analyze.tldr", "")).strip(),
            "what_it_does": str(ctx.get("analyze.what_it_does", "")).strip(),
            "what_it_improves": str(ctx.get("analyze.what_it_improves", "")).strip(),
        }


if __name__ == "__main__":
    # Usage:
    #   python -m PaperFoundry.analyzer [pdf] [model]
    #   python -m PaperFoundry.analyzer [pdf] [model] --verbose
    #   python -m PaperFoundry.analyzer [pdf] [model] --resume-from classify_sections
    from .llm import LLMClient

    test_dir = Path(__file__).parent.parent / "test"
    pdfs = sorted(test_dir.glob("*.pdf"))

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    verbose = "--verbose" in flags

    resume_from = None
    for flag in flags:
        if flag.startswith("--resume-from="):
            resume_from = flag.split("=", 1)[1]

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
    max_chars = int(args[2]) if len(args) > 2 else None
    cache_dir = Path(".papertrack_cache")

    print(f"Model    : {model}")
    print(f"Max chars: {max_chars or 'unlimited (no pagination)'}")
    print(f"Paper    : {pdf_path.name}")
    print(f"Cache    : {cache_dir / 'pipelines' / 'analyze_paper' / pdf_path.stem}")
    if resume_from:
        print(f"Resume   : from step {resume_from!r}")
    print("-" * 60)

    reader = pypdf.PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    raw_chars = sum(len(p) for p in pages)
    print(f"Pages    : {len(pages)}  |  Raw chars: {raw_chars:,}")

    client = LLMClient(model=model, max_chars=max_chars)
    client.load(keep_alive="10m")

    try:
        analyzer = PaperAnalyzer(llm=client, cache_dir=cache_dir)
        result = analyzer.analyze(
            str(pdf_path),
            resume_from=resume_from,
            verbose=True,
        )
        print("-" * 60)
        for key, value in result.items():
            print(f"\n[{key}]\n{value}")
    finally:
        client.unload()
