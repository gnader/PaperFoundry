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

# Analyze a PDF (text extraction, sections, keywords)
python analyze.py paper.pdf -o output.json
python analyze.py paper.pdf --debug-text

# Check if a specific paper is cited
python analyze.py paper.pdf --check-cite "Paper Title" --check-author "Smith"

# Check multiple papers from a JSON list
python analyze.py paper.pdf --check-cited papers.json

# Extract all references with importance scores
python analyze.py paper.pdf --refs
python analyze.py paper.pdf --refs --top 10
python analyze.py paper.pdf --refs -o refs.json

# Start the local web UI
python app.py  # opens at http://localhost:5000
```

## Dependencies

```bash
pip install pymupdf requests flask keybert marker-pdf pypdf
```

`requests` is optional for `bibextract.py` (skips OpenAlex enrichment if absent) but required for `monitor.py`. `flask` is required for `app.py`. `keybert` is required for `analyze.py` keyword extraction. `marker-pdf` is required for `analyze.py` (ML-based PDF→Markdown; pulls in PyTorch as a dependency). `pypdf` is used for PDF metadata extraction in `analyze.py`. `pymupdf` is still required for `bibextract.py`.

## Architecture

Independent single-file modules, no shared code:

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

### `analyze.py` — PDF text extraction, sections, keywords, citation checking

Uses `marker-pdf` (ML-based PDF→Markdown converter) for layout-aware text extraction — handles multi-column, tables, equations, headers/footers automatically. Uses `pypdf` for metadata extraction.

`PaperAnalyzer` class:
- **`__init__`**: runs marker-pdf conversion, strips markdown to plain text, detects sections, builds `section_map`. Accepts optional `model_dict` to share pre-loaded marker models across calls.
- **`section_map`**: `Dict[str, Tuple[int, Optional[int]]]` — maps section title → (start, end) char positions in `full_text`
- **`extract_title()`** → pypdf metadata first, then first markdown heading
- **`extract_keywords(top_n)`** → KeyBERT keyword extraction from introduction/abstract
- **`is_cited(papers)`** → checks if papers are cited: finds each paper in references, extracts citation key (bracket `[1]`/`[WAF23]` or author name), then searches all sections for that key
- **`extract_references(top_n)`** → extracts all references as structured data (tag, authors, title, year), enriches with citation locations and importance scores (section-weighted), sorted by importance descending
- **`summary()`** → title + keywords + sections overview
- **`close()`** → no-op for backward compat

Module-level `get_model_dict()` caches marker models for reuse across multiple PDFs.

### `app.py` — local web UI

- Flask server with 5 routes: `GET /`, `GET/POST /api/topics`, `DELETE /api/topics/<name>`, `POST /api/fetch`
- Imports `LiteratureMonitor` from `monitor` and `TopicFilter`, `load_topics` from `filter`
- Single-page UI in `templates/index.html`: Topics tab (add/remove) + Papers tab (fetch + filter by date range)
- `topics.json` is read/written at project root

## Planned

- Claude API integration: summarize papers from abstract + introduction