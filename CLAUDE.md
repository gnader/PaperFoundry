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

The per-module debug CLIs (`python -m PaperFoundry.monitor`, `.llm`, `.filter`) still exist and are useful for isolated troubleshooting — see each module's `main()` for flags — but day-to-day usage is the single `papertrack` command.

## Using PaperFoundry as a library

```python
import PaperFoundry
from PaperFoundry import LLMClient, FastFilter, load_topics, load_papers
```

The package re-exports its primary public API at the top level via lazy attribute loading (`PaperFoundry/__init__.py`): `LLMClient`, `Paper`, `ArxivFetcher`, `LiteratureMonitor`, `Topic`, `load_topics`, `FastFilter`, `load_papers`, `save_results`, `format_results`. Submodules are imported on first access, so `python -m PaperFoundry.<mod>` runs without double-import warnings.

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

Five library modules inside the `PaperFoundry/` package: `llm`, `monitor`, `topics`, `filter`, `cli`. The pipeline is `PaperFoundry.monitor → papers.json → PaperFoundry.filter`, with `PaperFoundry.llm` injected into `filter` as the scoring backend and `PaperFoundry.topics` providing the topic dataclass + markdown loader. `cli` (exposed as the `papertrack` console_script and `python -m PaperFoundry`) is a thin orchestrator that wires fetch → filter → Markdown report. User-facing content lives outside the package: `topics/*.md` (topic definitions), `papertrack.toml` (config), `.papertrack_cache/*.json` (per-category fetch cache).

### `PaperFoundry/monitor.py` — arXiv feed monitor

- `Paper` dataclass (`PaperFoundry/monitor.py:46`) — `id`, `title`, `authors`, `abstract`, `url`, `pdf_url`, `published`, `categories`, `source`, `fetched_at`.
- `ArxivFetcher` (`PaperFoundry/monitor.py:65`) — hits `https://export.arxiv.org/api/query`, parses the Atom XML into `Paper`s. `fetch()` paginates: when `known_ids` is supplied it keeps requesting batches until `target_new` unseen papers are collected (or arXiv is exhausted).
- `LiteratureMonitor` (`PaperFoundry/monitor.py:233`) — orchestrates one or more sources, dedupes by arXiv ID, sorts newest-first. Static `save()`, `load()`, and `load_ids()` helpers persist/read the JSON file.
- Source strings can be bare categories (`cs.GR`) or full listing URLs (`https://arxiv.org/list/cs.GR/recent`); `_resolve_category()` normalizes both.
- Date filtering: `--date D` is sugar for `--from D --to D`; dates are pushed into `_build_query()` as a `submittedDate:[YYYYMMDD0000 TO YYYYMMDD2359]` clause. The `0000`/`2359` time suffixes are required — arXiv treats bare `YYYYMMDD` as midnight, so same-day ranges would otherwise be zero-width.
- Incremental fetch: when the output file already exists, its IDs are loaded as `known_ids` and pagination skips past them so you get up to `--max` *new* papers per run.

### `PaperFoundry/llm.py` — Ollama wrapper

Thin abstraction over the official `ollama` Python package. Never starts the service — only connects.

`LLMClient(model, host)` (`PaperFoundry/llm.py:76`) validates at construction time that the service is reachable and the model is pulled — raises `RuntimeError` otherwise.

- `check_loaded()` → `(bool, message)` — whether the model is resident in VRAM, with reported size and expiry.
- `load(keep_alive)` → loads the model into VRAM via an empty-prompt `generate` call. `keep_alive` follows Ollama's format: `"30m"`, `"1h"`, `"-1"` to keep forever, `"0"` to unload immediately.
- `unload()` → calls `generate` with `keep_alive=0` to evict.
- `generate(prompt, system, format, options)` → one-shot generation. **Does not auto-load**; raises if the model isn't already resident. `format="json"` forces structured JSON output (used by `FastFilter`).
- `embed(text)` → embedding vector for `text` using the current model (the model must support embeddings).

Cross-version compatibility with the `ollama` package is handled by small helpers at the top of the file: `_iter_models()`, `_entry_attr()`, `_model_names()` normalize dict-shaped vs object-shaped responses; `_is_not_found()` detects "model not pulled" errors uniformly.

CLI mirrors the API with mutually exclusive actions: `--loaded` / `--load` / `--unload` / `--prompt`.

### `PaperFoundry/topics.py` — topic definitions and markdown loader

Holds the `Topic` dataclass (`name`, `keywords`, `description`, `papers` — the last is a list of freeform strings, reserved for future "known good" seeding) and the markdown topic-file parser.

