"""Topic-based paper filter using a local LLM.

For each (topic, paper) pair, `FastFilter.score` sends the topic description +
keywords and the paper's title + abstract to a local LLM (via `LLMClient` /
Ollama) and parses a structured JSON verdict. Used as a library by
`PaperFoundry.cli` (papertrack).
"""

import json
import re
from pathlib import Path
from typing import Dict

from .prompt import PromptLibrary
from .topics import Topic


class FastFilter:
    """LLM-based paper filter using title + abstract.

    For each (topic, paper) pair, binds the `fast` prompt (see `PromptLibrary`)
    with the topic description/keywords and the paper's title + abstract,
    sends it to the LLM, and parses a structured JSON verdict.
    """

    def __init__(self, llm, verbose: bool = False, prompts_dir: Path = None):
        self.llm = llm
        self.verbose = verbose
        self.prompt = PromptLibrary(prompts_dir).load("fast")

    def _bind(self, topic: Topic, paper: dict) -> Dict[str, str]:
        return self.prompt.render(
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

        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"verdict": "error", "reason": f"Failed to parse LLM response: {raw[:200]}"}

        verdict = str(data.get("verdict", "error")).lower().strip()
        if verdict not in ("match", "maybe", "no"):
            verdict = "error"

        reason = str(data.get("reason", "")).strip()

        return {"verdict": verdict, "reason": reason}

    def score(self, topic: Topic, paper: dict) -> dict:
        """Score a single paper against a single topic. Returns an enriched paper dict.

        `match_level` is one of: "match", "maybe", "no", "error".
        """
        bound = self._bind(topic, paper)

        if self.verbose:
            print(f"\n{'=' * 120}")
            print(f"[prompt]\n{bound['user']}")
            print(f"{'=' * 120}")

        raw = self.llm.generate(prompt=bound["user"], system=bound["system"], format="json")

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
