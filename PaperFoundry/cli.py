"""papertrack — unified CLI for the PaperFoundry pipeline.

Wires `monitor` (arXiv fetch + per-source JSON cache) → `filter` (LLM topic
classification) → a Markdown report. Reads model and topics path from a TOML
config so day-to-day usage is just:

    papertrack --arxiv cs.GR --date today
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .filter import FastFilter
from .monitor import LiteratureMonitor, Paper
from .topics import Topic, load_topics

DATE_PRESETS = ("today", "this-week", "this-month")


# ============================================================================
# Config
# ============================================================================


def _config_search_paths(explicit: Optional[Path]) -> List[Path]:
    if explicit:
        return [explicit]
    return [Path.cwd() / "papertrack.toml", Path.home() / ".papertrack" / "config.toml"]


def load_config(explicit: Optional[Path]) -> Tuple[dict, Optional[Path]]:
    """Return (config_dict, path_used). Empty dict if no file found."""
    for path in _config_search_paths(explicit):
        if path.is_file():
            with path.open("rb") as f:
                return tomllib.load(f), path
    if explicit:
        raise FileNotFoundError(f"Config file not found: {explicit}")
    return {}, None


# ============================================================================
# Date handling
# ============================================================================


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _hyphen(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def resolve_date_range(preset: Optional[str], date_from: Optional[str], date_to: Optional[str],
                       today: Optional[date] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return (from, to) as YYYYMMDD strings, or (None, None) if unspecified.

    Calendar-aligned semantics:
      today       → (today, today)
      this-week   → (Monday-of-this-week, today)
      this-month  → (1st-of-month, today)
    """
    if preset and (date_from or date_to):
        raise ValueError("--date cannot be combined with --from/--to")

    today = today or datetime.now(timezone.utc).date()

    if preset == "today":
        return _ymd(today), _ymd(today)
    if preset == "this-week":
        start = today - timedelta(days=today.weekday())  # Monday
        return _ymd(start), _ymd(today)
    if preset == "this-month":
        start = today.replace(day=1)
        return _ymd(start), _ymd(today)
    if preset is not None:
        raise ValueError(f"Unknown date preset: {preset!r}. Choices: {', '.join(DATE_PRESETS)}")

    def _parse(v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"Invalid date {v!r}: expected YYYY-MM-DD") from e
        return v.replace("-", "")

    return (_parse(date_from) if date_from else None,
            _parse(date_to) if date_to else None)


def _in_range(published: str, date_from: Optional[str], date_to: Optional[str]) -> bool:
    """Lex-compare ISO published date (first 10 chars) against YYYYMMDD bounds."""
    if not (date_from or date_to):
        return True
    if not published:
        return False
    iso = published[:10].replace("-", "")
    if date_from and iso < date_from:
        return False
    if date_to and iso > date_to:
        return False
    return True


# ============================================================================
# Cache
# ============================================================================


def resolve_category(source: str) -> str:
    """Same logic as ArxivFetcher._resolve_category — kept here for cache naming."""
    if source.startswith("http"):
        m = re.search(r"arxiv\.org/list/([^/?#]+)", source)
        if not m:
            raise ValueError(f"Cannot extract arXiv category from URL: {source}")
        return m.group(1)
    return source


def cache_path_for(cache_dir: Path, source: str) -> Path:
    return cache_dir / f"{resolve_category(source)}.json"


# ============================================================================
# Topic selection
# ============================================================================


def select_topics(all_topics: List[Topic], wanted: str) -> List[Topic]:
    if wanted == "all":
        return all_topics
    target = wanted.strip().lower()
    matched = [t for t in all_topics if t.name.lower() == target]
    if not matched:
        names = ", ".join(t.name for t in all_topics)
        raise ValueError(f"Topic {wanted!r} not found. Available: {names}")
    return matched


# ============================================================================
# Fetch + filter
# ============================================================================


def fetch_with_cache(sources: List[str], date_from: Optional[str], date_to: Optional[str],
                     max_per_source: int, cache_dir: Optional[Path]) -> Tuple[List[dict], dict]:
    """Fetch papers for each source, using a per-category JSON cache when given.

    Returns (in_range_papers, stats) where stats has per-source new/cached counts.
    """
    monitor = LiteratureMonitor(max_results=max_per_source)
    seen: set = set()
    combined: List[dict] = []
    stats = {"sources": {}, "from_cache": 0, "new": 0}

    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    for source in sources:
        cache_file = cache_path_for(cache_dir, source) if cache_dir else None
        cached_papers: List[Paper] = []
        known_ids: set = set()
        if cache_file and cache_file.is_file():
            try:
                cached_papers = LiteratureMonitor.load(str(cache_file))
                known_ids = {p.id for p in cached_papers}
            except Exception as e:
                print(f"  [warning] Could not load cache {cache_file}: {e}")

        new_papers = monitor.fetch_all(
            [source],
            date_from=date_from,
            date_to=date_to,
            known_ids=known_ids if known_ids else None,
            target_new=max_per_source,
        )

        if cache_file:
            merged = new_papers + cached_papers
            merged.sort(key=lambda p: p.published, reverse=True)
            LiteratureMonitor.save(merged, str(cache_file))

        in_range_cached = sum(1 for p in cached_papers if _in_range(p.published, date_from, date_to))

        for p in new_papers + cached_papers:
            if p.id in seen:
                continue
            if not _in_range(p.published, date_from, date_to):
                continue
            seen.add(p.id)
            combined.append(asdict(p))

        stats["sources"][source] = {"new": len(new_papers), "from_cache": in_range_cached}
        stats["from_cache"] += in_range_cached
        stats["new"] += len(new_papers)

    combined.sort(key=lambda p: p.get("published", ""), reverse=True)
    return combined, stats


