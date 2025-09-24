# NewsAPI General & Health Collector

This repository contains a small utility that downloads recent NewsAPI articles
for the **general** and **health** categories via the `/v2/everything`
endpoint. The script mirrors the adaptive window-splitting strategy shown in
the reference example, ensuring the free API quota is respected while
retrieving as many articles as possible.

## Prerequisites

* Python 3.10+
* `requests` library (`pip install requests`)
* A NewsAPI key (already embedded per the user request, but you can override it
  via the `NEWSAPI_KEY` environment variable).

## Usage

```bash
python news_collector.py
```

The script creates a `data/` directory (if it does not already exist) and
writes a timestamped JSON file containing the collected items. Each entry
includes metadata such as the collection window, depth within the recursive
splitting, and the originating category query.

## Configuration Highlights

* **Categories** – Defined in `CATEGORY_QUERIES` within
  [`news_collector.py`](news_collector.py). By default both `general` and
  `health` are queried.
* **Date Window** – Targets articles published between 48 and 24 hours ago to
  align with the NewsAPI free-plan latency.
* **Rate Limiting** – The script keeps track of the remaining API calls
  (`CALLS_BUDGET`) and recursively splits time windows when a query indicates
  more than 100 results are available.

Feel free to adjust these constants to match your needs or budget.
