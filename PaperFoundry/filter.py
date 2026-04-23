"""Topic-based paper filter using a local LLM.

Reads a papers.json file (produced by monitor.py) and a topics/ directory of markdown files, then
uses a local LLM (via LLMClient / Ollama) to judge whether each paper is relevant to each topic.

Two modes (planned):
  fast — LLM reads title + abstract, returns verdict + confidence + reasoning (this file)
  deep — downloads PDF, extracts full text, LLM does deeper analysis (future DeepFilter)

Usage:
    python filter.py papers.json --model gemma4:e2b
    python filter.py papers.json --model gemma4:e2b --topics topics/ -o filtered.json
    python filter.py papers.json --model gemma4:e2b --paper 2603.11969 --verbose
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .topics import Topic, load_topics

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompts(name: str, prompts_dir: Path = None) -> dict:
    """Load a prompts file with [system] and [user] section headers."""
    path = (prompts_dir or PROMPTS_DIR) / name
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped in ("[system]", "[user]"):
            current = stripped[1:-1]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    for key in ("system", "user"):
        if key not in sections:
            raise ValueError(f"Prompt file {path} missing [{key}] section")
    return {k: "\n".join(v).strip() for k, v in sections.items()}


# ===========================================================================================================================
# I/O helpers
# ===========================================================================================================================


def load_papers(path: str) -> List[dict]:
    """Load papers from a papers.json file produced by monitor.py."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    papers = data.get("papers", [])
    if not papers:
        raise ValueError(f"No papers found in {path!r}")
    return papers


def save_results(results: Dict[str, List[dict]], source_file: str, topics_file: str, path: str) -> None:
    """Write filtered results to a JSON file."""
    total = sum(len(papers) for papers in results.values())
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "topics_file": topics_file,
        "total_matched_papers": total,
        "results": [{"topic": topic_name, "match_count": len(papers), "papers": papers} for topic_name, papers in results.items()],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nSaved results to {path}")


def format_results(results: Dict[str, List[dict]]) -> None:
    """Print a human-readable summary to stdout."""
    if not results:
        print("No papers matched any topic.")
        return

    for topic_name, papers in results.items():
        n_match = sum(1 for p in papers if p.get("match_level") == "match")
        n_maybe = sum(1 for p in papers if p.get("match_level") == "maybe")
        counts = f"{n_match} match"
        if n_maybe:
            counts += f", {n_maybe} maybe"
        print(f"\n{topic_name}  ({counts})")
        for paper in papers:
            date = (paper.get("published") or "")[:10]
            title = paper.get("title", "(no title)")
            url = paper.get("url", "")
            level = paper.get("match_level", "")
            reason = paper.get("reason", "")

            tag = " [MAYBE]" if level == "maybe" else ""

            print(f"  [{date}] {title}{tag}")
            if reason:
                print(f"           {reason}")
            if url:
                print(f"           {url}")


# ===========================================================================================================================
# FastFilter
# ===========================================================================================================================


class FastFilter:
    """LLM-based paper filter using title + abstract.

    For each (topic, paper) pair, builds a prompt with the topic description/keywords and the paper's
    title + abstract, sends it to the LLM, and parses a structured JSON verdict.

    Prompts are loaded from external text files in the prompts/ directory.
    """

    def __init__(self, llm, verbose: bool = False, prompts_dir: Path = None):
        self.llm = llm
        self.verbose = verbose
        prompts = _load_prompts("fast.prompt", prompts_dir)
        self.system_prompt = prompts["system"]
        self.user_template = prompts["user"]

    def build_prompt(self, topic: Topic, paper: dict) -> str:
        """Build the user prompt for a single (topic, paper) pair."""
        return self.user_template.format(
            topic_name=topic.name,
            description=topic.description,
            keywords=", ".join(topic.keywords) if topic.keywords else "(none)",
            title=paper.get("title", "(no title)"),
            abstract=paper.get("abstract", "(no abstract)"),
        )

    def parse_response(self, raw: str) -> dict:
        """Parse the LLM's JSON response into a structured dict.

        Handles markdown fences, malformed JSON, and missing fields gracefully.
        """
        text = raw.strip()

        # Strip markdown code fences if present
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"verdict": "error", "reason": f"Failed to parse LLM response: {raw[:200]}"}

        # Validate and normalize fields
        verdict = str(data.get("verdict", "error")).lower().strip()
        if verdict not in ("match", "maybe", "no"):
            verdict = "error"

        reason = str(data.get("reason", "")).strip()

        return {"verdict": verdict, "reason": reason}

    def score(self, topic: Topic, paper: dict) -> dict:
        """Score a single paper against a single topic. Returns an enriched paper dict.

        `match_level` is one of: "match", "maybe", "no", "error".
        """
        prompt = self.build_prompt(topic, paper)

        if self.verbose:
            print(f"\n{'=' * 120}")
            print(f"[prompt]\n{prompt}")
            print(f"{'=' * 120}")

        raw = self.llm.generate(prompt=prompt, system=self.system_prompt, format="json")

        if self.verbose:
            print(f"[response] {raw}")

        result = self.parse_response(raw)

        return {
            "id": paper.get("id", ""),
            "title": paper.get("title", ""),
            "authors": paper.get("authors", []),
            "published": paper.get("published", ""),
            "url": paper.get("url", ""),
            "match_level": result["verdict"],
            "reason": result["reason"],
        }

    def run(self, topics: List[Topic], papers: List[dict]) -> Dict[str, List[dict]]:
        """Score all papers against all topics. Returns {topic_name: [matched_papers]}."""
        results: Dict[str, List[dict]] = {}

        for topic in topics:
            matched = []
            for i, paper in enumerate(papers):
                pid = paper.get("id", "?")
                print(f"  [{topic.name}] Scoring paper {i + 1}/{len(papers)}: {pid}", end="\r")
                scored = self.score(topic, paper)
                if scored["match_level"] in ("match", "maybe"):
                    matched.append(scored)
            print(f"  [{topic.name}] Done — {len(matched)} matched out of {len(papers)} papers" + " " * 30)

            if matched:
                matched.sort(key=lambda p: 0 if p.get("match_level") == "match" else 1)
                results[topic.name] = matched

        return results


