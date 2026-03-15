"""PaperFoundry web UI.

A local Flask server exposing the monitor + filter pipeline via a simple
single-page UI.

Usage:
    python app.py
    # then open http://localhost:5000
"""

import json
import os

from flask import Flask, jsonify, render_template, request

from filter import Topic, TopicFilter, load_topics
from monitor import LiteratureMonitor

app = Flask(__name__)

TOPICS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topics.json")
LIBRARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.json")


# ============================================================================
# Helpers
# ============================================================================


def _read_topics_json() -> dict:
    if not os.path.exists(TOPICS_PATH):
        return {"topics": []}
    with open(TOPICS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_topics_json(data: dict) -> None:
    with open(TOPICS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_library() -> dict:
    if not os.path.exists(LIBRARY_PATH):
        return {"papers": []}
    with open(LIBRARY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_library(data: dict) -> None:
    with open(LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _to_arxiv_date(value: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD for arXiv query syntax."""
    return value.replace("-", "")


# ============================================================================
# Routes — UI
# ============================================================================


@app.route("/")
def index():
    return render_template("index.html")


# ============================================================================
# Routes — Topics API
# ============================================================================


@app.route("/api/topics", methods=["GET"])
def get_topics():
    data = _read_topics_json()
    return jsonify(data.get("topics", []))


@app.route("/api/topics", methods=["POST"])
def add_topic():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    keywords = body.get("keywords") or []
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not isinstance(keywords, list):
        return jsonify({"error": "keywords must be a list"}), 400

    data = _read_topics_json()
    topics = data.setdefault("topics", [])

    if any(t.get("name") == name for t in topics):
        return jsonify({"error": f"Topic '{name}' already exists"}), 409

    topics.append({"name": name, "keywords": keywords, "description": "", "papers": []})
    _write_topics_json(data)
    return jsonify({"ok": True}), 201


@app.route("/api/topics/<path:name>", methods=["DELETE"])
def delete_topic(name: str):
    data = _read_topics_json()
    topics = data.get("topics", [])
    new_topics = [t for t in topics if t.get("name") != name]
    if len(new_topics) == len(topics):
        return jsonify({"error": f"Topic '{name}' not found"}), 404
    data["topics"] = new_topics
    _write_topics_json(data)
    return jsonify({"ok": True})


@app.route("/api/topics/<path:name>/papers", methods=["POST"])
def add_paper_to_topic(name: str):
    body = request.get_json(force=True)
    paper_id = (body.get("paper_id") or "").strip()
    if not paper_id:
        return jsonify({"error": "paper_id is required"}), 400

    data = _read_topics_json()
    topic = next((t for t in data["topics"] if t.get("name") == name), None)
    if not topic:
        return jsonify({"error": f"Topic '{name}' not found"}), 404

    papers = topic.setdefault("papers", [])
    if paper_id not in papers:
        papers.append(paper_id)
        _write_topics_json(data)
    return jsonify({"ok": True})


@app.route("/api/topics/<path:name>/papers/<path:paper_id>", methods=["DELETE"])
def remove_paper_from_topic(name: str, paper_id: str):
    data = _read_topics_json()
    topic = next((t for t in data["topics"] if t.get("name") == name), None)
    if not topic:
        return jsonify({"error": f"Topic '{name}' not found"}), 404

    papers = topic.get("papers", [])
    if paper_id not in papers:
        return jsonify({"error": "Paper not in topic"}), 404
    topic["papers"] = [p for p in papers if p != paper_id]
    _write_topics_json(data)
    return jsonify({"ok": True})


# ============================================================================
# Routes — Fetch API
# ============================================================================


@app.route("/api/fetch", methods=["POST"])
def fetch_papers():
    body = request.get_json(force=True)

    categories = body.get("categories") or []
    date_from = body.get("date_from") or None
    date_to = body.get("date_to") or None
    max_results = int(body.get("max_results") or 50)

    if not categories:
        return jsonify({"error": "at least one category is required"}), 400

    # Convert dates to arXiv format
    arxiv_from = _to_arxiv_date(date_from) if date_from else None
    arxiv_to = _to_arxiv_date(date_to) if date_to else None

    # Fetch
    monitor = LiteratureMonitor(max_results=max_results)
    try:
        papers = monitor.fetch_all(categories, date_from=arxiv_from, date_to=arxiv_to)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    # Load topics
    try:
        topics = load_topics(TOPICS_PATH)
    except Exception as e:
        return jsonify({"error": f"Failed to load topics: {e}"}), 500

    # Filter
    paper_dicts = [
        {
            "id": p.id,
            "title": p.title,
            "authors": p.authors,
            "abstract": p.abstract,
            "url": p.url,
            "published": p.published,
            "categories": p.categories,
        }
        for p in papers
    ]

    filt = TopicFilter(topics)
    results = filt.run(paper_dicts)

    # Re-attach abstract (TopicFilter strips it from results)
    paper_by_id = {p["id"]: p for p in paper_dicts}
    for group in results.values():
        for paper in group:
            paper["abstract"] = paper_by_id.get(paper["id"], {}).get("abstract", "")

    total_matched = sum(len(v) for v in results.values())

    # Build response groups
    response_results = [
        {"topic": name, "match_count": len(topic_papers), "papers": topic_papers}
        for name, topic_papers in results.items()
    ]

    # Append unmatched papers as "__other__" group
    matched_ids = {p["id"] for group in results.values() for p in group}
    unmatched = [p for p in paper_dicts if p["id"] not in matched_ids]
    if unmatched:
        response_results.append({
            "topic": "__other__",
            "match_count": len(unmatched),
            "papers": unmatched,
        })

    return jsonify({
        "total_fetched": len(papers),
        "total_matched": total_matched,
        "results": response_results,
    })


# ============================================================================
# Routes — Library API
# ============================================================================


@app.route("/api/library", methods=["GET"])
def get_library():
    return jsonify(_read_library())


@app.route("/api/library", methods=["POST"])
def save_to_library():
    body = request.get_json(force=True)
    incoming = body.get("papers") or []
    if not isinstance(incoming, list):
        return jsonify({"error": "papers must be a list"}), 400

    from datetime import datetime, timezone
    data = _read_library()
    existing_ids = {p["id"] for p in data["papers"]}

    added = 0
    for paper in incoming:
        if paper.get("id") and paper["id"] not in existing_ids:
            paper["saved_at"] = datetime.now(timezone.utc).isoformat()
            data["papers"].append(paper)
            existing_ids.add(paper["id"])
            added += 1

    _write_library(data)
    return jsonify({"ok": True, "added": added, "total": len(data["papers"])})


@app.route("/api/library/<path:paper_id>", methods=["DELETE"])
def delete_from_library(paper_id: str):
    data = _read_library()
    new_papers = [p for p in data["papers"] if p.get("id") != paper_id]
    if len(new_papers) == len(data["papers"]):
        return jsonify({"error": "Paper not found"}), 404
    data["papers"] = new_papers
    _write_library(data)
    return jsonify({"ok": True})


# ============================================================================
# Entry point
# ============================================================================


if __name__ == "__main__":
    app.run(debug=True, port=5000)
