"""Declarative prompt pipeline engine.

Pipelines are defined in TOML files (one per pipeline). Each step names a
prompt, declares input mappings from the shared context, and lists the output
keys to extract from the LLM's JSON response. Intermediate results are cached
as JSON files so the pipeline can resume from any step.

Usage:
    from PaperFoundry import Pipeline, LLMClient
    from pathlib import Path

    client = LLMClient(model="gemma4:e2b")
    client.load(keep_alive="30m")

    pipeline = Pipeline.load(Path("pipelines/analyze_paper.toml"))
    result = pipeline.run(
        client,
        initial_context={"raw_text": "..."},
        cache_dir=Path(".papertrack_cache"),
        run_id="my_paper",
    )
    # result["analyze.tldr"], result["analyze.what_it_does"], ...

    client.unload()

Context path syntax
-------------------
Step inputs are expressions of the form ``$.key``:
- ``$.raw_text``                  — initial context key
- ``$.detect_sections.sections``  — the "sections" output of step "detect_sections"

List values are joined with ``\\n`` when rendered into prompt strings.
"""

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .prompt import Prompt, PromptLibrary


@dataclass(frozen=True)
class PipelineStep:
    name: str
    prompt: str
    inputs: Dict[str, str]
    outputs: List[str]


def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON. Raises ValueError on failure."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse LLM JSON response: {exc}\nRaw: {raw[:300]}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected JSON object, got {type(data).__name__}: {raw[:200]}"
        )
    return data


def _resolve(expr: str, context: Dict[str, Any]) -> Any:
    """Resolve a '$.path' expression against the pipeline context.

    Tries a flat key first (e.g. ``detect_sections.sections``), then a
    two-level nested lookup for dict-valued context entries.
    """
    if not expr.startswith("$."):
        raise ValueError(f"Invalid context expression {expr!r}: must start with '$.'")
    key = expr[2:]

    if key in context:
        return context[key]

    # Nested fallback: "a.b" → context["a"]["b"]
    head, _, tail = key.partition(".")
    if tail and head in context:
        val = context[head]
        if isinstance(val, dict) and tail in val:
            return val[tail]

    raise KeyError(f"Context key {key!r} not found (expression: {expr!r})")


