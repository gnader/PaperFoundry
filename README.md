# PaperFoundry

Local arXiv paper triage. Fetch recent papers from arXiv categories, classify them against your own research topics using a locally-hosted LLM via [Ollama](https://ollama.com), and get a Markdown report — all from one CLI: `papertrack`.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) installed and running on `localhost:11434`
- At least one model pulled (e.g. `ollama pull gemma3:4b`)

```bash
pip install -e .          # registers the `papertrack` command
```

Editable install — code edits take effect immediately, no reinstall needed. `python -m PaperFoundry` works as an equivalent entry point.

## Quick start

```bash
# list topics
papertrack --topic-list

# today's papers in cs.GR, classified against every topic
papertrack --arxiv cs.GR --date today

# this week, two categories, narrow to one topic
papertrack --arxiv cs.GR cs.CV --date this-week --topic "Neural BRDF"

# explicit window, include non-matched papers in the report
papertrack --arxiv cs.GR --from 2026-01-01 --to 2026-01-31 --all-papers
```

Date presets are calendar-aligned: `today` → today only; `this-week` → Monday-of-this-week → today; `this-month` → 1st-of-month → today. Or pass `--from`/`--to` directly.

`papertrack` keeps a per-category JSON cache (default `.papertrack_cache/`) so reruns only fetch *new* papers from arXiv. Bypass it with `--no-cache`.

The report is written to `report_<DATE>_<sources>.md` in CWD by default; override with `-o`. Within each topic section, papers are grouped `✓ Match` → `? Maybe` → (`✗ No` only when `--all-papers` is set), newest-published first inside each bucket.

The model is loaded once at the start of the run and always unloaded from VRAM before exit (even on Ctrl-C or errors).

### Topics

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

### Config file

Create `papertrack.toml` in the working directory (or `~/.papertrack/config.toml`) so you don't have to retype `--model` and `--topics`:

```toml
model = "gemma3:4b"
topics = "topics"                 # dir or single .md
host = "http://localhost:11434"   # optional
keep_alive = "30m"                # optional
cache_dir = ".papertrack_cache"   # optional
output_dir = "."                  # optional
```

CLI flags always override config values. Override the lookup with `--config PATH`.

### All flags

| Flag | Purpose |
|---|---|
| `--arxiv CAT [CAT ...]` | arXiv categories or listing URLs (required unless `--topic-list`) |
| `--date {today,this-week,this-month}` | Calendar-aligned date preset |
| `--from YYYY-MM-DD`, `--to YYYY-MM-DD` | Explicit date window (mutually exclusive with `--date`) |
| `--topic NAME` | Single topic by exact name (case-insensitive). Default: `all` |
| `--topic-list` | List available topics and exit |
| `--all-papers` | Include non-matched papers under a `No` bucket |
| `-o FILE` | Output Markdown path |
| `--max N` | Max new papers to fetch per source (default: 200) |
| `--no-cache` | Bypass the per-source JSON cache |
| `--config PATH` | Override the config-file search path |
| `--model`, `--topics`, `--host`, `--keep-alive` | Override config values |

## Layout

```
PaperFoundry/           # the package
    cli.py              # papertrack — unified CLI
    __main__.py         # `python -m PaperFoundry` → cli.main
    llm.py              # Ollama wrapper
    monitor.py          # arXiv fetcher
    topics.py           # topic markdown loader
    filter.py           # LLM-backed topic filter
    prompts/
        fast.prompt     # classification prompt template
topics/                 # your topic definitions (one .md per topic)
papertrack.toml         # optional config
.papertrack_cache/      # per-category JSON cache (managed by papertrack)
```

## Notes

- arXiv announces new submissions once per weekday (~20:00 UTC, 14:00 ET cutoff) and not at all on weekends. `--date today` may legitimately return zero papers if run before the daily announcement.
- Prompts live in `PaperFoundry/prompts/` as section-tagged (`[system]` / `[user]`) plain-text files so they can be edited without touching code.
- `PaperFoundry.llm` never starts the Ollama service — it only connects. If the service isn't running or the model isn't pulled, you get a clear error at startup.
