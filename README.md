# PaperFoundry

Local arXiv paper triage. Fetch recent papers from arXiv categories, then classify them against your own research topics using a locally-hosted LLM via [Ollama](https://ollama.com).

Pipeline:

```
PaperFoundry.monitor  →  papers.json  →  PaperFoundry.filter  →  filtered.json
                                              ↑
                          PaperFoundry.topics (topics/*.md)
                          PaperFoundry.llm    (Ollama backend)
```

## Requirements

- Python 3.9+
- [Ollama](https://ollama.com) installed and running on `localhost:11434`
- At least one model pulled (e.g. `ollama pull gemma3:4b`)

```bash
pip install requests ollama
```

## Quick start

**1. Fetch recent papers from an arXiv category**

```bash
python -m PaperFoundry.monitor cs.GR -o papers.json --max 50
```

Re-running against the same `papers.json` does an incremental fetch — known IDs are skipped until `--max` new papers are collected.

**2. Define your topics**

Create one markdown file per topic under `topics/`:

```markdown
# Neural BRDF

## Description
Neural network models that learn to represent, approximate, or generalize
the bidirectional reflectance distribution function (BRDF)...

## Keywords
- Neural BRDF
- SVBRDF
- differentiable BRDF

## Papers
- Real-Time Neural Appearance Models (Zeltner, 2024)
```

The `# Title` is required. `## Description` is free-form prose; `## Keywords` and `## Papers` are bullet lists.

**3. Filter papers against your topics**

```bash
python -m PaperFoundry.filter papers.json --model gemma3:4b -o filtered.json
```

The LLM reads each paper's title + abstract and returns `match` / `maybe` / `no` with a one-sentence reason. Only `match`/`maybe` results are kept.

Preview the prompts without touching Ollama:

```bash
python -m PaperFoundry.filter papers.json --dry-run
```

## Using PaperFoundry as a library

```python
from PaperFoundry import LiteratureMonitor, LLMClient, FastFilter, load_topics, load_papers

# Fetch
monitor = LiteratureMonitor(["cs.GR"], max_per_source=50)
monitor.fetch()
monitor.save("papers.json")

# Filter
papers = load_papers("papers.json")
topics = load_topics("topics")

client = LLMClient(model="gemma3:4b")
client.load(keep_alive="30m")

results = FastFilter(llm=client).run(topics, papers)
```

Top-level exports: `LLMClient`, `Paper`, `ArxivFetcher`, `LiteratureMonitor`, `Topic`, `load_topics`, `FastFilter`, `load_papers`, `save_results`, `format_results`. Submodules are lazy-loaded, so `from PaperFoundry.llm import LLMClient` also works without pre-loading the rest of the package.

## Layout

```
PaperFoundry/           # the package
    llm.py              # Ollama wrapper (LLMClient)
    monitor.py          # arXiv fetcher (LiteratureMonitor, ArxivFetcher, Paper)
    topics.py           # Topic dataclass + markdown loader
    filter.py           # FastFilter + CLI
    prompts/
        fast.prompt     # FastFilter system + user prompt template
topics/                 # your topic definitions (one .md per topic)
papers_*.json           # fetched papers (output of monitor)
```

## CLI reference

Each module also runs as a debug CLI via `python -m PaperFoundry.<module>`:

| Command | Purpose |
|---|---|
| `python -m PaperFoundry.monitor <category> [-o FILE] [--max N] [--date D] [--from D --to D]` | Fetch papers |
| `python -m PaperFoundry.llm [--loaded \| --load \| --unload \| --prompt ...]` | Inspect / control Ollama models |
| `python -m PaperFoundry.filter <papers.json> [--model M] [--topics PATH] [--paper ID] [--verbose] [--dry-run]` | Filter papers against topics |

See `CLAUDE.md` for the full command catalogue and architecture notes.

## Notes

- `PaperFoundry.llm` never starts the Ollama service — it only connects. If the service isn't running or the model isn't pulled, you get a clear `RuntimeError` at client construction.
- `LLMClient.generate` does **not** auto-load the model. Call `client.load(keep_alive=...)` once, then call `generate` repeatedly.
- Prompts live in `PaperFoundry/prompts/` as section-tagged (`[system]` / `[user]`) plain-text files so they can be edited without touching code.
