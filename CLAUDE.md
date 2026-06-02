# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

`papertrack` is the only user-facing CLI. It's registered as a console_script via `pip install -e .` and is equivalent to `python -m PaperFoundry`.

```bash
# List topics (no model needed)
papertrack --topic-list

# Fetch + classify + write Markdown report
papertrack --arxiv cs.GR --date today
papertrack --arxiv cs.GR cs.CV --date this-week --topic "Neural BRDF"
papertrack --arxiv cs.GR --from 2026-01-01 --to 2026-01-31 --all-papers -o jan.md

# Equivalent entry point
python -m PaperFoundry --arxiv cs.GR --date today
```

`papertrack` is the only user-facing entry point. `analyzer` has a `__main__` for development testing (see below). Other modules (`monitor`, `llm`, `filter`) are library-only — they have no `main()` and no per-module CLI.

## Using PaperFoundry as a library

```python
import PaperFoundry
from PaperFoundry import LLMClient, FastFilter, load_topics, PromptLibrary
```

The package re-exports its primary public API at the top level via lazy attribute loading (`PaperFoundry/__init__.py`): `LLMClient`, `Paper`, `ArxivFetcher`, `LiteratureMonitor`, `Topic`, `load_topics`, `FastFilter`, `PaperAnalyzer`, `Pipeline`, `PipelineStep`, `Prompt`, `PromptLibrary`. Submodules are imported on first access.

## Dependencies

Python 3.11+ is required (TOML config is read via stdlib `tomllib`).

```bash
pip install -e .          # registers the `papertrack` console_script
# or, without install:
pip install requests ollama
```

- `requests` — required by `PaperFoundry.monitor` for the arXiv Atom API.
- `ollama` — required by `PaperFoundry.llm` (and transitively by `PaperFoundry.filter`).

The **Ollama service** must also be installed and running separately. On Windows the installer registers it as a background service listening on `http://localhost:11434`. `PaperFoundry.llm` never starts the service — it only connects and reports clear errors if the service or requested model isn't available. Pull models with `ollama pull <name>` (e.g. `ollama pull gemma3:4b`).

## Architecture

Eight library modules inside the `PaperFoundry/` package: `llm`, `monitor`, `topics`, `prompt`, `pipeline`, `filter`, `analyzer`, `cli`. The fetch/filter pipeline is `PaperFoundry.monitor → papers.json → PaperFoundry.filter`, with `PaperFoundry.llm` injected as the scoring backend and `PaperFoundry.topics` providing the topic dataclass + markdown loader. `cli` (exposed as the `papertrack` console_script and `python -m PaperFoundry`) is a thin orchestrator that wires fetch → filter → Markdown report. `pipeline` is the declarative prompt pipeline engine; `analyzer` wraps it for single-paper deep analysis (not wired into the CLI pipeline). User-facing content lives outside the package: `topics/*.md` (topic definitions), `pipelines/*.toml` (pipeline definitions), `papertrack.toml` (config), `.papertrack_cache/` (fetch + pipeline step caches).

### `PaperFoundry/monitor.py` — arXiv feed monitor

- `Paper` dataclass (`PaperFoundry/monitor.py:46`) — `id`, `title`, `authors`, `abstract`, `url`, `pdf_url`, `published`, `categories`, `source`, `fetched_at`.
- `ArxivFetcher` (`PaperFoundry/monitor.py:65`) — hits `https://export.arxiv.org/api/query`, parses the Atom XML into `Paper`s. `fetch()` paginates: when `known_ids` is supplied it keeps requesting batches until `target_new` unseen papers are collected (or arXiv is exhausted).
- `LiteratureMonitor` (`PaperFoundry/monitor.py:233`) — orchestrates one or more sources, dedupes by arXiv ID, sorts newest-first. Static `save()`, `load()`, and `load_ids()` helpers persist/read the JSON file.
- Source strings can be bare categories (`cs.GR`) or full listing URLs (`https://arxiv.org/list/cs.GR/recent`); `_resolve_category()` normalizes both.
- Date filtering: `--date D` is sugar for `--from D --to D`; dates are pushed into `_build_query()` as a `submittedDate:[YYYYMMDD0000 TO YYYYMMDD2359]` clause. The `0000`/`2359` time suffixes are required — arXiv treats bare `YYYYMMDD` as midnight, so same-day ranges would otherwise be zero-width.
- Incremental fetch: when the output file already exists, its IDs are loaded as `known_ids` and pagination skips past them so you get up to `--max` *new* papers per run.

### `PaperFoundry/llm.py` — Ollama wrapper

Thin abstraction over the official `ollama` Python package. Never starts the service — only connects.

`LLMClient(model, host, max_chars)` validates at construction time that the service is reachable and the model is pulled — raises `RuntimeError` otherwise.

