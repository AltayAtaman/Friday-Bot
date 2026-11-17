"""
Collect fresh, topic-agnostic AI & software engineering releases/tools from
GitHub, Hacker News (Show HN / front page), and Hugging Face APIs.

Filters out URLs already tracked in seen_tech.json and prints compact JSON
for verification against anti-hype proof requirements.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

SKILL_DIR = Path(__file__).resolve().parent.parent
SEEN_FILE = SKILL_DIR / "seen_tech.json"
USER_AGENT = "friday-ai-radar/1.0"


def _fetch_json(url: str, timeout: int = 12) -> Any:
    """Fetch JSON payload from url with a clean User-Agent."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _normalize_url(url: str) -> str:
    """Normalize repo/page URLs for reliable deduplication."""
    u = (url or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _load_seen() -> Set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {_normalize_url(str(x)) for x in data if x}
    except Exception:
        pass
    return set()


def fetch_github_candidates(days: int = 7) -> List[Dict[str, Any]]:
    """Query broad newly created GitHub repositories across all technical domains."""
    now = datetime.now(timezone.utc)
    cutoff_week = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    cutoff_recent = (now - timedelta(days=2)).strftime("%Y-%m-%d")

    queries = [
        ("github_7d_rising", f"created:>{cutoff_week} stars:>=60", 12),
        ("github_48h_fresh", f"created:>{cutoff_recent} stars:>=20", 10),
    ]
    out: List[Dict[str, Any]] = []
    for source_tag, q, per_page in queries:
        encoded = urllib.parse.quote(q, safe="")
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={encoded}&sort=stars&order=desc&per_page={per_page}"
        )
        try:
            data = _fetch_json(url)
            for item in data.get("items", []):
                lic = (item.get("license") or {}).get("spdx_id") or "unknown"
                out.append({
                    "source": source_tag,
                    "title": item.get("full_name", ""),
                    "url": item.get("html_url", ""),
                    "description": item.get("description") or "",
                    "language": item.get("language") or "",
                    "license": lic,
                    "stars": item.get("stargazers_count", 0),
                    "topics": (item.get("topics") or [])[:6],
                    "created_at": item.get("created_at", ""),
                })
        except Exception as e:
            out.append({"source": source_tag, "error": str(e)})
    return out


def fetch_hackernews_candidates(hours: int = 48) -> List[Dict[str, Any]]:
    """Query Hacker News Show HN and high-signal technical stories."""
    since_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    endpoints = [
        (
            "hn_show",
            f"https://hn.algolia.com/api/v1/search?tags=show_hn&numericFilters=created_at_i>{since_ts},points>=15&hitsPerPage=12",
        ),
        (
            "hn_top_stories",
            f"https://hn.algolia.com/api/v1/search?tags=story&numericFilters=created_at_i>{since_ts},points>=60&hitsPerPage=15",
        ),
    ]
    out: List[Dict[str, Any]] = []
    for source_tag, url in endpoints:
        try:
            data = _fetch_json(url)
            for hit in data.get("hits", []):
                target_url = hit.get("url") or ""
                if not target_url:
                    continue
                out.append({
                    "source": source_tag,
                    "title": hit.get("title", ""),
                    "url": target_url,
                    "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "created_at": hit.get("created_at", ""),
                })
        except Exception as e:
            out.append({"source": source_tag, "error": str(e)})
    return out


def fetch_huggingface_candidates() -> List[Dict[str, Any]]:
    """Query Hugging Face Daily Papers (with code) and trending models."""
    out: List[Dict[str, Any]] = []
    try:
        papers = _fetch_json("https://huggingface.co/api/daily_papers?limit=10")
        if isinstance(papers, list):
            for entry in papers:
                paper = entry.get("paper") or {}
                github_repo = paper.get("githubRepo") or entry.get("githubRepo") or ""
                pid = paper.get("id", "")
                out.append({
                    "source": "hf_daily_papers",
                    "title": entry.get("title") or paper.get("title") or "",
                    "url": github_repo or (f"https://huggingface.co/papers/{pid}" if pid else ""),
                    "paper_url": f"https://huggingface.co/papers/{pid}" if pid else "",
                    "github_repo": github_repo,
                    "upvotes": paper.get("upvotes", 0),
                    "summary": (paper.get("summary") or "")[:280],
                })
    except Exception as e:
        out.append({"source": "hf_daily_papers", "error": str(e)})

    try:
        models = _fetch_json("https://huggingface.co/api/models?sort=trendingScore&limit=10")
        if isinstance(models, list):
            for m in models:
                mid = m.get("id") or m.get("modelId") or ""
                if not mid:
                    continue
                out.append({
                    "source": "hf_trending_models",
                    "title": mid,
                    "url": f"https://huggingface.co/{mid}",
                    "pipeline_tag": m.get("pipeline_tag") or "",
                    "likes": m.get("likes", 0),
                    "downloads": m.get("downloads", 0),
                    "trending_score": m.get("trendingScore", 0),
                })
    except Exception as e:
        out.append({"source": "hf_trending_models", "error": str(e)})

    return out


def collect(days: int, hours: int) -> Dict[str, Any]:
    seen = _load_seen()
    raw = (
        fetch_github_candidates(days=days)
        + fetch_hackernews_candidates(hours=hours)
        + fetch_huggingface_candidates()
    )

    errors: List[Dict[str, Any]] = []
    deduped: Dict[str, Dict[str, Any]] = {}
    repo_name_counts: Dict[str, int] = {}
    skipped_seen = 0

    for item in raw:
        if "error" in item:
            continue
        title = str(item.get("title", ""))
        if "/" in title and item.get("source", "").startswith("github_"):
            rname = title.split("/", 1)[1].strip().lower()
            repo_name_counts[rname] = repo_name_counts.get(rname, 0) + 1

    for item in raw:
        if "error" in item:
            errors.append(item)
            continue
        title = str(item.get("title", ""))
        if "/" in title and item.get("source", "").startswith("github_"):
            rname = title.split("/", 1)[1].strip().lower()
            if repo_name_counts.get(rname, 0) > 2:
                continue
        url = item.get("url", "").strip()
        norm = _normalize_url(url)
        if not norm:
            continue
        if norm in seen:
            skipped_seen += 1
            continue
        if norm not in deduped:
            deduped[norm] = item

    return {
        "ok": True,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "seen_count": len(seen),
        "skipped_seen": skipped_seen,
        "candidate_count": len(deduped),
        "errors": errors,
        "candidates": list(deduped.values()),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Collect fresh non-hype AI & dev engineering candidates.")
    parser.add_argument("--days", type=int, default=7, help="GitHub lookback window in days (default: 7)")
    parser.add_argument("--hours", type=int, default=48, help="Hacker News lookback window in hours (default: 48)")
    args = parser.parse_args()
    result = collect(days=args.days, hours=args.hours)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
