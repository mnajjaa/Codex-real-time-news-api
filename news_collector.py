"""Collect NewsAPI articles for the general and health categories.

This script mirrors the adaptive window-splitting strategy showcased in the
reference snippet provided by the user.  It keeps API usage within a sensible
budget while iterating over the two requested categories using the
``/v2/everything`` endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests


# ========= SETTINGS =========

# API key provided by the user – ``NEWSAPI_KEY`` env var can override it.
API_KEY = os.getenv("NEWSAPI_KEY", "f5056adf2bf34cd682960e30fbd6759f")

LANGUAGE = "en"
OUTPUT_DIR = Path("data")

# Map of {category: search_query} – both categories requested by the user.
CATEGORY_QUERIES = {
    "general": "general",
    "health": "health",
}

# ``/v2/everything`` on the free plan is delayed ~24h, so query the window
# between 48h and 24h ago to avoid empty responses.
NOW = datetime.now(timezone.utc)
TO_DT = NOW - timedelta(hours=24)
FROM_DT = NOW - timedelta(hours=48)

# Budget/recursion parameters (tuned for the free API tier).
MIN_WINDOW_MINUTES = 45
MAX_DEPTH = 6
CALLS_BUDGET = 90
SLEEP_SECONDS = 0.25

# Optional domain allowlist – leave empty to search across all sources.
DOMAINS_ALLOWLIST: List[str] = []


# ========= INTERNAL STATE =========

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CALLS_LEFT = CALLS_BUDGET


def iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat()


def stable_id(seed: str) -> str:
    """Derive a stable identifier for an article from a deterministic seed."""

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def newsapi_everything(query: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """Execute a single ``/v2/everything`` request with rate-limit handling."""

    global CALLS_LEFT

    if CALLS_LEFT <= 0:
        return {"status": "stop", "reason": "budget_exhausted"}

    params = {
        "q": query,
        "from": start_iso,
        "to": end_iso,
        "language": LANGUAGE,
        "sortBy": "publishedAt",
        "pageSize": 100,
        "page": 1,
        "apiKey": API_KEY,
    }

    if DOMAINS_ALLOWLIST:
        params["domains"] = ",".join(DOMAINS_ALLOWLIST)

    try:
        response = requests.get(
            "https://newsapi.org/v2/everything", params=params, timeout=30
        )
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - propagate API error info to caller
        return {"status": "error", "error": str(exc)}

    if response.status_code == 429:
        return {"status": "ratelimited", "data": payload}

    if response.status_code != 200 or payload.get("status") != "ok":
        return {"status": "error", "http": response.status_code, "data": payload}

    CALLS_LEFT -= 1
    return {"status": "ok", "data": payload}


def collect_window(
    category: str,
    query: str,
    start_dt: datetime,
    end_dt: datetime,
    depth: int = 0,
) -> List[Dict[str, Any]]:
    """Fetch articles for ``query`` within a time window, splitting if needed."""

    global CALLS_LEFT

    items: List[Dict[str, Any]] = []
    if CALLS_LEFT <= 0:
        return items

    start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()
    window_minutes = (end_dt - start_dt).total_seconds() / 60

    response = newsapi_everything(query, start_iso, end_iso)

    if response.get("status") == "stop":
        return items

    if response.get("status") == "ratelimited":
        print(
            f"[STOP] Rate-limited on {category}."
            f" Details: {response.get('data')}"
        )
        CALLS_LEFT = 0
        return items

    if response.get("status") != "ok":
        print(
            f"[WARN] {category} {start_iso[-8:]}→{end_iso[-8:]} error: {response}"
        )
        return items

    data = response["data"]
    total_results = data.get("totalResults", 0) or len(data.get("articles") or [])
    articles = data.get("articles") or []

    print(
        f"[{category}] {start_dt.strftime('%m-%d %H:%M')}→{end_dt.strftime('%H:%M')} "
        f"depth={depth} total≈{total_results} got={len(articles)} "
        f"calls_left={CALLS_LEFT}"
    )

    should_split = (
        total_results >= 100
        and window_minutes > MIN_WINDOW_MINUTES
        and depth < MAX_DEPTH
    )

    if should_split and CALLS_LEFT > 0:
        midpoint = start_dt + (end_dt - start_dt) / 2
        items.extend(collect_window(category, query, start_dt, midpoint, depth + 1))
        if CALLS_LEFT > 0:
            items.extend(collect_window(category, query, midpoint, end_dt, depth + 1))
        return items

    for article in articles:
        url = article.get("url") or ""
        seed = url or (article.get("title", "") + (article.get("publishedAt") or ""))
        items.append(
            {
                "id": stable_id(seed),
                "category": category,
                "query": query,
                "title": article.get("title"),
                "description": article.get("description"),
                "content": article.get("content"),
                "url": url,
                "urlToImage": article.get("urlToImage"),
                "source": (article.get("source") or {}).get("name"),
                "author": article.get("author"),
                "publishedAt": article.get("publishedAt"),
                "collectedAt": iso_now(),
                "window_from": start_iso,
                "window_to": end_iso,
                "depth": depth,
            }
        )

    time.sleep(SLEEP_SECONDS)
    return items


def deduplicate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate articles by URL while preserving order."""

    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []

    for item in items:
        url = item.get("url")
        if url and url in seen:
            continue
        unique.append(item)
        if url:
            seen.add(url)

    return unique


def main() -> None:
    """Collect news articles for the configured categories and save to JSON."""

    if not API_KEY:
        raise RuntimeError(
            "NewsAPI key missing – set NEWSAPI_KEY or edit API_KEY constant."
        )

    collected: List[Dict[str, Any]] = []

    for category, query in CATEGORY_QUERIES.items():
        if CALLS_LEFT <= 0:
            print("[STOP] Budget exhausted before finishing categories.")
            break

        print(f"\n== Category: {category} (query='{query}') ==")
        collected.extend(collect_window(category, query, FROM_DT, TO_DT))

    unique_items = deduplicate(collected)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    output_path = OUTPUT_DIR / f"newsapi_general_health_{timestamp}.json"

    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(
            {
                "generatedAt": iso_now(),
                "itemCount": len(unique_items),
                "items": unique_items,
                "meta": {
                    "categories": list(CATEGORY_QUERIES.keys()),
                    "from": FROM_DT.isoformat(),
                    "to": TO_DT.isoformat(),
                    "calls_budget": CALLS_BUDGET,
                    "calls_left": CALLS_LEFT,
                    "domains": DOMAINS_ALLOWLIST,
                    "min_window_minutes": MIN_WINDOW_MINUTES,
                    "max_depth": MAX_DEPTH,
                },
            },
            fp,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"\n[DONE] raw={len(collected)} unique={len(unique_items)} "
        f"calls_left={CALLS_LEFT}"
    )
    print(f"[SAVED] {output_path}")


if __name__ == "__main__":
    main()