- `max_chars: Optional[int]` — the model's effective context limit in characters. When set, the pipeline engine uses it to decide whether to paginate a step's input. `None` means no limit.
- `check_loaded()` → `(bool, message)` — whether the model is resident in VRAM, with reported size and expiry.
- `load(keep_alive)` → loads the model into VRAM via an empty-prompt `generate` call. `keep_alive` follows Ollama's format: `"30m"`, `"1h"`, `"-1"` to keep forever, `"0"` to unload immediately.
- `unload()` → calls `generate` with `keep_alive=0` to evict.
- `generate(prompt, system, format, options)` → one-shot generation. **Does not auto-load**; raises if the model isn't already resident. `format="json"` forces structured JSON output (used by `FastFilter`).
- `embed(text)` → embedding vector for `text` using the current model (the model must support embeddings).

Cross-version compatibility with the `ollama` package is handled by small helpers at the top of the file: `_iter_models()`, `_entry_attr()`, `_model_names()` normalize dict-shaped vs object-shaped responses; `_is_not_found()` detects "model not pulled" errors uniformly.

### `PaperFoundry/topics.py` — topic definitions and markdown loader

Holds the `Topic` dataclass (`name`, `keywords`, `description`, `papers` — the last is a list of freeform strings, reserved for future "known good" seeding) and the markdown topic-file parser.

Topic files live under `topics/` at the repo root (one `.md` per topic) with a single `# Title` heading and `## Description` / `## Keywords` / `## Papers` sections. Keywords and Papers are bullet lists (`-` or `*`); Description is free-form prose. `_parse_topic_md()` implements the parsing rules; `load_topics(path)` accepts either a directory (loads all `*.md`, sorted by filename) or a single `.md` file. Missing `# Title` raises `ValueError`.

Shared with `filter.py` today and with the planned `DeepFilter` tomorrow.

### `PaperFoundry/filter.py` — topic-based paper filter

Reads a `papers.json` (produced by `PaperFoundry.monitor`) and a directory of `topics/*.md` files (via `topics.load_topics`), and for each `(topic, paper)` pair asks a local LLM via `LLMClient.generate(..., format="json")` to classify relevance.

`FastFilter`:
  - Loads the `fast_filter` prompt once via `PromptLibrary(prompts_dir).load("fast_filter")` (see `PaperFoundry/prompt.py`). The resulting `Prompt` exposes `.system_template`, `.user_template`, and a discovered `.parameters` set.
  - `_bind(topic, paper)` — calls `self.prompt.render(...)` with the five declared parameters (`topic_name`, `description`, `keywords`, `title`, `abstract`); returns `{"system": ..., "user": ...}`.
  - `parse_response(raw)` — strips markdown fences, parses JSON, normalizes `verdict` to one of `match` / `maybe` / `no` / `error`.
  - `score(topic, paper)` — **always** returns an enriched paper dict with `match_level` ∈ {match, maybe, no, error}. Filtering is the caller's job, not `score`'s. `cli.py`'s `score_all` is the loop driver over (topic × paper).

### `PaperFoundry/cli.py` — `papertrack` unified CLI

Thin orchestration layer. Loads config (`tomllib`, stdlib), resolves date range, fetches via `LiteratureMonitor` with a per-category JSON cache, scores every (topic, paper) pair via `FastFilter.score`, then writes a Markdown report grouped per topic with buckets `✓ Match` / `? Maybe` / `✗ No`.

Key functions: `load_config()`, `resolve_date_range()` (calendar-aligned: `today` / `this-week` = Monday→today / `this-month` = 1st→today), `fetch_with_cache()`, `score_all()`, `write_markdown()`. Cache files live at `<cache_dir>/<category>.json` and reuse `LiteratureMonitor.save/load` plus the existing `known_ids` incremental flow.

Config search order: `--config PATH` → `./papertrack.toml` → `~/.papertrack/config.toml`. Recognized keys: `model`, `topics`, `host`, `keep_alive`, `cache_dir`, `output_dir`, `max_chars`. CLI flags always override config. The model is always unloaded from VRAM before the CLI exits (including on exceptions and Ctrl-C), via a `try/finally` around the scoring/report phase.

`max_chars` (integer, optional) — passed directly to `LLMClient`. When set, prompts that declare `[paginate]` will split their input automatically.

Default report filename: `report_<YYYY-MM-DD>_<sources_joined>.md` in `output_dir` (or CWD). Override with `-o`. `python -m PaperFoundry` is wired to `cli.main` via `__main__.py`.

Other behavioral notes:
- `--max` defaults to 200 new papers per source.
- The Markdown report emits Title / Authors / Published / arXiv ID / Why only — abstracts are intentionally omitted to keep reports readable.
- If `--date today` returns zero papers, `main()` prints a note about arXiv's daily announcement schedule (~20:00 UTC weekdays, none on weekends) before exiting.

### `PaperFoundry/prompt.py` — prompts as shaders

A `.prompt` file is treated like a shader source: parsed (compiled) into a `Prompt` program with two required section templates (`[system]` / `[user]`) and an optional `[paginate]` section. `Prompt.render(**params)` binds parameters — validating strictly that every declared one is supplied and no extras slip in — and returns `{"system": ..., "user": ...}`.