_RANK = {"match": 0, "maybe": 1, "no": 2, "error": 3}


def score_all(filt: FastFilter, topics: List[Topic], papers: List[dict]) -> dict:
    """Score every (topic, paper) pair. Return {topic_name: [scored_paper, ...]} keeping all verdicts."""
    results: dict = {}
    for topic in topics:
        scored: List[dict] = []
        for i, paper in enumerate(papers):
            pid = paper.get("id", "?")
            print(f"  [{topic.name}] {i + 1}/{len(papers)}: {pid}", end="\r")
            row = filt.score(topic, paper)
            scored.append(row)
        scored.sort(key=lambda p: (_RANK.get(p.get("match_level", "error"), 4),
                                    -_published_rank(p.get("published", ""))))
        n_match = sum(1 for p in scored if p["match_level"] == "match")
        n_maybe = sum(1 for p in scored if p["match_level"] == "maybe")
        print(f"  [{topic.name}] {n_match} match, {n_maybe} maybe out of {len(papers)}" + " " * 30)
        results[topic.name] = scored
    return results


def _published_rank(published: str) -> int:
    """Sortable int from ISO published date. Newer → larger."""
    if not published:
        return 0
    try:
        return int(published[:10].replace("-", ""))
    except ValueError:
        return 0


# ============================================================================
# Markdown report
# ============================================================================


def write_markdown(meta: dict, results: dict, include_no: bool, output_path: Path) -> None:
    lines: List[str] = []
    lines.append(f"# Paper Triage Report — {meta['today']}")
    lines.append("")
    lines.append(f"- **Sources**: {', '.join(meta['sources'])}")
    lines.append(f"- **Date range**: {meta['date_range']}")
    lines.append(f"- **Model**: {meta['model']}")
    lines.append(f"- **Topics**: {', '.join(meta['topics'])}")
    lines.append(f"- **Papers fetched**: {meta['n_papers']}  ({meta['from_cache']} from cache, {meta['new']} new)")
    total_match = sum(1 for ps in results.values() for p in ps if p["match_level"] == "match")
    total_maybe = sum(1 for ps in results.values() for p in ps if p["match_level"] == "maybe")
    lines.append(f"- **Total matches**: {total_match + total_maybe} ({total_match} match, {total_maybe} maybe)")
    lines.append("")
    lines.append("---")

    for topic_name, papers in results.items():
        n_match = sum(1 for p in papers if p["match_level"] == "match")
        n_maybe = sum(1 for p in papers if p["match_level"] == "maybe")
        lines.append("")
        lines.append(f"## {topic_name}  —  {n_match} match, {n_maybe} maybe")

        buckets = [("match", "✓ Match"), ("maybe", "? Maybe")]
        if include_no:
            buckets.append(("no", "✗ No"))

        for level, header in buckets:
            in_bucket = [p for p in papers if p["match_level"] == level]
            if not in_bucket:
                continue
            lines.append("")
            lines.append(f"### {header}")
            for p in in_bucket:
                lines.append("")
                lines.append(_paper_md(p))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote report to {output_path}")


def _paper_md(p: dict) -> str:
    title = p.get("title", "(no title)")
    url = p.get("url", "")
    pid = p.get("id", "")
    authors = ", ".join(p.get("authors", []))
    published = (p.get("published") or "")[:10]
    reason = p.get("reason", "")
    parts = [
        f"#### [{title}]({url})" if url else f"#### {title}",
        f"- **Authors**: {authors}" if authors else "",
        f"- **Published**: {published}" if published else "",
        f"- **arXiv**: `{pid}`" if pid else "",
        f"- **Why**: {reason}" if reason else "",
    ]
    return "\n".join(s for s in parts if s)


# ============================================================================
# CLI
# ============================================================================


