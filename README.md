# Lobstr SDK for Python

A Python SDK for the [Lobstr.io](https://lobstr.io) scraping API.

## Why use this?

The Lobstr API requires a multi-step flow: create a squid, add tasks, start a run, poll for completion, wait for export, then download CSV. This SDK wraps all of that into a single `scrape()` call.

### Before

```python
import requests, time

headers = {"Authorization": "Token YOUR_KEY"}

# 1. Create squid
squid = requests.post("https://api.lobstr.io/v1/squids",
    json={"crawler": "5c55ffbf10fbbc7ee63ebb2bcaa332b7"},
    headers=headers).json()

# 2. Add tasks
requests.post("https://api.lobstr.io/v1/tasks",
    json={"tasks": [{"url": "https://instagram.com/cristiano"}], "squid": squid["id"]},
    headers=headers)

# 3. Start run
run = requests.post("https://api.lobstr.io/v1/runs",
    json={"squid": squid["id"]}, headers=headers).json()

# 4. Poll until done
while True:
    stats = requests.get(f"https://api.lobstr.io/v1/runs/{run['id']}/stats",
        headers=headers).json()
    if stats["is_done"]:
        break
    time.sleep(3)

# 5. Wait for export, download CSV, parse it...
```

### After

```python
from lobstr import Lobstr, Crawlers

client = Lobstr(api_key="YOUR_KEY")
result = client.scrape(
    crawler_id=Crawlers.INSTAGRAM_PROFILE_SCRAPER,
    urls=["https://instagram.com/cristiano"],
)
print(result.data[0])       # parsed dict from CSV
print(result.file_path)     # path to downloaded CSV
```

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/polymorphisma/unofficial-lobstr-sdk.git
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add git+https://github.com/polymorphisma/unofficial-lobstr-sdk.git
```

Or clone and install locally:

```bash
git clone https://github.com/polymorphisma/unofficial-lobstr-sdk.git
cd lobster-sdk
pip install .
```

> **Note:** This package is not yet published on PyPI. Install from GitHub for now.

## Quick Start

```python
import os
from lobstr import Lobstr, Crawlers

client = Lobstr(api_key=os.getenv("LOBSTR_API_KEY"))

# Synchronous scrape (blocks until results are ready)
result = client.scrape(
    crawler_id=Crawlers.INSTAGRAM_PROFILE_SCRAPER,
    urls=["https://instagram.com/cristiano"],
    concurrency=1,
)

for row in result.data:
    print(row)
```

## API Reference

### High-level

#### `client.scrape(crawler_id, urls, *, sync=True, concurrency=1)`

All-in-one method. Creates a squid, adds URL tasks, starts a run, polls until
completion, downloads the CSV, and returns a `ScrapeResult`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `crawler_id` | `str` | required | Crawler hash ID (use `Crawlers` constants) |
| `urls` | `list[str]` | required | URLs to scrape |
| `sync` | `bool` | `True` | Block until done and return `ScrapeResult`, or return `Run` immediately |
| `concurrency` | `int` | `1` | Number of concurrent slots |

Returns `ScrapeResult` when `sync=True`, `Run` when `sync=False`.

### Low-level methods

These map 1:1 to the Lobstr REST API:

| Method | Endpoint | Returns |
|---|---|---|
| `list_squids(crawler_id=None)` | `GET /v1/squids` | `list[Squid]` |
| `create_squid(crawler_id)` | `POST /v1/squids` | `Squid` |
| `update_squid(squid_id, *, name, concurrency)` | `POST /v1/squids/{id}` | `Squid` |
| `delete_squid(squid_id)` | `DELETE /v1/squids/{id}` | `None` |
| `empty_squid(squid_id)` | `POST /v1/squids/{id}/empty` | `None` |
| `add_tasks(squid_id, urls)` | `POST /v1/tasks` | `dict` |
| `start_run(squid_id)` | `POST /v1/runs` | `Run` |
| `get_run_stats(run_id)` | `GET /v1/runs/{id}/stats` | `RunStats` |
| `abort_run(run_id)` | `POST /v1/runs/{id}/abort` | `dict` |
| `list_runs(squid_id)` | `GET /v1/runs?squid={id}` | `list[Run]` |
| `download_run(run_id)` | `GET /v1/runs/{id}/download` | `str` (S3 URL) |

### Models

- **`Squid`** — id, crawler, name, concurrency
- **`Run`** — id, squid, status, is_done, export_done, duration, total_results
- **`RunStats`** — is_done, duration, total_results, raw
- **`ScrapeResult`** — data (list of dicts), file_path (CSV), run (Run)
- **`Crawlers`** — constants for known crawler IDs

### Async (fire and forget)

```python
run = client.scrape(
    crawler_id=Crawlers.INSTAGRAM_PROFILE_SCRAPER,
    urls=["https://instagram.com/cristiano"],
    sync=False,
)
print(f"Run started: {run.id}")

# Check later
stats = client.get_run_stats(run.id)
print(stats.is_done)
```

### Using low-level methods directly

```python
client = Lobstr(api_key="...")

# Manage squids
squids = client.list_squids(crawler_id=Crawlers.INSTAGRAM_PROFILE_SCRAPER)
squid = client.create_squid(Crawlers.INSTAGRAM_PROFILE_SCRAPER)
client.update_squid(squid.id, name="My Scraper", concurrency=3)

# Add tasks and run
client.add_tasks(squid.id, ["https://instagram.com/cristiano"])
run = client.start_run(squid.id)
stats = client.get_run_stats(run.id)

# Download when ready
csv_url = client.download_run(run.id)

# Cleanup
client.delete_squid(squid.id)
```

## Known Crawler IDs

| Constant | Hash |
|---|---|
| `Crawlers.INSTAGRAM_PROFILE_SCRAPER` | `5c55ffbf10fbbc7ee63ebb2bcaa332b7` |

## License

MIT
