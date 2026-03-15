# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Extract references and metadata from a PDF
python bibextract.py paper.pdf -o output.json --top 10

# Dump extracted full text for debugging
python bibextract.py paper.pdf --debug-text

# Enrich references with OpenAlex metadata (rate-limited, slow)
python bibextract.py paper.pdf --enrich

# Monitor arXiv categories for new papers
python monitor.py cs.GR cs.CV -o papers.json --max 50
python monitor.py https://arxiv.org/list/cs.GR/recent

# Monitor with date range
python monitor.py cs.GR --from 2026-01-01 --to 2026-03-15 -o papers.json

# Filter papers by topic keywords
python filter.py papers.json --topics topics.json -o filtered.json
python filter.py papers.json  # uses topics.json in cwd by default

# Start the local web UI
python app.py  # opens at http://localhost:5000
```

## Dependencies

```bash
pip install pymupdf requests flask
```

`requests` is optional for `bibextract.py` (skips OpenAlex enrichment if absent) but required for `monitor.py`. `flask` is required for `app.py`.

## Architecture

Two independent single-file modules, no shared code:

### `bibextract.py` — PDF analysis

`ReferenceExtractor` class:
- **`__init__`**: opens PDF, extracts `full_text`, detects `sections` (list of header dicts), builds `section_map`
- **`section_map`**: `Dict[str, Tuple[int, Optional[int]]]` — maps section title → (start, end) char positions in `full_text`
- **`extract()`** → all refs parsed + importance-scored, sorted by `importance_score` desc
- **`analyze(top_n)`** → full pipeline result: title + abstract + intro + sections + refs + top_refs

Citation importance scoring (`_calculate_importance_score`) weights each citation by the section it appears in (method/results sections score higher than introduction).

Module-level helpers outside the class handle OpenAlex enrichment (`lookup_google_scholar_metadata`, `enrich_references_with_scholar`).

### `monitor.py` — arXiv feed monitor

- `ArxivFetcher`: hits the arXiv Atom API, parses XML into `Paper` dataclasses
- `LiteratureMonitor`: orchestrates multiple sources, deduplicates by arXiv ID, sorts by date
- Sources can be bare category names (`cs.GR`) or full listing URLs
- Date range filtering via `--from` / `--to` (YYYY-MM-DD); passed down to `ArxivFetcher._build_query()`

### `filter.py` — topic-based paper filter

- `Topic` dataclass: `name`, `keywords`, `description`, `papers`
- `TopicFilter.run(papers)` → `Dict[topic_name, [paper_with_matched_keywords]]`
- Keyword matching: substring search in `title + abstract` (case-insensitive)
- Papers matching zero topics are excluded; papers can appear under multiple topics
- Config: `topics.json` in project root (forward-compatible schema with `description` + `papers` for future Claude AI scoring)

### `app.py` — local web UI

- Flask server with 5 routes: `GET /`, `GET/POST /api/topics`, `DELETE /api/topics/<name>`, `POST /api/fetch`
- Imports `LiteratureMonitor` from `monitor` and `TopicFilter`, `load_topics` from `filter`
- Single-page UI in `templates/index.html`: Topics tab (add/remove) + Papers tab (fetch + filter by date range)
- `topics.json` is read/written at project root

## Planned

- Claude API integration: summarize papers from abstract + introduction