Topic files live under `topics/` at the repo root (one `.md` per topic) with a single `# Title` heading and `## Description` / `## Keywords` / `## Papers` sections. Keywords and Papers are bullet lists (`-` or `*`); Description is free-form prose. `_parse_topic_md()` implements the parsing rules; `load_topics(path)` accepts either a directory (loads all `*.md`, sorted by filename) or a single `.md` file. Missing `# Title` raises `ValueError`.

Shared with `filter.py` today and with the planned `DeepFilter` tomorrow.

### `PaperFoundry/filter.py` — topic-based paper filter

Reads a `papers.json` (produced by `PaperFoundry.monitor`) and a directory of `topics/*.md` files (via `topics.load_topics`), and for each `(topic, paper)` pair asks a local LLM via `LLMClient.generate(..., format="json")` to classify relevance.

- `FastFilter` (`PaperFoundry/filter.py:151`):
  - Loads `prompts/fast.prompt` once via `_load_prompts()` (`PaperFoundry/filter.py:27`) — a section-tagged plain-text file with `[system]` and `[user]` headers; missing sections raise `ValueError`. `PROMPTS_DIR` resolves relative to the package directory.
  - `build_prompt(topic, paper)` — fills the user template with `{topic_name}`, `{description}`, `{keywords}`, `{title}`, `{abstract}`.
  - `parse_response(raw)` — strips markdown fences, parses JSON, normalizes `verdict` to one of `match` / `maybe` / `no` / `error`.
  - `score(topic, paper)` — **always** returns an enriched paper dict with `match_level` ∈ {match, maybe, no, error}. Filtering is the caller's job, not `score`'s.
  - `run(topics, papers)` — drives the pair loop, keeps only `match` / `maybe` results, and sorts `match` before `maybe`.
- Module-level I/O helpers: `load_papers`, `save_results`, `format_results` (topic loading is re-exported from `.topics`).
- CLI knobs of note: `--dry-run` prints prompts without contacting Ollama (no `--model` required); `--paper ID` runs against a single paper for debugging; `--verbose` echoes prompts and raw responses; `--unload` evicts the model after the run.

### `PaperFoundry/cli.py` — `papertrack` unified CLI

Thin orchestration layer. Loads config (`tomllib`, stdlib), resolves date range, fetches via `LiteratureMonitor` with a per-category JSON cache, scores every (topic, paper) pair via `FastFilter.score` directly (not `.run()` — that drops `match_level == "no"`, which `--all-papers` needs), then writes a Markdown report grouped per topic with buckets `✓ Match` / `? Maybe` / `✗ No`.

Key functions: `load_config()`, `resolve_date_range()` (calendar-aligned: `today` / `this-week` = Monday→today / `this-month` = 1st→today), `fetch_with_cache()`, `score_all()`, `write_markdown()`. Cache files live at `<cache_dir>/<category>.json` and reuse `LiteratureMonitor.save/load` plus the existing `known_ids` incremental flow.

Config search order: `--config PATH` → `./papertrack.toml` → `~/.papertrack/config.toml`. Recognized keys: `model`, `topics`, `host`, `keep_alive`, `cache_dir`, `output_dir`. CLI flags always override config. The model is always unloaded from VRAM before the CLI exits (including on exceptions and Ctrl-C), via a `try/finally` around the scoring/report phase.

Default report filename: `report_<YYYY-MM-DD>_<sources_joined>.md` in `output_dir` (or CWD). Override with `-o`. `python -m PaperFoundry` is wired to `cli.main` via `__main__.py`.

Other behavioral notes:
- `--max` defaults to 200 new papers per source.
- The Markdown report emits Title / Authors / Published / arXiv ID / Why only — abstracts are intentionally omitted to keep reports readable.
- If `--date today` returns zero papers, `main()` prints a note about arXiv's daily announcement schedule (~20:00 UTC weekdays, none on weekends) before exiting.

### `PaperFoundry/prompts/` directory

`PaperFoundry/prompts/fast.prompt` holds the FastFilter prompt in a single section-tagged file:

```
[system]
...system prompt...

[user]
...user template with {topic_name}, {description}, {keywords}, {title}, {abstract} placeholders...
```

Parsing rule: a section header is any line whose stripped form is exactly `[system]` or `[user]`. Everything between two headers (or from a header to end-of-file) is that section's body, with surrounding whitespace stripped. Bodies can contain quotes, curly braces, and blank lines safely.

Add additional prompt files alongside `fast.prompt` (e.g. a future `deep.prompt`) and load them with `_load_prompts("deep.prompt")`.
