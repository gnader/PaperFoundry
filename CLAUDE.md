# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# --- monitor.py ---------------------------------------------------------------

# Fetch recent papers from an arXiv category (writes/updates papers.json)
python monitor.py cs.GR

# Multiple categories, custom output, higher per-source cap
python monitor.py cs.GR cs.CV cs.LG -o papers.json --max 100

# Fetch from a listing URL instead of a bare category
python monitor.py https://arxiv.org/list/cs.GR/recent

# Single-day fetch (shorthand for --from D --to D)
python monitor.py cs.GR --date 2026-03-15

# Date-range fetch
python monitor.py cs.GR --from 2026-01-01 --to 2026-03-15 -o papers.json

# Running monitor against an existing output file does an incremental fetch:
# known IDs are skipped and the fetcher paginates until --max new papers
# are collected (or arXiv is exhausted).

# --- llm.py -------------------------------------------------------------------

# Check the Ollama service is reachable (no --model needed)
python llm.py

# List currently loaded models
python llm.py --loaded

# Load a model into VRAM with a keep-alive
python llm.py --model gemma3:4b --load --keep-alive 30m

# Evict a model from VRAM
python llm.py --model gemma3:4b --unload

# One-shot generate
python llm.py --model gemma3:4b --prompt "hello"
python llm.py --model gemma3:4b --prompt "classify this" --system "You are a classifier" --format json

# --- filter.py ----------------------------------------------------------------

# Filter papers against topics.json using a local LLM
python filter.py papers.json --model gemma3:4b

# Custom topics file, write results to JSON
python filter.py papers.json --model gemma3:4b --topics topics.json -o filtered.json

# Debug a single paper with prompt/response echo
python filter.py papers.json --model gemma3:4b --paper 2603.11969 --verbose

# Dry-run: print the prompts that would be sent, no model needed
python filter.py papers.json --dry-run

# Keep model longer / unload when done
python filter.py papers.json --model gemma3:4b --keep-alive 1h --unload
```

## Dependencies

```bash
pip install requests ollama
```

- `requests` — required by `monitor.py` for the arXiv Atom API.
- `ollama` — required by `llm.py` (and transitively by `filter.py`).

The **Ollama service** must also be installed and running separately. On Windows the installer registers it as a background service listening on `http://localhost:11434`. `llm.py` never starts the service — it only connects and reports clear errors if the service or requested model isn't available. Pull models with `ollama pull <name>` (e.g. `ollama pull gemma3:4b`).

## Architecture

Three independent single-file modules. The pipeline is `monitor.py → papers.json → filter.py`, with `llm.py` injected into `filter.py` as the scoring backend.

### `monitor.py` — arXiv feed monitor

- `Paper` dataclass (`monitor.py:46`) — `id`, `title`, `authors`, `abstract`, `url`, `pdf_url`, `published`, `categories`, `source`, `fetched_at`.
- `ArxivFetcher` (`monitor.py:65`) — hits `https://export.arxiv.org/api/query`, parses the Atom XML into `Paper`s. `fetch()` paginates: when `known_ids` is supplied it keeps requesting batches until `target_new` unseen papers are collected (or arXiv is exhausted).
- `LiteratureMonitor` (`monitor.py:233`) — orchestrates one or more sources, dedupes by arXiv ID, sorts newest-first. Static `save()`, `load()`, and `load_ids()` helpers persist/read the JSON file.
- Source strings can be bare categories (`cs.GR`) or full listing URLs (`https://arxiv.org/list/cs.GR/recent`); `_resolve_category()` normalizes both.
- Date filtering: `--date D` is sugar for `--from D --to D`; dates are pushed into `_build_query()` as a `submittedDate:[lo TO hi]` clause.
- Incremental fetch: when the output file already exists, its IDs are loaded as `known_ids` and pagination skips past them so you get up to `--max` *new* papers per run.

### `llm.py` — Ollama wrapper

Thin abstraction over the official `ollama` Python package. Never starts the service — only connects.

`LLMClient(model, host)` (`llm.py:76`) validates at construction time that the service is reachable and the model is pulled — raises `RuntimeError` otherwise.

- `check_loaded()` → `(bool, message)` — whether the model is resident in VRAM, with reported size and expiry.
- `load(keep_alive)` → loads the model into VRAM via an empty-prompt `generate` call. `keep_alive` follows Ollama's format: `"30m"`, `"1h"`, `"-1"` to keep forever, `"0"` to unload immediately.
- `unload()` → calls `generate` with `keep_alive=0` to evict.
- `generate(prompt, system, format, options)` → one-shot generation. **Does not auto-load**; raises if the model isn't already resident. `format="json"` forces structured JSON output (used by `FastFilter`).
- `embed(text)` → embedding vector for `text` using the current model (the model must support embeddings).

Cross-version compatibility with the `ollama` package is handled by small helpers at the top of the file: `_iter_models()`, `_entry_attr()`, `_model_names()` normalize dict-shaped vs object-shaped responses; `_is_not_found()` detects "model not pulled" errors uniformly.

CLI mirrors the API with mutually exclusive actions: `--loaded` / `--load` / `--unload` / `--prompt`.

### `filter.py` — topic-based paper filter

Reads a `papers.json` (produced by `monitor.py`) and a `topics.json`, and for each `(topic, paper)` pair asks a local LLM via `LLMClient.generate(..., format="json")` to classify relevance.

- `Topic` dataclass (`filter.py:54`) — `name`, `keywords`, `description`, `papers` (the last is reserved for future "known good" seeding).
- `FastFilter` (`filter.py:151`):
  - Loads `prompts/fast.prompt` once via `_load_prompts()` (`filter.py:27`) — a section-tagged plain-text file with `[system]` and `[user]` headers; missing sections raise `ValueError`.
  - `build_prompt(topic, paper)` — fills the user template with `{topic_name}`, `{description}`, `{keywords}`, `{title}`, `{abstract}`.
  - `parse_response(raw)` — strips markdown fences, parses JSON, normalizes `verdict` to one of `match` / `maybe` / `no` / `error`.
  - `score(topic, paper)` — **always** returns an enriched paper dict with `match_level` ∈ {match, maybe, no, error}. Filtering is the caller's job, not `score`'s.
  - `run(topics, papers)` — drives the pair loop, keeps only `match` / `maybe` results, and sorts `match` before `maybe`.
- Module-level I/O helpers: `load_papers`, `load_topics`, `save_results`, `format_results`.
- CLI knobs of note: `--dry-run` prints prompts without contacting Ollama (no `--model` required); `--paper ID` runs against a single paper for debugging; `--verbose` echoes prompts and raw responses; `--unload` evicts the model after the run.

### `prompts/` directory

`prompts/fast.prompt` holds the FastFilter prompt in a single section-tagged file:

```
[system]
...system prompt...

[user]
...user template with {topic_name}, {description}, {keywords}, {title}, {abstract} placeholders...
```

Parsing rule: a section header is any line whose stripped form is exactly `[system]` or `[user]`. Everything between two headers (or from a header to end-of-file) is that section's body, with surrounding whitespace stripped. Bodies can contain quotes, curly braces, and blank lines safely.

Add additional prompt files alongside `fast.prompt` (e.g. a future `deep.prompt`) and load them with `_load_prompts("deep.prompt")`.
