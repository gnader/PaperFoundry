"""PaperFoundry web UI.

A local Flask server exposing the monitor + filter pipeline via a simple
single-page UI.

Usage:
    python app.py
    # then open http://localhost:5000
"""

import json
import logging
import os
import shutil
import time

import requests as http_requests
from flask import Flask, Response, jsonify, render_template, request

from analyze import PaperAnalyzer
from filter import Topic, TopicFilter, load_topics
from monitor import LiteratureMonitor

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

app = Flask(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_PATH = os.path.join(_BASE_DIR, "topics.json")
LIBRARY_PATH = os.path.join(_BASE_DIR, "library.json")
TMP_DIR = os.path.join(_BASE_DIR, "tmp")


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


def _cleanup_tmp(tmp_dir: str) -> None:
    """Remove a temp directory, retrying on Windows file-lock errors."""
    if not os.path.exists(tmp_dir):
        return
    for attempt in range(5):
        try:
            shutil.rmtree(tmp_dir)
            return
        except PermissionError:
            time.sleep(0.3)
    # Last-ditch attempt — log if cleanup fails so user can inspect ./tmp/
    try:
        shutil.rmtree(tmp_dir)
    except Exception as e:
        logging.warning("Could not remove %s: %s (clean up manually)", tmp_dir, e)


def _to_arxiv_date(value: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD for arXiv query syntax."""
    return value.replace("-", "")


# ============================================================================
# Routes — UI
# ============================================================================


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tmp", methods=["DELETE"])
def clean_tmp():
    """Remove all files in the tmp directory."""
    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    app.logger.info("Cleaned tmp directory")
    return jsonify({"message": "Tmp folder cleaned"})


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


@app.route("/api/topics/<path:name>", methods=["PUT"])
def update_topic(name: str):
    body = request.get_json(force=True)
    data = _read_topics_json()
    topic = next((t for t in data["topics"] if t.get("name") == name), None)
    if not topic:
        return jsonify({"error": f"Topic '{name}' not found"}), 404

    if "description" in body:
        topic["description"] = body["description"]
    if "keywords" in body:
        if not isinstance(body["keywords"], list):
            return jsonify({"error": "keywords must be a list"}), 400
        topic["keywords"] = body["keywords"]

    _write_topics_json(data)
    return jsonify({"ok": True})


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


@app.route("/api/topics/<path:name>/extract-keywords", methods=["POST"])
def extract_paper_keywords(name: str):
    body = request.get_json(force=True)
    paper_id = (body.get("paper_id") or "").strip()
    if not paper_id:
        return jsonify({"error": "paper_id is required"}), 400

    data = _read_topics_json()
    topic = next((t for t in data["topics"] if t.get("name") == name), None)
    if not topic:
        return jsonify({"error": f"Topic '{name}' not found"}), 404

    # Download PDF from arXiv
    tmp_dir = os.path.join(TMP_DIR, f"extract-{paper_id.replace('/', '_')}")
    os.makedirs(tmp_dir, exist_ok=True)
    pdf_path = os.path.join(tmp_dir, f"{paper_id.replace('/', '_')}.pdf")
    analyzer = None
    try:
        url = f"https://arxiv.org/pdf/{paper_id}"
        resp = http_requests.get(url, timeout=30)
        resp.raise_for_status()
        with open(pdf_path, "wb") as f:
            f.write(resp.content)

        analyzer = PaperAnalyzer(pdf_path)
        keywords = analyzer.extract_keywords(top_n=20)
        return jsonify({"keywords": keywords})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if analyzer is not None:
            try:
                analyzer.close()
            except Exception:
                pass
        _cleanup_tmp(tmp_dir)


# ============================================================================
# Routes — Analysis API
# ============================================================================


@app.route("/api/analyze-paper", methods=["POST"])
def analyze_paper():
    """Analyze a single paper: extract keywords, check citations, score relevance."""
    body = request.get_json(force=True)
    paper_id = body.get("paper_id", "")
    topic_name = body.get("topic_name", "")

    if not paper_id:
        return jsonify({"error": "paper_id is required"}), 400

    # Load topic config for keyword matching + citation checking
    topic_keywords = []
    associated_papers = []
    if topic_name:
        data = _read_topics_json()
        topic = next((t for t in data["topics"] if t.get("name") == topic_name), None)
        if topic:
            topic_keywords = topic.get("keywords", [])
            associated_paper_ids = topic.get("papers", [])
            library = _read_library()
            lib_by_id = {p["id"]: p for p in library.get("papers", [])}
            for pid in associated_paper_ids:
                if pid in lib_by_id:
                    lp = lib_by_id[pid]
                    authors_str = lp["authors"][0] if lp.get("authors") else ""
                    associated_papers.append({"title": lp.get("title", ""), "authors": authors_str})

    tmp_dir = os.path.join(TMP_DIR, f"analyze-{paper_id.replace('/', '_')}")
    os.makedirs(tmp_dir, exist_ok=True)
    analyzer = None

    try:
        pdf_path = os.path.join(tmp_dir, f"{paper_id.replace('/', '_')}.pdf")
        app.logger.info("Downloading %s", paper_id)
        url = f"https://arxiv.org/pdf/{paper_id}"
        resp = http_requests.get(url, timeout=30)
        resp.raise_for_status()
        with open(pdf_path, "wb") as f:
            f.write(resp.content)

        app.logger.info("Analyzing %s", paper_id)
        analyzer = PaperAnalyzer(pdf_path)
        extracted_keywords = analyzer.extract_keywords(top_n=20)

        # Extract full reference list
        references = analyzer.extract_references()

        # Citation checking
        citations = []
        if associated_papers:
            citations = analyzer.is_cited(associated_papers)

        # Relevance scoring
        score = 0.0
        matched = set()
        topic_kws_lower = {k.lower() for k in topic_keywords}
        for kw, kw_score in extracted_keywords:
            kw_lower = kw.lower()
            if kw_lower in topic_kws_lower:
                score += kw_score
                matched.add(kw)
            else:
                for tkw in topic_kws_lower:
                    if kw_lower in tkw or tkw in kw_lower:
                        score += kw_score * 0.5
                        matched.add(kw)
                        break
        for cr in citations:
            if cr.get("cited"):
                score += 2.0

        result = {
            "relevance_score": round(score, 2),
            "extracted_keywords": extracted_keywords,
            "matched_keywords": list(matched),
            "citations": citations,
            "references": references,
            "error": None,
        }
        app.logger.info("Done %s — score: %.1f", paper_id, score)
        return jsonify(result)

    except Exception as e:
        app.logger.error("Error analyzing %s: %s", paper_id, e)
        return jsonify({"error": str(e)}), 500
    finally:
        if analyzer is not None:
            try:
                analyzer.close()
            except Exception:
                pass
        _cleanup_tmp(tmp_dir)


@app.route("/api/analyze-topic", methods=["POST"])
def analyze_topic():
    body = request.get_json(force=True)
    topic_name = body.get("topic_name", "")
    papers = body.get("papers", [])

    if not topic_name or not papers:
        return jsonify({"error": "topic_name and papers are required"}), 400

    # Load topic config
    data = _read_topics_json()
    topic = next((t for t in data["topics"] if t.get("name") == topic_name), None)
    if not topic:
        return jsonify({"error": f"Topic '{topic_name}' not found"}), 404

    topic_keywords = topic.get("keywords", [])
    associated_paper_ids = topic.get("papers", [])

    # Resolve associated papers from library for citation checking
    library = _read_library()
    lib_by_id = {p["id"]: p for p in library.get("papers", [])}
    associated_papers = []
    for pid in associated_paper_ids:
        if pid in lib_by_id:
            lp = lib_by_id[pid]
            authors_str = lp["authors"][0] if lp.get("authors") else ""
            associated_papers.append({"title": lp.get("title", ""), "authors": authors_str})

    total = len(papers)

    def _sse_event(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def generate():
        tmp_dir = os.path.join(TMP_DIR, f"analyze-{int(time.time())}")
        os.makedirs(tmp_dir, exist_ok=True)
        results = []

        try:
            for i, paper in enumerate(papers):
                pid = paper.get("id", "")
                result = {
                    "id": pid,
                    "title": paper.get("title", ""),
                    "relevance_score": 0,
                    "extracted_keywords": [],
                    "matched_keywords": [],
                    "citations": [],
                    "references": [],
                    "error": None,
                }

                if not pid:
                    result["error"] = "No paper ID"
                    results.append(result)
                    yield _sse_event({"type": "error", "paper_id": pid, "message": "No paper ID"})
                    continue

                pdf_path = os.path.join(tmp_dir, f"{pid.replace('/', '_')}.pdf")
                analyzer = None
                try:
                    # Rate limit: 1s delay between downloads (skip first)
                    if i > 0:
                        time.sleep(1)

                    app.logger.info("[%d/%d] Downloading %s", i + 1, total, pid)
                    yield _sse_event({"type": "progress", "paper_id": pid, "step": "downloading", "index": i + 1, "total": total})

                    url = f"https://arxiv.org/pdf/{pid}"
                    resp = http_requests.get(url, timeout=30)
                    resp.raise_for_status()
                    with open(pdf_path, "wb") as f:
                        f.write(resp.content)

                    app.logger.info("[%d/%d] Analyzing %s", i + 1, total, pid)
                    yield _sse_event({"type": "progress", "paper_id": pid, "step": "analyzing", "index": i + 1, "total": total})

                    analyzer = PaperAnalyzer(pdf_path)
                    extracted_keywords = analyzer.extract_keywords(top_n=20)
                    result["extracted_keywords"] = extracted_keywords
                    result["references"] = analyzer.extract_references()

                    # Citation checking
                    if associated_papers:
                        cite_results = analyzer.is_cited(associated_papers)
                        result["citations"] = cite_results

                    # Relevance scoring
                    score = 0.0
                    topic_kws_lower = {k.lower() for k in topic_keywords}
                    matched = set()

                    for kw, kw_score in extracted_keywords:
                        kw_lower = kw.lower()
                        if kw_lower in topic_kws_lower:
                            score += kw_score
                            matched.add(kw)
                        else:
                            for tkw in topic_kws_lower:
                                if kw_lower in tkw or tkw in kw_lower:
                                    score += kw_score * 0.5
                                    matched.add(kw)
                                    break

                    # Citation bonus
                    for cr in result.get("citations", []):
                        if cr.get("cited"):
                            score += 2.0

                    result["relevance_score"] = round(score, 2)
                    result["matched_keywords"] = list(matched)

                    app.logger.info("[%d/%d] Done %s — score: %.1f", i + 1, total, pid, result["relevance_score"])
                    yield _sse_event({"type": "progress", "paper_id": pid, "step": "done", "index": i + 1, "total": total, "score": result["relevance_score"]})

                except Exception as e:
                    result["error"] = str(e)
                    app.logger.error("[%d/%d] Error %s: %s", i + 1, total, pid, e)
                    yield _sse_event({"type": "error", "paper_id": pid, "message": str(e)})
                finally:
                    if analyzer is not None:
                        try:
                            analyzer.close()
                        except Exception:
                            pass

                results.append(result)
        finally:
            _cleanup_tmp(tmp_dir)

        # Sort by relevance score descending
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        app.logger.info("Analysis complete for topic '%s' — %d papers", topic_name, len(results))
        yield _sse_event({"type": "complete", "papers": results})

    return Response(generate(), mimetype="text/event-stream")


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