def _default_output(output_dir: Path, today: date, sources: Iterable[str]) -> Path:
    sanitized = [resolve_category(s).replace("/", "_") for s in sources]
    name = f"report_{today.isoformat()}_{'_'.join(sanitized)}.md"
    return output_dir / name


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="papertrack",
        description="Fetch arXiv papers, classify against topics with a local LLM, write a Markdown report.",
    )
    parser.add_argument("--arxiv", nargs="+", metavar="CAT", help="arXiv categories or listing URLs (e.g. cs.GR cs.CV).")
    parser.add_argument("--topic", default="all", metavar="NAME",
                        help='Topic name to filter against, or "all" (default).')
    parser.add_argument("--topic-list", action="store_true", help="List available topics and exit.")
    parser.add_argument("--date", choices=DATE_PRESETS, help="Date preset: today, this-week, this-month.")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="Start date (inclusive).")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="End date (inclusive).")
    parser.add_argument("--all-papers", action="store_true",
                        help="Include non-matched papers in the report (under a 'No' bucket).")
    parser.add_argument("-o", "--output", metavar="FILE", help="Output Markdown path.")
    parser.add_argument("--max", type=int, default=200, help="Max new papers to fetch per source (default: 200).")
    parser.add_argument("--no-cache", action="store_true", help="Bypass per-source JSON cache.")
    parser.add_argument("--config", metavar="PATH", help="Path to a papertrack.toml config file.")
    parser.add_argument("--model", help="Ollama model (overrides config).")
    parser.add_argument("--topics", metavar="PATH", help="Topics dir or single .md (overrides config).")
    parser.add_argument("--host", help="Ollama host (overrides config).")
    parser.add_argument("--keep-alive", help="Keep model in VRAM for this duration (overrides config).")

    args = parser.parse_args(argv)

    cfg, cfg_path = load_config(Path(args.config) if args.config else None)

    model = args.model or cfg.get("model")
    topics_path = args.topics or cfg.get("topics", "topics")
    host = args.host or cfg.get("host", "http://localhost:11434")
    keep_alive = args.keep_alive or cfg.get("keep_alive", "30m")
    cache_dir = None if args.no_cache else Path(cfg.get("cache_dir", ".papertrack_cache"))
    output_dir = Path(cfg.get("output_dir", "."))

    # --topic-list short-circuit (no model needed)
    if args.topic_list:
        topics = load_topics(topics_path)
        print(f"Topics in {topics_path}:")
        for t in topics:
            print(f"  - {t.name}  ({len(t.keywords)} keyword{'s' if len(t.keywords) != 1 else ''})")
        return

    if not args.arxiv:
        parser.error("--arxiv is required (unless using --topic-list)")
    if not model:
        parser.error("--model is required (or set `model = \"...\"` in papertrack.toml)")

    try:
        date_from, date_to = resolve_date_range(args.date, args.date_from, args.date_to)
    except ValueError as e:
        parser.error(str(e))

    today = datetime.now(timezone.utc).date()
    if date_from and date_to:
        date_range_str = f"{_hyphen(_parse_ymd(date_from))} → {_hyphen(_parse_ymd(date_to))}"
    elif date_from:
        date_range_str = f"{_hyphen(_parse_ymd(date_from))} → (open)"
    elif date_to:
        date_range_str = f"(open) → {_hyphen(_parse_ymd(date_to))}"
    else:
        date_range_str = "(no date filter)"

    all_topics = load_topics(topics_path)
    try:
        topics = select_topics(all_topics, args.topic)
    except ValueError as e:
        parser.error(str(e))

    if cfg_path:
        print(f"Config: {cfg_path}")
    print(f"Topics: {len(topics)}/{len(all_topics)} from {topics_path}")
    print(f"Date range: {date_range_str}")

    papers, stats = fetch_with_cache(args.arxiv, date_from, date_to, args.max, cache_dir)
    if not papers:
        print("No papers in range. Nothing to filter.")
        if args.date == "today":
            print("Note: arXiv announces new submissions once per weekday around 20:00 UTC "
                  "(14:00 ET cutoff) and not at all on weekends. Today's batch may not be "
                  "live yet — try `--date this-week` or rerun later.")
        return
    print(f"Total papers in range: {len(papers)} ({stats['from_cache']} cached, {stats['new']} new)")

    from .llm import LLMClient

    client = LLMClient(model=model, host=host)
    ok, msg = client.load(keep_alive=keep_alive)
    if not ok:
        print(f"Failed to load model: {msg}", file=sys.stderr)
        sys.exit(1)
    print(msg)

    try:
        filt = FastFilter(llm=client)
        results = score_all(filt, topics, papers)

        output_path = Path(args.output) if args.output else _default_output(output_dir, today, args.arxiv)
        meta = {
            "today": today.isoformat(),
            "sources": args.arxiv,
            "date_range": date_range_str,
            "model": model,
            "topics": [t.name for t in topics],
            "n_papers": len(papers),
            "from_cache": stats["from_cache"],
            "new": stats["new"],
        }
        write_markdown(meta, results, include_no=args.all_papers, output_path=output_path)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise
    finally:
        ok, msg = client.unload()
        print(msg)


def _parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


if __name__ == "__main__":
    main()