# ===========================================================================================================================
# CLI
# ===========================================================================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter papers by topic relevance using a local LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python filter.py papers.json --model gemma4:e2b
  python filter.py papers.json --model gemma4:e2b --topics topics/ -o filtered.json
  python filter.py papers.json --model gemma4:e2b --paper 2603.11969 --verbose
    """,
    )
    parser.add_argument("papers", help="Path to papers.json produced by monitor.py.")
    parser.add_argument("--topics", default="topics", metavar="PATH", help="Path to topics directory, or a single .md topic file (default: topics/).")
    parser.add_argument("-o", "--output", metavar="FILE", help="Write filtered results to this JSON file.")
    parser.add_argument("--model", help="Ollama model name (e.g. gemma4:e2b). Not required with --dry-run.")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host (default: http://localhost:11434).")
    parser.add_argument("--keep-alive", default="30m", help='Keep model in VRAM for this duration (default: 30m). Use "-1" for forever.')
    parser.add_argument("--paper", metavar="ID", help="Run on a single paper by ID (for debugging).")
    parser.add_argument("--verbose", action="store_true", help="Print prompts and raw LLM responses.")
    parser.add_argument("--unload", action="store_true", help="Unload the model from VRAM after filtering is done.")
    parser.add_argument("--dry-run", action="store_true", help="Print the prompts that would be sent to the LLM without actually calling it. No model needed.")
    parser.add_argument("--prompts", default=None, metavar="DIR", help="Prompts directory (default: prompts/ next to filter.py).")

    args = parser.parse_args()

    prompts_dir = Path(args.prompts) if args.prompts else None

    # Load data
    papers = load_papers(args.papers)
    topics = load_topics(args.topics)

    if args.paper:
        papers = [p for p in papers if p.get("id") == args.paper]
        if not papers:
            print(f"Paper '{args.paper}' not found in {args.papers}")
            return

    # Dry-run mode: just print prompts, no LLM needed
    if args.dry_run:
        filt = FastFilter(llm=None, verbose=False, prompts_dir=prompts_dir)
        for topic in topics:
            for paper in papers:
                pid = paper.get("id", "?")
                print(f"{'=' * 120}")
                print(f"[system]\n{filt.system_prompt}\n")
                print(f"[user] topic={topic.name}  paper={pid}")
                print(filt.build_prompt(topic, paper))
        print(f"{'=' * 120}")
        print(f"\n{len(topics)} topic(s) x {len(papers)} paper(s) = {len(topics) * len(papers)} prompt(s)")
        return

    if not args.model:
        parser.error("--model is required (unless using --dry-run)")

    print(f"Loaded {len(papers)} paper(s), {len(topics)} topic(s). Model: {args.model}")

    # Set up LLM
    from .llm import LLMClient

    client = LLMClient(model=args.model, host=args.host)
    ok, msg = client.load(keep_alive=args.keep_alive)
    if not ok:
        print(f"Failed to load model: {msg}")
        return
    print(msg)

    # Run filter
    filt = FastFilter(llm=client, verbose=args.verbose, prompts_dir=prompts_dir)
    results = filt.run(topics, papers)

    # Output
    format_results(results)
    if args.output:
        save_results(results, args.papers, args.topics, args.output)

    if args.unload:
        ok, msg = client.unload()
        print(msg)


if __name__ == "__main__":
    main()