- `Prompt` (frozen dataclass): `name`, `system_template`, `user_template`, `parameters: frozenset[str]`, `source_path`, `paginate_input: Optional[str]`. Methods: `load(name, root)`, `validate(params)`, `render(**params)`.
- `PromptLibrary(root)`: directory registry. `load(name)` → compile `<root>/<name>.prompt`. `list()` → sorted stems of all `*.prompt` files. Default root is `PaperFoundry/prompts/`.
- Both sections are templates (the system section can also carry placeholders). Placeholder discovery uses `string.Formatter().parse()`.

**`[paginate]` section** (optional): declares that the prompt supports being called multiple times on chunks of a large input. Body is `input = <param_name>`, naming which prompt parameter gets split. When the pipeline engine sees `paginate_input` is set and the input exceeds `llm.max_chars`, it splits automatically, runs once per chunk, and merges results (lists are extended with deduplication; strings are joined with `\n\n`).

### `PaperFoundry/pipeline.py` — declarative prompt pipeline engine

Pipelines are defined as TOML files under `pipelines/` at the repo root. Each `[[step]]` entry names a prompt, declares input mappings from the shared context, and lists output keys to extract from the LLM's JSON response. Intermediate results are cached per-step as JSON so any step can be re-run without repeating earlier work.

**TOML format:**
```toml
[pipeline]
name = "my_pipeline"

[[step]]
name = "step_a"
prompt = "some_prompt"
inputs  = { param = "$.initial_key" }
outputs = ["result_key"]

[[step]]
name = "step_b"
prompt = "another_prompt"
inputs  = { data = "$.step_a.result_key" }
outputs = ["final_key"]
```

Input expressions: `$.key` reads from the initial context; `$.step_name.output_key` reads a prior step's output.

**`Pipeline`** — loaded via `Pipeline.load(path, prompts_dir)`. Key method:
- `run(llm, initial_context, cache_dir, run_id, resume_from, verbose)` — executes steps in order. Auto-resume: if a step's cache file exists it is loaded and the LLM call is skipped; once any step re-runs, all subsequent steps re-run too (consistency). `resume_from="step_name"` forces re-execution from that step onwards regardless of cache.

**Pagination**: if a step's prompt has `paginate_input` set (via `[paginate]` in the `.prompt` file) and `llm.max_chars` is set and the input exceeds it, the engine splits the text at paragraph/line boundaries, runs the prompt once per chunk, and merges results automatically. A warning is printed.

**Cache layout**: `.papertrack_cache/pipelines/{pipeline_name}/{run_id}/{step_name}.json`

**`PipelineStep`** (frozen dataclass): `name`, `prompt`, `inputs: dict[str, str]`, `outputs: list[str]`.

### `PaperFoundry/analyzer.py` — single-paper deep analysis

Thin wrapper over `Pipeline` that extracts full PDF text and runs the `analyze_paper` pipeline (`pipelines/analyze_paper.toml`).

**`PaperAnalyzer`**:
- `extract_text(pdf_path)` — extracts the full raw text from all pages via `pypdf`. No truncation; length is managed by `llm.max_chars` + pipeline pagination.
- `analyze(pdf_path, run_id, resume_from, verbose)` — runs the three-step pipeline and returns `{"tldr": ..., "what_it_does": ..., "what_it_improves": ...}`. `run_id` defaults to the PDF's stem.
- Constructor params: `llm`, `prompts_dir`, `cache_dir`, `pipeline_path` (defaults to `pipelines/analyze_paper.toml`).

The `analyze_paper` pipeline steps:
1. `detect_sections` (`section_detect` prompt, paginate-enabled) — returns `{"sections": [...]}`.
2. `classify_sections` (`section_classify` prompt) — selects 2–4 sections most useful for a TLDR (abstract, intro, conclusion, named method). Returns `{"key_sections": [...]}`.
3. `analyze` (`analyze` prompt) — takes the full text + key section names and returns `{"tldr", "what_it_does", "what_it_improves"}`.

Dev test entry point:
```bash
python -m PaperFoundry.analyzer [pdf] [model] [max_chars]
python -m PaperFoundry.analyzer [pdf] [model] [max_chars] --verbose
python -m PaperFoundry.analyzer [pdf] [model] [max_chars] --resume-from=classify_sections
```
PDFs are looked up in `test/*.pdf` if no path is given.

### `PaperFoundry/prompts/` directory

Current prompts:

| File | Placeholders | Paginate | Used by |
|---|---|---|---|
| `fast_filter.prompt` | `topic_name`, `description`, `keywords`, `title`, `abstract` | — | `FastFilter` |
| `analyze.prompt` | `text`, `key_sections` | — | `analyze_paper` pipeline (step 3) |
| `section_detect.prompt` | `text` | `text` | `analyze_paper` pipeline (step 1) |
| `section_classify.prompt` | `sections` | — | `analyze_paper` pipeline (step 2) |

Parsing rule: a section header is any line whose stripped form is exactly `[system]`, `[user]`, or `[paginate]`. Everything between two headers (or from a header to end-of-file) is that section's body, with surrounding whitespace stripped. Literal `{` / `}` in prompt bodies must be escaped as `{{` / `}}` (standard `string.Formatter` convention) — necessary when the prompt body contains JSON examples.

Add additional prompt files and load them with `PromptLibrary().load("name")` or `Prompt.load("name")`.