def _split_text(text: str, max_chars: int) -> List[str]:
    """Split text into chunks of at most max_chars, preferring paragraph/line breaks."""
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    start = 0
    look_back = min(500, max_chars // 4)
    while start < len(text):
        end = start + max_chars
        if end >= len(text):
            chunks.append(text[start:])
            break
        boundary = text.rfind("\n\n", end - look_back, end)
        if boundary <= start:
            boundary = text.rfind("\n", end - look_back, end)
        if boundary <= start:
            boundary = end
        chunks.append(text[start:boundary])
        start = boundary
    return [c.strip() for c in chunks if c.strip()]


def _coerce_for_prompt(value: Any) -> str:
    """Convert a context value to a string for prompt rendering.

    Lists are joined with newlines; everything else is str()-cast.
    """
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    return str(value)


class Pipeline:
    """A linear sequence of LLM-backed steps loaded from a TOML file.

    Each step resolves its inputs from the shared context (keyed by
    ``{step_name}.{output_key}``), calls the LLM via the named prompt, and
    writes its declared output keys back into the context.
    """

    def __init__(
        self,
        name: str,
        description: str,
        steps: List[PipelineStep],
        _prompts: Dict[str, Prompt],
    ):
        self.name = name
        self.description = description
        self.steps = steps
        self._prompts = _prompts

    @classmethod
    def load(cls, path: Path, prompts_dir: Optional[Path] = None) -> "Pipeline":
        """Parse a pipeline TOML file and pre-compile its prompts."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Pipeline file not found: {path}")

        with path.open("rb") as f:
            data = tomllib.load(f)

        meta = data.get("pipeline", {})
        name = meta.get("name", path.stem)
        description = meta.get("description", "")

        raw_steps = data.get("step", [])
        if not raw_steps:
            raise ValueError(f"Pipeline {path} has no [[step]] entries")

        steps: List[PipelineStep] = []
        for raw in raw_steps:
            for required in ("name", "prompt", "inputs", "outputs"):
                if required not in raw:
                    raise ValueError(
                        f"Step is missing required key {required!r} in {path}"
                    )
            steps.append(
                PipelineStep(
                    name=raw["name"],
                    prompt=raw["prompt"],
                    inputs=dict(raw["inputs"]),
                    outputs=list(raw["outputs"]),
                )
            )

        lib = PromptLibrary(prompts_dir)
        prompts: Dict[str, Prompt] = {}
        for step in steps:
            if step.prompt not in prompts:
                prompts[step.prompt] = lib.load(step.prompt)

        return cls(name=name, description=description, steps=steps, _prompts=prompts)

    def _cache_path(self, cache_dir: Path, run_id: str, step_name: str) -> Path:
        return cache_dir / "pipelines" / self.name / run_id / f"{step_name}.json"

    def _load_step_cache(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("outputs")
        except (json.JSONDecodeError, AttributeError):
            return None

    def _save_step_cache(
        self, path: Path, step_name: str, outputs: Dict[str, Any]
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"step": step_name, "outputs": outputs}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _run_step(
        self, step: PipelineStep, llm, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve inputs, call LLM, extract declared outputs.

        If the prompt declares a paginate_input and llm.max_chars is set, the
        input is automatically split into chunks when it exceeds the limit.
        """
        resolved = {param: _resolve(expr, context) for param, expr in step.inputs.items()}
        prompt = self._prompts[step.prompt]
        max_chars = getattr(llm, "max_chars", None)

        if prompt.paginate_input and max_chars:
            raw_val = resolved.get(prompt.paginate_input, "")
            text_val = raw_val if isinstance(raw_val, str) else _coerce_for_prompt(raw_val)
            if len(text_val) > max_chars:
                return self._run_step_paginated(step, llm, resolved, text_val, max_chars, prompt)

        prompt_inputs = {param: _coerce_for_prompt(val) for param, val in resolved.items()}
        bound = prompt.render(**prompt_inputs)
        raw = llm.generate(prompt=bound["user"], system=bound["system"], format="json")

        parsed = _parse_json(raw)
        missing = [k for k in step.outputs if k not in parsed]
        if missing:
            raise ValueError(
                f"Step {step.name!r}: LLM response missing expected keys {missing}. "
                f"Got: {list(parsed.keys())}"
            )
        return {k: parsed[k] for k in step.outputs}

    def _run_step_paginated(
        self,
        step: PipelineStep,
        llm,
        resolved: Dict[str, Any],
        text_val: str,
        max_chars: int,
        prompt,
    ) -> Dict[str, Any]:
        """Run a step by splitting the paginate_input across multiple LLM calls."""
        chunks = _split_text(text_val, max_chars)
        print(
            f"  [{step.name}] input is {len(text_val):,} chars — "
            f"paginating into {len(chunks)} chunks (limit: {max_chars:,})"
        )

        merged: Dict[str, Any] = {}
        seen_sets: Dict[str, set] = {}

        for idx, chunk in enumerate(chunks):
            chunk_resolved = dict(resolved)
            chunk_resolved[prompt.paginate_input] = chunk
            prompt_inputs = {p: _coerce_for_prompt(v) for p, v in chunk_resolved.items()}
            bound = prompt.render(**prompt_inputs)
            raw = llm.generate(prompt=bound["user"], system=bound["system"], format="json")

            try:
                parsed = _parse_json(raw)
            except ValueError as exc:
                print(f"  [{step.name}] chunk {idx + 1}/{len(chunks)}: parse failed — {exc}")
                continue

            for k in step.outputs:
                val = parsed.get(k)
                if k not in merged:
                    merged[k] = val
                    if isinstance(val, list):
                        seen_sets[k] = {str(x) for x in val}
                elif isinstance(val, list) and isinstance(merged[k], list):
                    for item in val:
                        if str(item) not in seen_sets[k]:
                            seen_sets[k].add(str(item))
                            merged[k].append(item)
                elif isinstance(val, str) and isinstance(merged[k], str):
                    merged[k] = merged[k] + "\n\n" + val

        missing = [k for k in step.outputs if k not in merged]
        if missing:
            raise ValueError(
                f"Step {step.name!r}: no output produced for keys {missing} after pagination"
            )
        return {k: merged[k] for k in step.outputs}

    def run(
        self,
        llm,
        initial_context: Dict[str, Any],
        cache_dir: Optional[Path] = None,
        run_id: Optional[str] = None,
        resume_from: Optional[str] = None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Execute all steps, returning the final context dict.

        Auto-resume: if a step's cache file exists it is loaded and the LLM
        call is skipped. Once any step is re-run, all subsequent steps are
        also re-run to stay consistent.

        Args:
            llm: LLMClient (must already be loaded into VRAM).
            initial_context: Seed values accessible via '$.key' expressions.
            cache_dir: Root for per-step JSON caches. Caching is disabled when
                not supplied.
            run_id: Stable identifier for this run (e.g. PDF stem). Derived
                from a hash of initial_context when not supplied.
            resume_from: Step name to force-rerun from. Steps at and after
                this position ignore existing caches and re-execute.
            verbose: Print each step's status (cache hit / running).

        Returns:
            Context dict with every step's outputs merged in as
            ``{step_name}.{output_key}``.
        """
        context: Dict[str, Any] = dict(initial_context)

        if cache_dir is not None:
            cache_dir = Path(cache_dir)
            if run_id is None:
                run_id = hashlib.md5(
                    json.dumps(initial_context, sort_keys=True, default=str).encode()
                ).hexdigest()[:12]

        if resume_from is not None:
            names = [s.name for s in self.steps]
            if resume_from not in names:
                raise ValueError(
                    f"resume_from={resume_from!r} not found in pipeline {self.name!r}. "
                    f"Available steps: {names}"
                )
        resume_idx = (
            next(i for i, s in enumerate(self.steps) if s.name == resume_from)
            if resume_from is not None
            else None
        )

        force_rerun = False

        for i, step in enumerate(self.steps):
            if resume_idx is not None and i >= resume_idx:
                force_rerun = True

            cache_path = (
                self._cache_path(cache_dir, run_id, step.name)
                if cache_dir is not None
                else None
            )

            if not force_rerun and cache_path is not None:
                cached = self._load_step_cache(cache_path)
                if cached is not None:
                    for k, v in cached.items():
                        context[f"{step.name}.{k}"] = v
                    if verbose:
                        print(f"  [{step.name}] cache hit")
                    continue

            if verbose:
                print(f"  [{step.name}] running...")

            outputs = self._run_step(step, llm, context)
            for k, v in outputs.items():
                context[f"{step.name}.{k}"] = v

            if cache_path is not None:
                self._save_step_cache(cache_path, step.name, outputs)
                if verbose:
                    print(f"  [{step.name}] cached → {cache_path}")

            force_rerun = True

        return context
